from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel
from typing import Optional, Any
from app.middleware.auth_middleware import get_current_user
from app.services.supabase_client import get_supabase
from app.services.organization_service import OrganizationService
from app.services.group_service import GroupService
from app.services.analysis_service import (
    generate_summary,
    generate_notes,
    extract_keywords,
    generate_questions,
    segment_topics,
    detect_highlights,
    translate_content,
    generate_lecture_action_plan,
    build_action_plan_sections,
    aggregate_workspace_action_plan,
)

router = APIRouter(prefix="/api/analysis", tags=["Analysis"])


class AnalysisRequest(BaseModel):
    lecture_id: str
    format_type: str = "detailed"


class TranslateRequest(BaseModel):
    lecture_id: str
    content: str
    target_language: str


class AnalysisResponse(BaseModel):
    content: str
    analysis_type: str
    cached: bool = False


class ActionPlanRequest(BaseModel):
    lecture_id: str
    force_refresh: bool = False


class WorkspaceActionPlanRequest(BaseModel):
    org_id: str
    group_id: Optional[str] = None
    force_refresh: bool = False


class ActionPlanResponse(BaseModel):
    content: str
    content_json: dict[str, Any]
    cached: bool = False


class ActionPlanSectionResponse(BaseModel):
    content: str
    content_json: Any
    cached: bool = False


async def _can_access_lecture(lecture: dict, user_id: str) -> bool:
    """
    Access model:
    - Personal lecture (no org_id): only uploader can access.
    - Workspace lecture (org_id, no group_id): any workspace member can access.
    - Team lecture (org_id + group_id): team members and org owner can access.
    - Multi-team shared lecture: accessible to members of shared teams.
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

    # Org owner can always access team lectures.
    if org_role == "owner":
        return True

    # Members need explicit team membership for team-scoped lectures.
    group_role = await GroupService.get_group_role(group_id, user_id)
    if group_role:
        return True

    # Also allow access if lecture is shared to one of user's teams.
    if lecture_id:
        supabase = get_supabase()
        user_groups = (
            supabase.table("group_members")
            .select("group_id")
            .eq("user_id", user_id)
            .execute()
        )
        team_ids = [g["group_id"] for g in (user_groups.data or [])]
        if team_ids:
            shared = (
                supabase.table("lecture_team_shares")
                .select("group_id")
                .eq("lecture_id", lecture_id)
                .in_("group_id", team_ids)
                .execute()
            )
            if shared.data:
                return True

    return False


async def _get_transcript(lecture_id: str, user_id: str) -> str:
    """Get transcript for a lecture, verify access."""
    supabase = get_supabase()
    result = (
        supabase.table("lectures")
        .select("id, transcript_text, summary_text, user_id, status, org_id, group_id")
        .eq("id", lecture_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    lecture = result.data[0]
    can_access = await _can_access_lecture(lecture, user_id)
    if not can_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if not lecture.get("transcript_text"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transcript not yet available.",
        )

    return lecture["transcript_text"]


async def _get_lecture_for_access(lecture_id: str, user_id: str) -> dict:
    supabase = get_supabase()
    result = (
        supabase.table("lectures")
        .select("id, transcript_text, summary_text, user_id, status, org_id, group_id")
        .eq("id", lecture_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    lecture = result.data[0]
    can_access = await _can_access_lecture(lecture, user_id)
    if not can_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    return lecture


def _get_cached(lecture_id: str, analysis_type: str) -> Optional[str]:
    """Check if analysis is already cached in the database."""
    supabase = get_supabase()
    result = (
        supabase.table("lecture_analysis")
        .select("content")
        .eq("lecture_id", lecture_id)
        .eq("analysis_type", analysis_type)
        .execute()
    )
    if result.data:
        return result.data[0]["content"]
    return None


def _save_cache(lecture_id: str, analysis_type: str, content: str):
    """Save analysis result to database cache."""
    supabase = get_supabase()
    try:
        supabase.table("lecture_analysis").upsert({
            "lecture_id": lecture_id,
            "analysis_type": analysis_type,
            "content": content,
        }, on_conflict="lecture_id,analysis_type").execute()
    except Exception:
        pass  # Non-critical, silently fail


def _get_cached_action_plan(lecture_id: str) -> Optional[dict]:
    supabase = get_supabase()
    result = (
        supabase.table("lecture_action_plans")
        .select("*")
        .eq("lecture_id", lecture_id)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def _save_action_plan(lecture_id: str, markdown: str, content_json: dict):
    sections = build_action_plan_sections(content_json, markdown)
    supabase = get_supabase()

    share_rows = (
        supabase.table("lecture_team_shares")
        .select("group_id")
        .eq("lecture_id", lecture_id)
        .execute()
    )
    share_team_ids = [r["group_id"] for r in (share_rows.data or []) if r.get("group_id")]

    full_payload = {
        "lecture_id": lecture_id,
        "markdown_content": markdown,
        "content_json": content_json,
        "tasks_json": sections["tasks"]["content_json"],
        "timeline_json": sections["timeline"]["content_json"],
        "dependencies_json": sections["dependencies"]["content_json"],
        "team_breakdown_json": sections["team_breakdown"]["content_json"],
        "share_team_ids_json": share_team_ids,
        "is_shared": len(share_team_ids) > 0,
    }

    # Some environments have stale PostgREST schema cache after migration.
    # Fall back to minimal columns so generation still succeeds.
    try:
        supabase.table("lecture_action_plans").upsert(full_payload, on_conflict="lecture_id").execute()
    except Exception:
        minimal_payload = {
            "lecture_id": lecture_id,
            "markdown_content": markdown,
            "content_json": content_json,
        }
        supabase.table("lecture_action_plans").upsert(minimal_payload, on_conflict="lecture_id").execute()


async def _ensure_action_plan(lecture_id: str, user_id: str, force_refresh: bool = False) -> tuple[dict, bool]:
    lecture = await _get_lecture_for_access(lecture_id, user_id)

    cached = _get_cached_action_plan(lecture_id)
    if cached and not force_refresh:
        return cached, True

    transcript = lecture.get("transcript_text")
    if not transcript:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transcript not yet available.")

    summary = lecture.get("summary_text") or _get_cached(lecture_id, "summary_detailed") or ""
    highlights = _get_cached(lecture_id, "highlights") or ""

    workspace_teams = []
    org_id = lecture.get("org_id")
    if org_id:
        try:
            workspace_teams = await GroupService.get_groups_for_org(org_id)
        except Exception:
            workspace_teams = []

    markdown, content_json = await generate_lecture_action_plan(
        transcript,
        summary,
        highlights,
        workspace_teams=workspace_teams,
    )

    # Try cache persistence, but do not block response if DB schema/cache is stale.
    try:
        _save_action_plan(lecture_id, markdown, content_json)
        latest = _get_cached_action_plan(lecture_id)
        if latest:
            return latest, False
    except Exception:
        pass

    # Direct Groq output fallback (non-cached) so feature still works.
    sections = build_action_plan_sections(content_json, markdown)
    return {
        "lecture_id": lecture_id,
        "markdown_content": markdown,
        "content_json": content_json,
        "tasks_json": sections["tasks"]["content_json"],
        "timeline_json": sections["timeline"]["content_json"],
        "dependencies_json": sections["dependencies"]["content_json"],
        "team_breakdown_json": sections["team_breakdown"]["content_json"],
    }, False


@router.post("/summary", response_model=AnalysisResponse)
async def get_summary(req: AnalysisRequest, current_user: dict = Depends(get_current_user)):
    """Generate summary in specified format (cached)."""
    cache_key = f"summary_{req.format_type}"
    cached = _get_cached(req.lecture_id, cache_key)
    if cached:
        return AnalysisResponse(content=cached, analysis_type=cache_key, cached=True)

    transcript = await _get_transcript(req.lecture_id, current_user["user_id"])
    content = await generate_summary(transcript, req.format_type)
    _save_cache(req.lecture_id, cache_key, content)
    return AnalysisResponse(content=content, analysis_type=cache_key)


@router.post("/notes", response_model=AnalysisResponse)
async def get_notes(req: AnalysisRequest, current_user: dict = Depends(get_current_user)):
    """Generate structured auto-notes (cached)."""
    cached = _get_cached(req.lecture_id, "notes")
    if cached:
        return AnalysisResponse(content=cached, analysis_type="notes", cached=True)

    transcript = await _get_transcript(req.lecture_id, current_user["user_id"])
    content = await generate_notes(transcript)
    _save_cache(req.lecture_id, "notes", content)
    return AnalysisResponse(content=content, analysis_type="notes")


@router.post("/keywords", response_model=AnalysisResponse)
async def get_keywords(req: AnalysisRequest, current_user: dict = Depends(get_current_user)):
    """Extract keywords, technical terms, glossary (cached)."""
    cached = _get_cached(req.lecture_id, "keywords")
    if cached:
        return AnalysisResponse(content=cached, analysis_type="keywords", cached=True)

    transcript = await _get_transcript(req.lecture_id, current_user["user_id"])
    content = await extract_keywords(transcript)
    _save_cache(req.lecture_id, "keywords", content)
    return AnalysisResponse(content=content, analysis_type="keywords")


@router.post("/questions", response_model=AnalysisResponse)
async def get_questions(req: AnalysisRequest, current_user: dict = Depends(get_current_user)):
    """Generate questions (cached per type)."""
    cache_key = f"questions_{req.format_type}"
    cached = _get_cached(req.lecture_id, cache_key)
    if cached:
        return AnalysisResponse(content=cached, analysis_type=cache_key, cached=True)

    transcript = await _get_transcript(req.lecture_id, current_user["user_id"])
    content = await generate_questions(transcript, req.format_type)
    _save_cache(req.lecture_id, cache_key, content)
    return AnalysisResponse(content=content, analysis_type=cache_key)


@router.post("/topics", response_model=AnalysisResponse)
async def get_topics(req: AnalysisRequest, current_user: dict = Depends(get_current_user)):
    """Segment lecture into topics/chapters (cached)."""
    cached = _get_cached(req.lecture_id, "topics")
    if cached:
        return AnalysisResponse(content=cached, analysis_type="topics", cached=True)

    transcript = await _get_transcript(req.lecture_id, current_user["user_id"])
    content = await segment_topics(transcript)
    _save_cache(req.lecture_id, "topics", content)
    return AnalysisResponse(content=content, analysis_type="topics")


@router.post("/highlights", response_model=AnalysisResponse)
async def get_highlights(req: AnalysisRequest, current_user: dict = Depends(get_current_user)):
    """Detect important highlights (cached)."""
    cached = _get_cached(req.lecture_id, "highlights")
    if cached:
        return AnalysisResponse(content=cached, analysis_type="highlights", cached=True)

    transcript = await _get_transcript(req.lecture_id, current_user["user_id"])
    content = await detect_highlights(transcript)
    _save_cache(req.lecture_id, "highlights", content)
    return AnalysisResponse(content=content, analysis_type="highlights")


@router.post("/translate", response_model=AnalysisResponse)
async def translate(req: TranslateRequest, current_user: dict = Depends(get_current_user)):
    """Translate analysis content to another language (cached in DB)."""
    # Verify lecture access
    supabase = get_supabase()
    result = (
        supabase.table("lectures")
        .select("user_id, org_id, group_id")
        .eq("id", req.lecture_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")
    
    lecture = result.data[0]
    can_access = await _can_access_lecture(lecture, current_user["user_id"])
    if not can_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    # Build a stable cache key from content hash + language
    import hashlib
    content_hash = hashlib.md5(req.content.encode()).hexdigest()[:12]
    cache_key = f"translate_{req.target_language}_{content_hash}"

    # Check DB cache
    cached = _get_cached(req.lecture_id, cache_key)
    if cached:
        return AnalysisResponse(content=cached, analysis_type=cache_key, cached=True)

    # Generate translation
    content = await translate_content(req.content, req.target_language)

    # Save to DB
    _save_cache(req.lecture_id, cache_key, content)

    return AnalysisResponse(content=content, analysis_type=cache_key)


@router.post("/action-plan", response_model=ActionPlanResponse)
async def get_action_plan(req: ActionPlanRequest, current_user: dict = Depends(get_current_user)):
    row, cached = await _ensure_action_plan(req.lecture_id, current_user["user_id"], req.force_refresh)
    return ActionPlanResponse(
        content=row.get("markdown_content") or "",
        content_json=row.get("content_json") or {},
        cached=cached,
    )


async def _get_action_plan_section(
    req: ActionPlanRequest,
    current_user: dict,
    section: str,
) -> ActionPlanSectionResponse:
    row, cached = await _ensure_action_plan(req.lecture_id, current_user["user_id"], req.force_refresh)

    sections = build_action_plan_sections(row.get("content_json") or {}, row.get("markdown_content") or "")
    content = sections[section]["content"] if section in sections else ""

    # Prefer precomputed DB columns when available, else derive from content_json.
    if section == "tasks":
        content_json = row.get("tasks_json") if row.get("tasks_json") is not None else sections["tasks"]["content_json"]
    elif section == "timeline":
        content_json = row.get("timeline_json") if row.get("timeline_json") is not None else sections["timeline"]["content_json"]
    elif section == "dependencies":
        content_json = row.get("dependencies_json") if row.get("dependencies_json") is not None else sections["dependencies"]["content_json"]
    elif section == "team_breakdown":
        content_json = row.get("team_breakdown_json") if row.get("team_breakdown_json") is not None else sections["team_breakdown"]["content_json"]
    elif section == "markdown":
        content_json = row.get("content_json") or {}
    else:
        raise HTTPException(status_code=400, detail="Invalid action plan section")

    return ActionPlanSectionResponse(content=content, content_json=content_json, cached=cached)


@router.post("/action-plan/tasks", response_model=ActionPlanSectionResponse)
async def get_action_plan_tasks(req: ActionPlanRequest, current_user: dict = Depends(get_current_user)):
    return await _get_action_plan_section(req, current_user, "tasks")


@router.post("/action-plan/timeline", response_model=ActionPlanSectionResponse)
async def get_action_plan_timeline(req: ActionPlanRequest, current_user: dict = Depends(get_current_user)):
    return await _get_action_plan_section(req, current_user, "timeline")


@router.post("/action-plan/dependencies", response_model=ActionPlanSectionResponse)
async def get_action_plan_dependencies(req: ActionPlanRequest, current_user: dict = Depends(get_current_user)):
    return await _get_action_plan_section(req, current_user, "dependencies")


@router.post("/action-plan/team-breakdown", response_model=ActionPlanSectionResponse)
async def get_action_plan_team_breakdown(req: ActionPlanRequest, current_user: dict = Depends(get_current_user)):
    return await _get_action_plan_section(req, current_user, "team_breakdown")


@router.post("/action-plan/markdown", response_model=ActionPlanSectionResponse)
async def get_action_plan_markdown(req: ActionPlanRequest, current_user: dict = Depends(get_current_user)):
    return await _get_action_plan_section(req, current_user, "markdown")


@router.post("/workspace-action-plan", response_model=ActionPlanResponse)
async def get_workspace_action_plan(req: WorkspaceActionPlanRequest, current_user: dict = Depends(get_current_user)):
    role = await OrganizationService.get_role(req.org_id, current_user["user_id"])
    if not role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this workspace")

    supabase = get_supabase()

    cache_query = (
        supabase.table("workspace_action_plans")
        .select("*")
        .eq("org_id", req.org_id)
    )
    if req.group_id:
        cache_query = cache_query.eq("group_id", req.group_id)
    else:
        cache_query = cache_query.is_("group_id", "null")

    cache_result = cache_query.execute()
    if cache_result.data and not req.force_refresh:
        row = cache_result.data[0]
        return ActionPlanResponse(
            content=row.get("markdown_content") or "",
            content_json=row.get("content_json") or {},
            cached=True,
        )

    lectures_query = (
        supabase.table("lectures")
        .select("id, org_id, group_id, user_id")
        .eq("org_id", req.org_id)
    )
    if req.group_id:
        lectures_query = lectures_query.eq("group_id", req.group_id)
    lectures_result = lectures_query.execute()

    accessible_lectures = []
    for lecture in (lectures_result.data or []):
        if await _can_access_lecture(lecture, current_user["user_id"]):
            accessible_lectures.append(lecture)

    lecture_plans = []
    for lecture in accessible_lectures:
        row, _ = await _ensure_action_plan(lecture["id"], current_user["user_id"], False)
        lecture_plans.append(row.get("content_json") or {})

    markdown, content_json = aggregate_workspace_action_plan(lecture_plans)
    sections = build_action_plan_sections(content_json, markdown)

    upsert_payload = {
        "org_id": req.org_id,
        "group_id": req.group_id,
        "markdown_content": markdown,
        "content_json": content_json,
        "tasks_json": sections["tasks"]["content_json"],
        "timeline_json": sections["timeline"]["content_json"],
        "dependencies_json": sections["dependencies"]["content_json"],
        "team_breakdown_json": sections["team_breakdown"]["content_json"],
        "risks_json": content_json.get("risks", []),
    }

    delete_query = supabase.table("workspace_action_plans").delete().eq("org_id", req.org_id)
    if req.group_id:
        delete_query = delete_query.eq("group_id", req.group_id)
    else:
        delete_query = delete_query.is_("group_id", "null")
    delete_query.execute()

    try:
        supabase.table("workspace_action_plans").insert(upsert_payload).execute()
    except Exception:
        # Stale schema cache fallback: store minimal payload and still return response.
        supabase.table("workspace_action_plans").insert({
            "org_id": req.org_id,
            "group_id": req.group_id,
            "markdown_content": markdown,
            "content_json": content_json,
        }).execute()

    return ActionPlanResponse(content=markdown, content_json=content_json, cached=False)


