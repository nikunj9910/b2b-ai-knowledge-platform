import httpx
from app.config import GROQ_API_KEY
import asyncio
import tiktoken
import json
import re
from datetime import datetime, timedelta

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

# --------------------------
# Utility functions for summarization and analysis
# --------------------------
def chunk_text(text: str, max_tokens: int = 1200, overlap: int = 200):
    encoder = tiktoken.get_encoding("cl100k_base")
    tokens = encoder.encode(text)

    chunks = []
    start = 0

    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk = encoder.decode(tokens[start:end])
        chunks.append(chunk)
        start += max_tokens - overlap

    return chunks


semaphore = asyncio.Semaphore(3)


async def summarize_chunk(chunk: str, prompt: str):
    async with semaphore:
        return await safe_groq_call(
            "You are a concise academic summarizer.",
            f"{prompt}\n\nTEXT:\n{chunk}",
            max_tokens=1024
        )


async def summarize_chunks(chunks: list[str], prompt: str):
    tasks = [summarize_chunk(c, prompt) for c in chunks]
    return await asyncio.gather(*tasks)


async def merge_summaries(partials: list[str], prompt: str) -> str:
    combined = "\n\n".join(partials)

    result = await safe_groq_call(
        "You are an expert academic summarizer.",
        f"{prompt}\n\nCONTENT:\n{combined}",
        max_tokens=2048
    )
    return result or ""

# ──────────────────────────────────────
# 0. Groq API Wrapper
# ─────────────────────────────────────

async def safe_groq_call(system, user, max_tokens=2048, retries=3):
    delay = 1

    for attempt in range(retries):
        try:
            return await _call_groq(system, user, max_tokens)
        except Exception as e:
            if attempt == retries - 1:
                raise e  # final fail

            await asyncio.sleep(delay)
            delay *= 2  # exponential backoff

async def _call_groq(system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
    """Generic Groq API call."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Groq API error: {response.status_code} - {response.text}")
        return response.json()["choices"][0]["message"]["content"]


# ──────────────────────────────────────
# 1. MULTIPLE SUMMARY FORMATS
# ──────────────────────────────────────

async def generate_summary(transcript: str, format_type: str = "detailed") -> str:
    prompts = {
        "short": "Summarize briefly in 3-5 sentences.",
        "bullet": "Summarize in bullet points.",
        "detailed": "Create a structured academic summary with headings.",
        "exam": "Create exam-focused notes.",
        "concept": "Create a concept-based summary.",
    }

    prompt = prompts.get(format_type, prompts["detailed"])

    # STEP 1: chunk
    chunks = chunk_text(transcript)

    # STEP 2: parallel summaries
    partials = await summarize_chunks(chunks, prompt)

    # STEP 3: final merge
    final = await merge_summaries([p for p in partials if p is not None], prompt)

    return final or ""


# ──────────────────────────────────────
# 2. AUTO NOTES GENERATOR
# ──────────────────────────────────────

async def generate_notes(transcript: str) -> str:
    """Transform transcript into clean structured notes."""
    system = "You are an expert note-taker creating clean, organized lecture notes from a transcript."
    user = f"""Transform this lecture transcript into clean, well-organized notes:

Requirements:
1. Fix all grammar and make sentences clean
2. Create clear headings and subheadings (use ## and ###)
3. **Bold** key points and important statements
4. Extract and highlight definitions using > blockquotes
5. Use bullet points for lists
6. Mark important statements with ⚠️
7. Add section dividers between topics
8. Make it easy to scan and study from

TRANSCRIPT:
{transcript}"""
    return await safe_groq_call(system, user) or ""


# ──────────────────────────────────────
# 3. KEYWORD & CONCEPT EXTRACTION
# ──────────────────────────────────────

async def extract_keywords(transcript: str) -> str:
    """Extract keywords, technical terms, glossary, and topic clusters."""
    system = "You are an expert at analyzing academic content and extracting key information."
    user = f"""Analyze this lecture transcript and extract:

## 🔑 Important Keywords
(list top 15-20 keywords with brief context)

## 🔬 Technical Terms
(list all technical/specialized terms with definitions)

## 📖 Glossary
(alphabetically ordered term: definition pairs)

## 🗂️ Topic Clusters
(group related concepts into clusters with labels)

TRANSCRIPT:
{transcript}"""
    return await safe_groq_call(system, user) or ""


# ──────────────────────────────────────
# 4. Q&A GENERATOR
# ──────────────────────────────────────

async def generate_questions(transcript: str, qtype: str = "mixed") -> str:
    """Generate questions from lecture content."""
    prompts = {
        "mcq": """Generate 10 Multiple Choice Questions from this lecture.
Format each as:
### Q1. [Question]
- A) [Option]
- B) [Option]
- C) [Option]
- D) [Option]
**Answer: [Letter]) [Explanation]**""",

        "short": """Generate 10 Short Answer Questions from this lecture.
Format each as:
### Q1. [Question]
**Answer:** [2-3 sentence answer]""",

        "long": """Generate 5 Long Answer Questions from this lecture.
Format each as:
### Q1. [Question]
**Model Answer:** [Detailed 1-2 paragraph answer]""",

        "flashcards": """Generate 15 Flashcards from this lecture.
Format each as:
### Card 1
**Front:** [Question/Term]
**Back:** [Answer/Definition]
---""",

        "mixed": """Generate a practice test from this lecture content:

## Section A: Multiple Choice (5 questions)
(format: question + 4 options + answer)

## Section B: Short Answer (5 questions)
(format: question + brief answer)

## Section C: Long Answer (2 questions)
(format: question + detailed answer)

## Flashcards (5 cards)
(format: front/back pairs)""",
    }

    fmt = prompts.get(qtype, prompts["mixed"])
    system = "You are an expert exam paper setter creating questions from lecture content. Questions should test understanding, not just memory."
    user = f"{fmt}\n\nTRANSCRIPT:\n{transcript}"
    return await safe_groq_call(system, user) or ""


# ──────────────────────────────────────
# 5. TOPIC SEGMENTATION
# ──────────────────────────────────────

async def segment_topics(transcript: str) -> str:
    """Automatically segment lecture into topics/chapters."""
    system = "You are an expert at organizing academic content into logical sections."
    user = f"""Analyze this lecture transcript and split it into logical topics/chapters:

For each topic provide:
## Chapter [N]: [Topic Title]
**Duration estimate:** [approximate portion of lecture]
**Key Points:**
- Point 1
- Point 2
**Summary:** [1-2 sentence summary of this section]

---

Make sure topics flow logically and cover the entire lecture.

TRANSCRIPT:
{transcript}"""
    return await safe_groq_call(system, user) or ""


# ──────────────────────────────────────
# 6. SMART HIGHLIGHT DETECTION
# ──────────────────────────────────────

async def detect_highlights(transcript: str) -> str:
    """Detect important statements and highlights in the transcript."""
    system = "You are an expert at identifying critical moments and important statements in lectures."
    user = f"""Analyze this lecture transcript and find:

## ⚠️ Explicitly Marked Important
Statements where the speaker says "important", "remember this", "note this", "key point", "this will come in exam", etc.

## 🔴 Critical Concepts
The most critical concepts that students MUST understand.

## 📌 Definitions Given
Any definitions explicitly stated by the speaker.

## 💡 Examples & Analogies
Notable examples or analogies used to explain concepts.

## ❓ Questions Asked
Any questions posed by the speaker or students.

For each item, quote the relevant text and explain why it's important.

TRANSCRIPT:
{transcript}"""
    return await safe_groq_call(system, user) or ""


# ──────────────────────────────────────
# 7. TRANSLATION
# ──────────────────────────────────────

LANGUAGE_MAP = {
    "hindi": "Hindi (हिन्दी)",
    "hinglish": "Hinglish (Hindi written in English/Roman script, mixing Hindi and English naturally)",
    "gujarati": "Gujarati (ગુજરાતી)",
    "marathi": "Marathi (मराठी)",
    "tamil": "Tamil (தமிழ்)",
    "telugu": "Telugu (తెలుగు)",
    "bengali": "Bengali (বাংলা)",
    "kannada": "Kannada (ಕನ್ನಡ)",
}


async def translate_content(content: str, target_language: str) -> str:
    """Translate content to the specified language."""
    lang_name = LANGUAGE_MAP.get(target_language, target_language)

    system = f"""You are an expert translator. Translate the following content to {lang_name}.
Rules:
- Keep all markdown formatting (##, ###, **, -, etc.) intact
- Keep emojis and special symbols as they are
- For technical terms, keep the English term in parentheses after the translation
- If the target is Hinglish, write Hindi words using English/Roman letters (e.g., "yeh bahut important hai")
- Maintain the same structure and organization
- Do NOT add any extra content or explanations"""

    user = f"Translate the following to {lang_name}:\n\n{content}"
    return await safe_groq_call(system, user, max_tokens=4096) or ""


def _extract_json_payload(raw: str) -> dict:
    """Extract best-effort JSON object from LLM output."""
    if not raw:
        return {}

    text = raw.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, flags=re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    return {}


def _normalize_date(value: str) -> str:
    if not value:
        return ""
    v = str(value).strip()
    if not v:
        return ""
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return v


def _normalize_task(task: dict, index: int) -> dict:
    if not isinstance(task, dict):
        task = {}

    priority = str(task.get("priority", "medium")).lower().strip()
    if priority not in ["high", "medium", "low"]:
        priority = "medium"

    status = str(task.get("status", "todo")).lower().strip()
    if status not in ["todo", "in_progress", "blocked", "done"]:
        status = "todo"

    dependencies = task.get("dependencies", [])
    if not isinstance(dependencies, list):
        dependencies = []

    title = str(task.get("title", "")).strip() or f"Task {index}"

    return {
        "id": str(task.get("id", f"task_{index}")).strip() or f"task_{index}",
        "title": title,
        "description": str(task.get("description", "")).strip(),
        "team": str(task.get("team", "Unassigned")).strip() or "Unassigned",
        "owner": str(task.get("owner", "")).strip() or "TBD",
        "priority": priority,
        "deadline": _normalize_date(str(task.get("deadline", "")).strip()),
        "status": status,
        "dependencies": [str(dep).strip() for dep in dependencies if str(dep).strip()],
    }


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z0-9]+", (text or "").lower()) if len(t) >= 2}


def _resolve_team_name(raw_team: str, task_context: str, allowed_team_names: list[str]) -> str:
    """Map model-provided team labels to workspace team names.
    Falls back to first available workspace team to avoid generic labels.
    """
    if not allowed_team_names:
        return (raw_team or "Unassigned").strip() or "Unassigned"

    cleaned = (raw_team or "").strip()
    if not cleaned:
        return allowed_team_names[0]

    allowed_lower = {name.lower(): name for name in allowed_team_names}
    if cleaned.lower() in allowed_lower:
        return allowed_lower[cleaned.lower()]

    # Fuzzy token overlap between task/team context and workspace team names.
    source_tokens = _tokenize(f"{cleaned} {task_context}")
    best_name = allowed_team_names[0]
    best_score = -1.0

    for candidate in allowed_team_names:
        cand_tokens = _tokenize(candidate)
        if not cand_tokens:
            continue
        overlap = len(source_tokens & cand_tokens)
        score = overlap / len(cand_tokens)

        # small boost for substring containment
        if cleaned.lower() in candidate.lower() or candidate.lower() in cleaned.lower():
            score += 0.4

        if score > best_score:
            best_score = score
            best_name = candidate

    return best_name


def normalize_action_plan_payload(payload: dict, workspace_teams: list[dict] | None = None) -> dict:
    """Normalize to strict, UI-ready schema."""
    if not isinstance(payload, dict):
        payload = {}

    raw_tasks = payload.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raw_tasks = []

    tasks = [_normalize_task(t, i + 1) for i, t in enumerate(raw_tasks)]

    workspace_teams = workspace_teams or []
    allowed_team_names = [str(t.get("name", "")).strip() for t in workspace_teams if str(t.get("name", "")).strip()]

    if allowed_team_names:
        for task in tasks:
            context = f"{task.get('title', '')} {task.get('description', '')}"
            task["team"] = _resolve_team_name(task.get("team", ""), context, allowed_team_names)

    # Ensure timeline is always usable: estimate deadlines when missing.
    base = datetime.utcnow().date()
    for idx, task in enumerate(tasks):
        if task.get("deadline"):
            continue

        priority = task.get("priority", "medium")
        if priority == "high":
            delta_days = 7 + (idx * 3)
        elif priority == "medium":
            delta_days = 14 + (idx * 4)
        else:
            delta_days = 21 + (idx * 5)

        task["deadline"] = (base + timedelta(days=delta_days)).isoformat()

    teams = payload.get("teams", {})
    if not isinstance(teams, dict):
        teams = {}

    # Build team map from workspace teams when available.
    if workspace_teams:
        team_map = {
            str(t.get("name", "")).strip(): str(t.get("description", "")).strip() or f"Tasks owned by {str(t.get('name', '')).strip()}"
            for t in workspace_teams
            if str(t.get("name", "")).strip()
        }
    else:
        team_map = {str(k): str(v) for k, v in teams.items()}

    # Ensure every referenced team exists in teams mapping.
    for task in tasks:
        if task["team"] not in team_map:
            team_map[task["team"]] = f"Tasks owned by {task['team']}"

    return {
        "summary": str(payload.get("summary", "")).strip(),
        "tasks": tasks,
        "teams": team_map,
    }


def build_action_plan_sections(content_json: dict, markdown: str) -> dict:
    tasks = content_json.get("tasks", []) if isinstance(content_json, dict) else []

    def sort_key(task: dict):
        deadline = task.get("deadline") or "9999-12-31"
        return deadline

    timeline = sorted([t for t in tasks if t.get("deadline")], key=sort_key)

    dependencies = []
    for t in tasks:
        for dep in t.get("dependencies", []):
            dependencies.append({
                "task_id": t.get("id"),
                "task_title": t.get("title"),
                "depends_on": dep,
            })

    team_breakdown = {}
    for t in tasks:
        team = t.get("team", "Unassigned")
        team_breakdown.setdefault(team, []).append(t.get("title", "Untitled task"))

    tasks_md_lines = ["## Tasks"]
    if not tasks:
        tasks_md_lines.append("No tasks found.")
    else:
        for t in tasks:
            extra = []
            if t.get("deadline"):
                extra.append(f"deadline: {t['deadline']}")
            extra.append(f"status: {t.get('status', 'todo')}")
            tasks_md_lines.append(f"- **{t.get('title', 'Task')}** ({', '.join(extra)})")
            if t.get("description"):
                tasks_md_lines.append(f"  - {t['description']}")

    timeline_md_lines = ["## Timeline"]
    if not timeline:
        timeline_md_lines.append("No deadline-based checkpoints available.")
    else:
        for t in timeline:
            timeline_md_lines.append(f"- {t.get('deadline')}: **{t.get('title')}**")

    dep_md_lines = ["## Dependencies"]
    if not dependencies:
        dep_md_lines.append("No explicit dependencies.")
    else:
        for d in dependencies:
            dep_md_lines.append(f"- **{d['task_title']}** depends on `{d['depends_on']}`")

    team_md_lines = ["## Team Breakdown"]
    if not team_breakdown:
        team_md_lines.append("No team assignment found.")
    else:
        for team, titles in team_breakdown.items():
            team_md_lines.append(f"### {team}")
            for title in titles:
                team_md_lines.append(f"- {title}")

    return {
        "tasks": {
            "content": "\n".join(tasks_md_lines),
            "content_json": tasks,
        },
        "timeline": {
            "content": "\n".join(timeline_md_lines),
            "content_json": timeline,
        },
        "dependencies": {
            "content": "\n".join(dep_md_lines),
            "content_json": dependencies,
        },
        "team_breakdown": {
            "content": "\n".join(team_md_lines),
            "content_json": team_breakdown,
        },
        "markdown": {
            "content": markdown,
            "content_json": content_json,
        },
    }


async def generate_lecture_action_plan(
    transcript: str,
    summary: str = "",
    highlights: str = "",
    workspace_teams: list[dict] | None = None,
) -> tuple[str, dict]:
    """
    Generate a lecture-level action plan with both markdown and strict JSON.
    Uses summary/highlights first for token efficiency and falls back to transcript context.
    """
    system = """You are an expert PM assistant creating execution-ready action plans.
Return valid JSON only, no prose outside JSON.
"""

    workspace_teams = workspace_teams or []
    team_lines = "\n".join([
        f"- {str(t.get('name', '')).strip()}: {str(t.get('description', '')).strip() or 'No description'}"
        for t in workspace_teams
        if str(t.get("name", "")).strip()
    ])

    user = f"""
Generate an action plan from this lecture context.

Preferred input (token-efficient):
SUMMARY:
{summary[:7000]}

HIGHLIGHTS:
{highlights[:7000]}

TRANSCRIPT CONTEXT:
{transcript[:12000]}

WORKSPACE TEAMS (must use these exact team names when assigning tasks):
{team_lines or '- Unassigned: No workspace teams available'}

Return strictly JSON with schema:
{{
  "summary": "short strategic summary",
  "tasks": [
    {{
      "id": "task_1",
      "title": "",
      "description": "",
      "team": "",
      "owner": "",
      "priority": "high|medium|low",
            "deadline": "YYYY-MM-DD",
      "status": "todo|in_progress|blocked|done",
      "dependencies": ["task_2"]
    }}
  ],
  "teams": {{ "Team Name": "what this team owns" }}
}}

Rules:
- Provide at least 5 tasks.
- Every task must include a realistic deadline in YYYY-MM-DD format.
- Use dependencies where sequencing is needed.
- If workspace teams are provided, assign every task to one of those exact team names only.
- Do not invent generic teams like "Backend Team" unless it exactly exists in workspace teams.
"""

    raw = await safe_groq_call(system, user, max_tokens=3072)
    payload = _extract_json_payload(raw)
    normalized = normalize_action_plan_payload(payload, workspace_teams=workspace_teams)

    sections = build_action_plan_sections(normalized, "")
    markdown = "\n\n".join([
        "# Action Plan",
        f"## Summary\n{normalized.get('summary') or 'No summary provided.'}",
        sections["tasks"]["content"],
        sections["dependencies"]["content"],
        sections["team_breakdown"]["content"],
        sections["timeline"]["content"],
    ])

    return markdown, normalized


def aggregate_workspace_action_plan(lecture_plans: list[dict]) -> tuple[str, dict]:
    """Aggregate lecture plans into workspace strategic plan with deduped tasks."""
    dedup: dict[str, dict] = {}

    for plan in lecture_plans:
        for task in plan.get("tasks", []):
            key = re.sub(r"\s+", " ", task.get("title", "").strip().lower())
            if not key:
                continue
            if key not in dedup:
                dedup[key] = task

    tasks = list(dedup.values())
    tasks.sort(key=lambda t: (t.get("deadline") or "9999-12-31", t.get("priority") or "medium"))

    dependencies = []
    for t in tasks:
        for dep in t.get("dependencies", []):
            dependencies.append({
                "task_id": t.get("id"),
                "task_title": t.get("title"),
                "depends_on": dep,
            })

    teams = {}
    for t in tasks:
        team = t.get("team", "Unassigned")
        teams.setdefault(team, []).append(t.get("title", "Untitled task"))

    timeline = [t for t in tasks if t.get("deadline")]
    timeline.sort(key=lambda t: t.get("deadline"))

    risks = []
    blocked = [t for t in tasks if t.get("status") == "blocked"]
    if blocked:
        risks.append(f"{len(blocked)} blocked task(s) need escalation.")
    if not timeline:
        risks.append("No dated milestones found; execution timeline is ambiguous.")

    content_json = {
        "summary": f"Aggregated action plan from {len(lecture_plans)} lecture plan(s).",
        "tasks": tasks,
        "dependencies": dependencies,
        "risks": risks,
        "timeline": timeline,
        "teams": teams,
    }

    md_lines = [
        "# Workspace Action Plan",
        f"## Strategic Summary\n{content_json['summary']}",
        "## Priority Tasks",
    ]
    if not tasks:
        md_lines.append("No tasks available.")
    else:
        for t in tasks:
            md_lines.append(f"- **{t.get('title')}** [{t.get('priority', 'medium')}] ({t.get('team', 'Unassigned')})")

    md_lines.append("## Dependencies")
    if not dependencies:
        md_lines.append("No explicit dependencies.")
    else:
        for d in dependencies:
            md_lines.append(f"- **{d['task_title']}** depends on `{d['depends_on']}`")

    md_lines.append("## Risks")
    if not risks:
        md_lines.append("No major risks flagged.")
    else:
        for r in risks:
            md_lines.append(f"- {r}")

    md_lines.append("## Timeline")
    if not timeline:
        md_lines.append("No deadline-based checkpoints.")
    else:
        for t in timeline:
            md_lines.append(f"- {t.get('deadline')}: {t.get('title')}")

    md_lines.append("## Team Breakdown")
    if not teams:
        md_lines.append("No team mappings available.")
    else:
        for team, titles in teams.items():
            md_lines.append(f"### {team}")
            for title in titles:
                md_lines.append(f"- {title}")

    return "\n".join(md_lines), content_json
