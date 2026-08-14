import asyncio
import json
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, status, BackgroundTasks
from app.models.schemas import LectureResponse, LectureListResponse, MessageResponse, TeamShareRequest
from app.middleware.auth_middleware import get_current_user
from app.services.supabase_client import get_supabase
from app.services.transcription_service import transcribe_audio
from app.services.document_extraction_service import extract_document_text
from app.services.analysis_service import generate_summary
from app.services.rag_service import process_lecture_for_rag
from app.services.organization_service import OrganizationService
from app.services.group_service import GroupService
from app.services.team_suggestion_service import TeamSuggestionService
from app.config import MAX_AUDIO_SIZE_MB, ALLOWED_MEDIA_TYPES
import uuid

router = APIRouter(prefix="/api/lectures", tags=["Lectures"])


DOC_EXTENSIONS = {"pdf", "docx", "pptx"}


async def _can_write_lecture_scope(org_id: Optional[str], group_id: Optional[str], user_id: str) -> bool:
    """
    Write permissions:
    - Personal (no org): allowed for the user.
    - Workspace-scoped (org with no group): owner only.
    - Team-scoped (org + group): owner or that team's admin.
    """
    if not org_id:
        return True

    org_role = await OrganizationService.get_role(org_id, user_id)
    if org_role == "owner":
        return True

    if not group_id:
        return False

    group_role = await GroupService.get_group_role(group_id, user_id)
    return group_role == "admin"


async def _can_access_lecture(lecture: dict, user_id: str) -> bool:
    """
    Access model:
    - Personal lecture (no org_id): only uploader can access.
    - Workspace lecture (org_id, no group_id): any workspace member can access.
    - Team lecture (org_id + group_id): team members and org owner can access.
    - Multi-team shared lecture: accessible to members of any shared team.
    """
    owner_id = lecture.get("user_id")
    org_id = lecture.get("org_id")
    group_id = lecture.get("group_id")
    lecture_id = lecture.get("id")

    # Personal content remains private to creator.
    if not org_id:
        return owner_id == user_id

    org_role = await OrganizationService.get_role(org_id, user_id)
    if not org_role:
        return False

    # Workspace-wide lecture is visible to all workspace members.
    if not group_id:
        return True

    # Workspace owner can always access team lectures.
    if org_role == "owner":
        return True

    # Members need explicit team membership for team-scoped lectures.
    group_role = await GroupService.get_group_role(group_id, user_id)
    if group_role:
        return True
    
    # Check if lecture is shared with any of user's teams via lecture_team_shares
    if lecture_id:
        supabase = get_supabase()
        user_group_ids = supabase.table("group_members") \
            .select("group_id") \
            .eq("user_id", user_id) \
            .execute()
        
        user_team_ids = {gm["group_id"] for gm in (user_group_ids.data or [])}
        
        if user_team_ids:
            shared_teams = supabase.table("lecture_team_shares") \
                .select("group_id") \
                .eq("lecture_id", lecture_id) \
                .in_("group_id", list(user_team_ids)) \
                .execute()
            
            if shared_teams.data:
                return True
    
    return False


async def _upload_and_process_lecture(
    lecture_id: str,
    user_id: str,
    file_content: bytes,
    content_type: str,
    file_ext: str,
):
    """
    Background task: upload raw file to storage, then run transcript/summary/RAG pipeline.
    This keeps /upload response fast and avoids long client waits.
    """
    supabase = get_supabase()
    storage_path = f"{user_id}/{lecture_id}.{file_ext or 'upload'}"

    try:
        storage_options = {}
        if content_type:
            storage_options["content-type"] = content_type

        supabase.storage.from_("lecture-audio").upload(
            path=storage_path,
            file=file_content,
            file_options=storage_options,
        )

        audio_url = supabase.storage.from_("lecture-audio").get_public_url(storage_path)
        supabase.table("lectures").update({"audio_url": audio_url}).eq("id", lecture_id).execute()

        await _process_lecture(lecture_id, audio_url, file_ext)
    except Exception as e:
        supabase.table("lectures").update({
            "status": "failed",
            "summary_text": f"Upload/processing failed: {str(e)}",
        }).eq("id", lecture_id).execute()


async def _process_lecture(lecture_id: str, audio_url: str, file_ext: str):
    """
    Background task: transcribe audio, generate summary, create RAG index.
    Updates lecture status through each stage.
    """
    supabase = get_supabase()
    is_document = (file_ext or "").lower().lstrip(".") in DOC_EXTENSIONS

    try:
        # Step 1: "Transcribe" stage.
        # - Audio/video: Deepgram diarization + timestamps
        # - Docs: extract text (no OCR) and treat it as transcript
        supabase.table("lectures").update({"status": "transcribing"}).eq("id", lecture_id).execute()
        if is_document:
            result = await extract_document_text(audio_url, file_ext)
            # For documents we only set `transcript_text` (no word-level timestamps)
            supabase.table("lectures").update({
                "transcript_text": result["transcript_text"],
                "transcript_json": None,
                "status": "summarizing",
            }).eq("id", lecture_id).execute()
        else:
            result = await transcribe_audio(audio_url)

            # Store transcript text + structured data (speakers, timestamps)
            supabase.table("lectures").update({
                "transcript_text": result["transcript_text"],
                "transcript_json": json.dumps({
                    "utterances": result["utterances"],
                    "speaker_labels": result["speaker_labels"],
                    "detected_language": result["detected_language"],
                    "duration_seconds": result["duration_seconds"],
                    "word_count": result["word_count"],
                }),
                "status": "summarizing",
            }).eq("id", lecture_id).execute()

        # Step 2: Summarize with Groq
        summary = await generate_summary(result["transcript_text"], "detailed")
        supabase.table("lectures").update({
            "summary_text": summary,
            "status": "processing_rag",
        }).eq("id", lecture_id).execute()

        # Step 3: RAG processing
        await process_lecture_for_rag(lecture_id, result["transcript_text"])
        supabase.table("lectures").update({"status": "completed"}).eq("id", lecture_id).execute()

    except Exception as e:
        supabase.table("lectures").update({
            "status": "failed",
            "summary_text": f"Processing failed: {str(e)}",
        }).eq("id", lecture_id).execute()


@router.post("/upload", response_model=LectureResponse)
async def upload_lecture(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    audio: UploadFile = File(...),
    org_id: Optional[str] = Form(None),
    group_id: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """Upload an audio/video file and start processing."""

    # Validate file type
    content_type = audio.content_type or ""
    file_ext = audio.filename.split(".")[-1].lower() if audio.filename and "." in audio.filename else ""
    is_document = file_ext in DOC_EXTENSIONS

    # Some browsers report doc uploads as `application/octet-stream`, so we accept docs by extension too.
    if content_type not in ALLOWED_MEDIA_TYPES and not is_document:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type: {content_type}. Allowed: {', '.join(ALLOWED_MEDIA_TYPES)}",
        )

    # Read and validate size
    file_content = await audio.read()
    file_size_mb = len(file_content) / (1024 * 1024)

    if file_size_mb > MAX_AUDIO_SIZE_MB:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large ({file_size_mb:.1f}MB). Maximum: {MAX_AUDIO_SIZE_MB}MB",
        )

    supabase = get_supabase()

    if org_id and group_id:
        group = await GroupService.get_group_by_id(group_id)
        if not group or group.get("org_id") != org_id:
            raise HTTPException(status_code=400, detail="Group does not belong to the selected workspace")

    allowed = await _can_write_lecture_scope(org_id, group_id, current_user["user_id"])
    if not allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions to upload in this scope")

    # Create lecture record
    lecture_result = supabase.table("lectures").insert({
        "user_id": current_user["user_id"],
        "title": title,
        "status": "uploading",
        "org_id": org_id,
        "group_id": group_id,
    }).execute()

    if not lecture_result.data:
        raise HTTPException(status_code=500, detail="Failed to create lecture record")

    lecture = lecture_result.data[0]
    lecture_id = lecture["id"]

    # Start background storage + processing pipeline
    background_tasks.add_task(
        _upload_and_process_lecture,
        lecture_id,
        current_user["user_id"],
        file_content,
        content_type,
        file_ext,
    )

    updated = supabase.table("lectures").select("*").eq("id", lecture_id).execute()
    lecture_data = updated.data[0] if updated.data else lecture

    return LectureResponse(**lecture_data)



@router.post("/upload-url")
async def get_upload_url(
    title: str = Form(...),
    filename: str = Form(...),
    org_id: Optional[str] = Form(None),
    group_id: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    supabase = get_supabase()

    if org_id and group_id:
        group = await GroupService.get_group_by_id(group_id)
        if not group or group.get("org_id") != org_id:
            raise HTTPException(status_code=400, detail="Group does not belong to the selected workspace")

    allowed = await _can_write_lecture_scope(org_id, group_id, current_user["user_id"])
    if not allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions to upload in this scope")

    # Step 1: Create lecture record
    lecture_result = supabase.table("lectures").insert({
        "user_id": current_user["user_id"],
        "title": title,
        "status": "uploading",
        "org_id": org_id,
        "group_id": group_id,
    }).execute()

    lecture = lecture_result.data[0]
    lecture_id = lecture["id"]

    # Step 2: Generate file path
    file_ext = filename.split(".")[-1]
    storage_path = f"{current_user['user_id']}/{lecture_id}.{file_ext}"

    # Step 3: Create signed upload URL
    signed = supabase.storage.from_("lecture-audio").create_signed_upload_url(storage_path)

    return {
        "upload_url": signed["signed_url"],
        "path": storage_path,
        "lecture_id": lecture_id,
    }

@router.post("/confirm-upload")
async def confirm_upload(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    lecture_id: str = Form(...),
    path: str = Form(...),
    org_id: Optional[str] = Form(None),
    group_id: Optional[str] = Form(None),
):
    supabase = get_supabase()

    if org_id and group_id:
        group = await GroupService.get_group_by_id(group_id)
        if not group or group.get("org_id") != org_id:
            raise HTTPException(status_code=400, detail="Group does not belong to the selected workspace")

    allowed = await _can_write_lecture_scope(org_id, group_id, current_user["user_id"])
    if not allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions to upload in this scope")

    # Get public URL
    audio_url = supabase.storage.from_("lecture-audio").get_public_url(path)

    # Update lecture
    update_data = {
        "audio_url": audio_url,
        "status": "transcribing",
    }
    if org_id:
        update_data["org_id"] = org_id
    if group_id:
        update_data["group_id"] = group_id
    
    supabase.table("lectures").update(update_data).eq("id", lecture_id).execute()

    # Start processing
    background_tasks.add_task(_process_lecture, lecture_id, audio_url, path.split(".")[-1])

    return {"message": "Upload confirmed, processing started"}

@router.get("", response_model=LectureListResponse)
async def list_lectures(
    org_id: Optional[str] = None,
    group_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """List lectures. If org_id/group_id provided, filter by them."""
    supabase = get_supabase()
    query = supabase.table("lectures").select("*")

    if group_id:
        group = await GroupService.get_group_by_id(group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        org_role = await OrganizationService.get_role(group["org_id"], current_user["user_id"])
        group_role = await GroupService.get_group_role(group_id, current_user["user_id"])
        if org_role != "owner" and not group_role:
            raise HTTPException(status_code=403, detail="Not authorized to view this team")

        # Team view should include:
        # 1) Lectures directly owned by this team (lectures.group_id == group_id)
        # 2) Lectures shared to this team via lecture_team_shares
        owned_result = (
            supabase.table("lectures")
            .select("*")
            .eq("group_id", group_id)
            .execute()
        )

        shared_result = (
            supabase.table("lecture_team_shares")
            .select("lecture_id")
            .eq("group_id", group_id)
            .execute()
        )

        shared_ids = [row["lecture_id"] for row in (shared_result.data or [])]
        shared_lectures_data = []
        if shared_ids:
            shared_lectures = (
                supabase.table("lectures")
                .select("*")
                .in_("id", shared_ids)
                .execute()
            )
            shared_lectures_data = shared_lectures.data or []

        # Merge without duplicates (same lecture might be both owned + shared).
        merged_by_id = {
            lecture["id"]: lecture
            for lecture in (owned_result.data or []) + shared_lectures_data
        }
        merged_lectures = list(merged_by_id.values())
        merged_lectures.sort(key=lambda l: l.get("created_at") or "", reverse=True)

        lectures = [LectureResponse(**l) for l in merged_lectures]
        return LectureListResponse(lectures=lectures)
    elif org_id:
        role = await OrganizationService.get_role(org_id, current_user["user_id"])
        if not role:
            raise HTTPException(status_code=403, detail="Not a member of this workspace")

        query = query.eq("org_id", org_id)
    else:
        query = query.eq("user_id", current_user["user_id"])

    result = query.order("created_at", desc=True).execute()
    lectures = [LectureResponse(**l) for l in (result.data or [])]
    return LectureListResponse(lectures=lectures)


@router.get("/{lecture_id}/suggest-teams")
async def suggest_teams_for_lecture(
    lecture_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Suggest teams to share a lecture with based on content similarity and membership.
    Returns ranked list of teams with relevance scores and reasons.
    """
    supabase = get_supabase()
    
    # Get lecture
    result = supabase.table("lectures") \
        .select("*") \
        .eq("id", lecture_id) \
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Lecture not found")
    
    lecture = result.data[0]
    
    # Check access
    can_access = await _can_access_lecture(lecture, current_user["user_id"])
    if not can_access:
        raise HTTPException(status_code=404, detail="Lecture not found")
    
    # Must have org_id to suggest teams
    org_id = lecture.get("org_id")
    if not org_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot suggest teams for personal lectures"
        )

    org_role = await OrganizationService.get_role(org_id, current_user["user_id"])
    if not org_role:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    
    # Get transcript
    transcript = lecture.get("transcript_text", "")
    if not transcript:
        raise HTTPException(
            status_code=400,
            detail="Lecture transcription not yet complete"
        )
    
    # Get suggestions
    suggested_teams = await TeamSuggestionService.suggest_teams(
        org_id=org_id,
        user_id=current_user["user_id"],
        transcript_text=transcript,
        limit=5,
    )

    # Owners/admins can evaluate all teams for manual override.
    # Members only see teams they belong to.
    if org_role == "member":
        allowed_team_ids = {
            g["id"]
            for g in (await GroupService.get_groups_for_user(org_id, current_user["user_id"]))
        }
        suggested_teams = [t for t in suggested_teams if t.get("id") in allowed_team_ids]

    # Build explicit eligible team list for the selector UI.
    if org_role in ["owner", "admin"]:
        eligible_teams = await GroupService.get_groups_for_org(org_id)
    else:
        eligible_teams = await GroupService.get_groups_for_user(org_id, current_user["user_id"])
    
    return {
        "lecture_id": lecture_id,
        "suggested_teams": suggested_teams,
        "eligible_teams": [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "description": t.get("description"),
            }
            for t in (eligible_teams or [])
        ],
    }


@router.put("/{lecture_id}/share-teams")
async def update_lecture_team_shares(
    lecture_id: str,
    request: TeamShareRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Update which teams this lecture is shared with.
    Accepts a list of team IDs and updates the lecture_team_shares table.
    Only workspace owner can manage sharing.
    """
    supabase = get_supabase()
    team_ids = request.team_ids
    
    # Get lecture
    result = supabase.table("lectures") \
        .select("*") \
        .eq("id", lecture_id) \
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Lecture not found")
    
    lecture = result.data[0]
    org_id = lecture.get("org_id")
    user_id = current_user["user_id"]
    
    if not org_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot share personal lectures to teams"
        )
    
    # Check permissions (workspace owner/admin)
    org_role = await OrganizationService.get_role(org_id, user_id)
    if org_role not in ["owner", "admin"]:
        raise HTTPException(status_code=403, detail="Only workspace owner or admin can manage lecture sharing")
    
    # Validate all teams belong to this org
    teams_result = supabase.table("groups") \
        .select("id") \
        .eq("org_id", org_id) \
        .in_("id", team_ids) \
        .execute()
    
    valid_team_ids = {t["id"] for t in (teams_result.data or [])}
    invalid_teams = set(team_ids) - valid_team_ids
    
    if invalid_teams:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid teams: {', '.join(invalid_teams)}"
        )
    
    # Clear existing shares and add new ones
    supabase.table("lecture_team_shares").delete().eq("lecture_id", lecture_id).execute()
    
    for team_id in team_ids:
        supabase.table("lecture_team_shares").insert({
            "lecture_id": lecture_id,
            "group_id": team_id,
        }).execute()
    
    return {
        "lecture_id": lecture_id,
        "shared_with_teams": team_ids,
        "message": f"Lecture shared with {len(team_ids)} team(s)",
    }


@router.get("/{lecture_id}", response_model=LectureResponse)
async def get_lecture(lecture_id: str, current_user: dict = Depends(get_current_user)):
    """Get a single lecture by ID."""
    supabase = get_supabase()
    result = (
        supabase.table("lectures")
        .select("*")
        .eq("id", lecture_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Lecture not found")

    lecture = result.data[0]
    can_access = await _can_access_lecture(lecture, current_user["user_id"])
    if not can_access:
        raise HTTPException(status_code=404, detail="Lecture not found")

    return LectureResponse(**lecture)


@router.delete("/{lecture_id}", response_model=MessageResponse)
async def delete_lecture(lecture_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a lecture and its associated data."""
    supabase = get_supabase()

    result = (
        supabase.table("lectures")
        .select("id, audio_url, user_id, org_id, group_id")
        .eq("id", lecture_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Lecture not found")

    lecture = result.data[0]
    org_id = lecture.get("org_id")
    group_id = lecture.get("group_id")
    user_id = current_user["user_id"]

    # Personal lecture: only creator can delete.
    if not org_id:
        if lecture.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    else:
        # Workspace lecture (no group): only workspace owner can delete.
        # Team lecture: workspace owner or team admin can delete.
        org_role = await OrganizationService.get_role(org_id, user_id)
        group_role = await GroupService.get_group_role(group_id, user_id) if group_id else None

        if not group_id:
            if org_role != "owner":
                raise HTTPException(status_code=403, detail="Only workspace owner can delete this knowledge item")
        else:
            if org_role != "owner" and group_role != "admin":
                raise HTTPException(status_code=403, detail="Only team admin or workspace owner can delete this knowledge item")

    # Delete chunks
    supabase.table("lecture_chunks").delete().eq("lecture_id", lecture_id).execute()
    # Delete lecture
    supabase.table("lectures").delete().eq("id", lecture_id).execute()

    # Try delete from storage
    try:
        audio_url = lecture.get("audio_url", "")
        if audio_url and "/lecture-audio/" in audio_url:
            path_part = audio_url.split("/lecture-audio/")[-1]
            if path_part:
                supabase.storage.from_("lecture-audio").remove([path_part])
    except Exception:
        pass

    return MessageResponse(message="Lecture deleted successfully")
