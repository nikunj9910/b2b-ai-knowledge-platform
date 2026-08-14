"""
Team suggestion service: Suggests teams to share a lecture with based on content similarity.
"""

from typing import List
import re
from app.services.supabase_client import get_supabase
from app.services.rag_service import _cohere_embed


class TeamSuggestionService:
    """Intelligently suggests teams to share a lecture with."""

    @staticmethod
    async def suggest_teams(
        org_id: str,
        user_id: str,
        transcript_text: str,
        limit: int = 5,
    ) -> List[dict]:
        """
        Suggest teams to share a lecture with, based on:
        1. User's existing membership (highest priority)
        2. Content similarity to existing lectures in teams
        3. Team member overlap with user's other teams
        
        Returns list of teams ranked by relevance (highest first).
        Each team dict has: {id, name, description, score, reason}
        """
        supabase = get_supabase()
        
        # Get all teams in the organization
        teams_result = supabase.table("groups") \
            .select("*") \
            .eq("org_id", org_id) \
            .execute()
        
        if not teams_result.data:
            return []
        
        teams = teams_result.data
        
        # Get teams user is member of
        user_team_memberships = supabase.table("group_members") \
            .select("group_id, role") \
            .eq("user_id", user_id) \
            .execute()
        
        user_team_ids = {
            m["group_id"] for m in (user_team_memberships.data or [])
        }
        
        # Score each team
        scored_teams = []
        
        for team in teams:
            membership_score = 0
            content_score = 0
            overlap_score = 0
            reasons = []
            team_id = team["id"]
            
            # 1. User is already a member (high priority)
            if team_id in user_team_ids:
                membership_score = 100
                reasons.append("You're already a member")
            
            # 2. Content similarity via transcript embedding
            try:
                content_score = await TeamSuggestionService._score_content_similarity(
                    team_id, transcript_text
                )
            except Exception:
                # If embedding fails, skip embedding-based scoring.
                content_score = 0

            # 2b. Fallback relevance from team metadata (name/description).
            # Helps when teams are new and have little/no historical lecture chunks.
            metadata_score = TeamSuggestionService._score_team_metadata_relevance(
                team.get("name", ""),
                team.get("description", ""),
                transcript_text,
            )
            content_score = min(100, content_score + metadata_score)

            if content_score >= 10:
                    reasons.append("Similar content in team")
            
            # 3. Member overlap (colleagues are in this team)
            if team_id not in user_team_ids:
                overlap_score = await TeamSuggestionService._score_member_overlap(
                    user_id, team_id
                )
                if overlap_score >= 10:
                    reasons.append(f"Shared with {max(1, overlap_score // 10)} colleagues")

            total_score = max(0, min(100, membership_score + content_score + overlap_score))
            membership_score = max(0, min(100, membership_score))
            content_score = max(0, min(100, content_score))
            overlap_score = max(0, min(100, overlap_score))
            
            scored_teams.append({
                "id": team_id,
                "name": team["name"],
                "description": team.get("description"),
                "score": total_score,
                "membership_score": membership_score,
                "content_score": content_score,
                "overlap_score": overlap_score,
                "reason": " • ".join(reasons) if reasons else "Relevant team"
            })
        
        # Sort by score descending
        scored_teams.sort(key=lambda x: x["score"], reverse=True)
        
        return scored_teams[:limit]
    
    @staticmethod
    async def _score_content_similarity(
        team_id: str,
        transcript_text: str,
        top_k: int = 3
    ) -> int:
        """
        Score content similarity by finding lectures in the team with similar content.
        Uses embeddings and semantic search.
        """
        try:
            # Chunk and embed the new transcript
            new_embedding = await _cohere_embed([transcript_text], input_type="search_query")
            if not new_embedding:
                return 0
            
            new_vec = new_embedding[0]
            
            supabase = get_supabase()
            
            # Get lecture chunks from this team's lectures
            team_lectures = supabase.table("lectures") \
                .select("id") \
                .eq("group_id", team_id) \
                .execute()
            
            if not team_lectures.data:
                return 0
            
            team_lecture_ids = [lec["id"] for lec in team_lectures.data]
            if not team_lecture_ids:
                return 0
            
            # Find similar chunks in this team
            similarity_scores = []
            for lecture_id in team_lecture_ids:
                chunks = supabase.table("lecture_chunks") \
                    .select("embedding, chunk_text") \
                    .eq("lecture_id", lecture_id) \
                    .limit(5) \
                    .execute()
                
                if chunks.data:
                    for chunk in chunks.data:
                        embedding = chunk.get("embedding")
                        if embedding:
                            # Cosine similarity
                            score = sum(a * b for a, b in zip(new_vec, embedding))
                            similarity_scores.append(score)
            
            # Average the top similarities
            if similarity_scores:
                top_similarities = sorted(similarity_scores, reverse=True)[:top_k]
                avg_similarity = sum(top_similarities) / len(top_similarities)
                # Scale to 0-100
                return int(max(0, min(100, avg_similarity * 100)))
            
            return 0
        
        except Exception as e:
            # If similarity calculation fails, return 0 (no boost)
            return 0
    
    @staticmethod
    async def _score_member_overlap(
        user_id: str,
        team_id: str
    ) -> int:
        """
        Score based on how many colleagues are in this team.
        Returns 0-30 points.
        """
        try:
            supabase = get_supabase()
            
            # Get users in team
            team_members = supabase.table("group_members") \
                .select("user_id") \
                .eq("group_id", team_id) \
                .execute()
            
            team_member_ids = {m["user_id"] for m in (team_members.data or [])}
            
            # Get user's teams to find colleagues
            user_teams = supabase.table("group_members") \
                .select("group_id") \
                .eq("user_id", user_id) \
                .execute()
            
            user_team_ids = {t["group_id"] for t in (user_teams.data or [])}
            
            # Find colleagues (users in user's teams)
            colleagues = set()
            for user_team_id in user_team_ids:
                team_mates = supabase.table("group_members") \
                    .select("user_id") \
                    .eq("group_id", user_team_id) \
                    .execute()
                
                for mate in (team_mates.data or []):
                    colleagues.add(mate["user_id"])
            
            # Remove self
            colleagues.discard(user_id)
            
            # Count overlap
            overlap = len(colleagues & team_member_ids)
            
            # Scale to 0-30
            return min(30, overlap * 10)
        
        except Exception as e:
            return 0

    @staticmethod
    def _score_team_metadata_relevance(team_name: str, team_description: str, transcript_text: str) -> int:
        """
        Lightweight lexical fallback for content relevance.
        Scores overlap between transcript keywords and team metadata.
        Returns 0-30 points.
        """
        combined_meta = f"{team_name or ''} {team_description or ''}".lower()
        transcript = (transcript_text or "").lower()

        if not combined_meta.strip() or not transcript.strip():
            return 0

        meta_tokens = {
            t for t in re.findall(r"[a-zA-Z0-9]+", combined_meta)
            if len(t) >= 3
        }
        transcript_tokens = {
            t for t in re.findall(r"[a-zA-Z0-9]+", transcript)
            if len(t) >= 3
        }

        if not meta_tokens or not transcript_tokens:
            return 0

        overlap = len(meta_tokens & transcript_tokens)
        # 1 match -> 8, 2 -> 16, 3 -> 24, 4+ -> 30 (cap)
        return min(30, overlap * 8)
