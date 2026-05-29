"""
On-demand AI generation — research summaries and application assistance.
All calls use the user's own Anthropic API key (BYOK).
"""

import json
import logging
import uuid as uuid_lib
from typing import List, Optional
from uuid import UUID

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app import models
from app.database import get_db
from app.deps import get_current_user
from app.security import decrypt_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["generate"])

MODEL = "claude-haiku-4-5"

DEFAULT_RESEARCH_PROMPT = """Summarize this company based on the job posting:
1. What they do and their market position
2. Culture and work environment signals from the posting
3. Growth stage / stability signals
4. Why this role could be a good fit given the candidate's background"""

DEFAULT_APPLICATION_TEMPLATES = [
    {
        "label": "Cover Letter",
        "prompt": "Write a compelling cover letter for this position (under 300 words). Reference specific details from the job description and draw on relevant experience from the resume. Be genuine and specific, avoid generic phrases.",
    },
    {
        "label": "Why do you want to work here?",
        "prompt": "Write 2-3 sentences explaining why the candidate wants to work at this specific company in this role. Focus on genuine alignment between their background/goals and the company's mission or product.",
    },
    {
        "label": "About me",
        "prompt": "Write a 2-3 sentence professional summary tailored for this specific application, highlighting the most relevant skills and experience from the resume.",
    },
]


def _get_review(review_id: UUID, user: models.User, db: Session) -> models.UserJobReview:
    review = (
        db.query(models.UserJobReview)
        .options(joinedload(models.UserJobReview.job))
        .filter(
            models.UserJobReview.id == review_id,
            models.UserJobReview.user_id == user.id,
        )
        .first()
    )
    if not review:
        raise HTTPException(status_code=404, detail="Job not found")
    return review


def _get_api_key(user_id: UUID, db: Session) -> str:
    key_obj = (
        db.query(models.UserAPIKey)
        .filter(
            models.UserAPIKey.user_id == user_id,
            models.UserAPIKey.provider == models.LLMProvider.ANTHROPIC,
        )
        .first()
    )
    if not key_obj:
        raise HTTPException(status_code=400, detail="No Anthropic API key configured. Add one in Settings → API Keys.")
    return decrypt_api_key(key_obj.encrypted_key)


def _get_criteria(user_id: UUID, db: Session) -> models.Criteria | None:
    return (
        db.query(models.Criteria)
        .filter(models.Criteria.user_id == user_id, models.Criteria.is_active == True)  # noqa: E712
        .order_by(models.Criteria.updated_at.desc())
        .first()
    )


def _get_profile(user_id: UUID, db: Session) -> models.Profile | None:
    return (
        db.query(models.Profile)
        .filter(models.Profile.user_id == user_id, models.Profile.is_active == True)  # noqa: E712
        .order_by(models.Profile.updated_at.desc())
        .first()
    )


def _resume_block(profile: models.Profile | None) -> str:
    if profile and profile.resume_text:
        return f"\n\n## Candidate Resume\n{profile.resume_text}"
    return "\n\n## Candidate Resume\nNot provided"


def _job_block(job: models.Job) -> str:
    return f"""## Job Posting
Title: {job.title}
Company: {job.company}
Location: {job.location or 'Not specified'}
Remote: {job.remote}
Description:
{job.description or 'Not provided'}"""


@router.post("/{review_id}/research")
def generate_research(
    review_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate an AI company research summary for a job."""
    review = _get_review(review_id, current_user, db)
    job = review.job

    criteria = _get_criteria(current_user.id, db)
    profile = _get_profile(current_user.id, db)
    api_key = _get_api_key(current_user.id, db)

    research_prompt = (criteria.research_prompt if criteria else None) or DEFAULT_RESEARCH_PROMPT

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system="You are a career research assistant helping a job candidate research a company before applying. Be concise and practical.",
            messages=[{
                "role": "user",
                "content": f"{_job_block(job)}{_resume_block(profile)}\n\n## Research Request\n{research_prompt}",
            }],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=400, detail="Invalid Anthropic API key.")
    except Exception as e:
        logger.exception("Claude API error during research generation")
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    summary = msg.content[0].text

    review.research_summary = summary
    db.commit()

    return {"summary": summary}


@router.post("/{review_id}/application/{template_idx}")
def generate_application_answer(
    review_id: UUID,
    template_idx: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate an AI answer for one application template."""
    review = _get_review(review_id, current_user, db)
    job = review.job

    criteria = _get_criteria(current_user.id, db)
    profile = _get_profile(current_user.id, db)
    api_key = _get_api_key(current_user.id, db)

    templates = (criteria.application_templates if criteria else None) or DEFAULT_APPLICATION_TEMPLATES
    if template_idx < 0 or template_idx >= len(templates):
        raise HTTPException(status_code=404, detail="Template index out of range")

    template = templates[template_idx]

    voice = (criteria.voice_guidelines if criteria else None) or ""
    voice_section = f"\n\nVoice and style guidelines — follow these carefully:\n{voice}" if voice else ""
    system_prompt = (
        f"You are a career assistant helping a job candidate write application materials. "
        f"Write in first person as the candidate. Be specific and draw from the resume.{voice_section}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": f"{_job_block(job)}{_resume_block(profile)}\n\n## Task: {template['label']}\n{template['prompt']}",
            }],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=400, detail="Invalid Anthropic API key.")
    except Exception as e:
        logger.exception("Claude API error during application generation")
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    answer = msg.content[0].text

    # Save answer back to the review
    answers = dict(review.application_answers or {})
    answers[str(template_idx)] = answer
    review.application_answers = answers
    db.commit()

    return {"answer": answer}


# ── Refinement chat ───────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class RefineRequest(BaseModel):
    messages: List[ChatMessage]
    template_idx: int
    current_answer: Optional[str] = None


@router.post("/{review_id}/refine")
def refine_application(
    review_id: UUID,
    body: RefineRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Conversational refinement of a single application answer."""
    review = _get_review(review_id, current_user, db)
    job = review.job

    criteria = _get_criteria(current_user.id, db)
    profile = _get_profile(current_user.id, db)
    api_key = _get_api_key(current_user.id, db)

    templates = (criteria.application_templates if criteria else None) or DEFAULT_APPLICATION_TEMPLATES
    if body.template_idx < 0 or body.template_idx >= len(templates):
        raise HTTPException(status_code=404, detail="Template index out of range")

    template = templates[body.template_idx]
    voice = (criteria.voice_guidelines if criteria else None) or ""
    voice_section = f"\n\nVoice and style guidelines — follow these carefully:\n{voice}" if voice else ""

    # Build the current-draft section so the model always knows what exists
    if body.current_answer:
        draft_section = f"\n\n## Current draft (what you already wrote)\n{body.current_answer}"
    else:
        draft_section = "\n\n## Current draft\nNone yet — this is a fresh start."

    system_prompt = (
        f"You are a career assistant helping a job candidate refine their application materials "
        f"through a back-and-forth conversation. You wrote the current draft shown below. "
        f"Help the candidate improve it based on their feedback. "
        f"When they ask for a revision, output the complete revised text so they can copy it directly. "
        f"Be specific, draw from the resume, and write in first person as the candidate.\n\n"
        f"{_job_block(job)}{_resume_block(profile)}\n\n"
        f"## Template: {template['label']}\n{template['prompt']}"
        f"{draft_section}"
        f"{voice_section}"
    )

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=400, detail="Invalid Anthropic API key.")
    except Exception as e:
        logger.exception("Claude API error during refinement")
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    return {"response": msg.content[0].text}


# ── Prompt-extraction (merge conversation learnings into existing prompts) ────

class ExtractChangesRequest(BaseModel):
    messages: List[ChatMessage]
    change_type: str          # "voice" | "application"
    current_content: str      # existing guidelines or prompt text to merge into
    template_idx: int = 0     # used only for application type (for label context)


@router.post("/{review_id}/extract-changes")
def extract_prompt_changes(
    review_id: UUID,
    body: ExtractChangesRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Reads a refinement conversation and merges the learnings into either
    the voice guidelines or the application template prompt.
    Returns a proposed replacement — not saved until the user confirms.
    """
    if body.change_type not in ("voice", "application"):
        raise HTTPException(status_code=400, detail="change_type must be 'voice' or 'application'")

    _get_review(review_id, current_user, db)  # validates ownership
    criteria = _get_criteria(current_user.id, db)
    api_key = _get_api_key(current_user.id, db)

    conversation_text = "\n".join(
        f"{'User' if m.role == 'user' else 'AI'}: {m.content}"
        for m in body.messages
    )

    if body.change_type == "voice":
        system_prompt = (
            "You are a writing-style analyst. Your job is to extract style and voice preferences "
            "from a conversation between a job candidate and an AI writing assistant, then merge "
            "them into existing voice guidelines.\n\n"
            "Rules:\n"
            "- Extract ONLY style/tone/voice preferences (e.g. word choices, punctuation rules, "
            "register, things to avoid). Ignore content/structural feedback.\n"
            "- Merge intelligently: don't duplicate existing rules. If a new rule contradicts an "
            "old one, replace the old one.\n"
            "- Keep the output concise and actionable — a list of clear guidelines.\n"
            "- Output ONLY the merged guidelines text. No preamble, no explanation."
        )
        user_content = (
            f"## Existing voice guidelines\n{body.current_content or '(none yet)'}\n\n"
            f"## Conversation to extract from\n{conversation_text}\n\n"
            "Produce the merged voice guidelines."
        )
    else:
        templates = (criteria.application_templates if criteria else None) or DEFAULT_APPLICATION_TEMPLATES
        template_label = (
            templates[body.template_idx]["label"]
            if isinstance(templates[body.template_idx], dict)
            else templates[body.template_idx].get("label", "")
        ) if body.template_idx < len(templates) else ""

        system_prompt = (
            "You are a prompt engineer. Your job is to extract content and structural preferences "
            "from a conversation between a job candidate and an AI writing assistant, then merge "
            "them into an existing application template prompt.\n\n"
            "Rules:\n"
            "- Extract ONLY content/structural changes (e.g. what to include, exclude, emphasize, "
            "length, specific talking points). Ignore style/tone feedback.\n"
            "- Merge intelligently: update or remove instructions that conflict with new ones.\n"
            "- Keep the output as a clean, direct instruction prompt for an AI writing assistant.\n"
            "- Output ONLY the merged prompt text. No preamble, no explanation."
        )
        user_content = (
            f"## Template: {template_label}\n\n"
            f"## Existing prompt\n{body.current_content or '(none yet)'}\n\n"
            f"## Conversation to extract from\n{conversation_text}\n\n"
            "Produce the merged application prompt."
        )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=400, detail="Invalid Anthropic API key.")
    except Exception as e:
        logger.exception("Claude API error during prompt extraction")
        raise HTTPException(status_code=502, detail=f"AI extraction failed: {e}")

    return {"proposed": msg.content[0].text}


# ── Interview prep ────────────────────────────────────────────────────────────

DEFAULT_INTERVIEW_PREP_PROMPT = """You are an experienced hiring manager preparing to interview a candidate for the role below.

Generate 12–15 interview questions you would realistically ask, covering four categories:
- Behavioral (past experience stories — "Tell me about a time…")
- Technical (skills, tools, and role-specific knowledge)
- Situational (hypothetical scenarios — "What would you do if…")
- Culture/Motivation (fit, values, goals — "Why this company?", "Where do you see yourself in 5 years?")

For each question write a coaching note that:
1. Names the best career story or experience from the candidate's background to draw on (use story titles if provided)
2. Specifies what outcome or angle to emphasize for this specific role
3. Notes any direct connection to language or requirements in the job description

Return a JSON array only — no other text, no markdown code fences. Schema:
[{"category": "Behavioral", "question": "...", "coaching": "...", "story_refs": ["story title or empty"]}]"""


@router.post("/{review_id}/interview-prep")
def generate_interview_prep(
    review_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate AI interview questions with coaching notes for a job."""
    review = _get_review(review_id, current_user, db)
    job = review.job

    criteria = _get_criteria(current_user.id, db)
    profile = _get_profile(current_user.id, db)
    api_key = _get_api_key(current_user.id, db)

    prep_prompt = (criteria.interview_prep_prompt if criteria else None) or DEFAULT_INTERVIEW_PREP_PROMPT

    # Build career stories block
    stories_block = ""
    if profile and profile.career_stories:
        stories = profile.career_stories or []
        if stories:
            lines = []
            for s in stories:
                title = s.get("title", "Untitled")
                content = s.get("content", "")
                lines.append(f"### {title}\n{content}")
            stories_block = "\n\n## Candidate's Career Stories\n" + "\n\n".join(lines)

    user_content = f"{_job_block(job)}{_resume_block(profile)}{stories_block}\n\n{prep_prompt}"

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system="You are an expert interview coach and hiring manager. Always respond with valid JSON only.",
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=400, detail="Invalid Anthropic API key.")
    except Exception as e:
        logger.exception("Claude API error during interview prep generation")
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    raw = msg.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

    try:
        questions_raw = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse interview prep JSON: %s\nRaw: %s", e, raw)
        raise HTTPException(status_code=502, detail="AI returned malformed JSON. Try regenerating.")

    # Claude sometimes wraps the list in {"questions": [...]} — unwrap it.
    if isinstance(questions_raw, dict):
        questions_raw = questions_raw.get("questions", [])

    # Stamp each question with a unique ID and empty notes field
    questions = []
    for q in questions_raw:
        if not isinstance(q, dict):
            continue  # skip any malformed items
        questions.append({
            "id": str(uuid_lib.uuid4()),
            "category": q.get("category", "General"),
            "question": q.get("question", ""),
            "coaching": q.get("coaching", ""),
            "story_refs": q.get("story_refs") or [],
            "notes": "",
        })

    review.interview_questions = questions
    db.commit()

    return {"questions": questions}
