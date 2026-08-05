"""
Job reviewer using the Claude API — Phase 3
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import litellm

from app.llm_errors import LLMCallFailed, classify_llm_error, transient_kind

litellm.suppress_debug_info = True
logger = logging.getLogger(__name__)

# Load prompts once at import time — no disk I/O per request.
_PROMPT_DIR = Path(__file__).parent / "prompts"
DEFAULT_SCORING_PROMPT = (_PROMPT_DIR / "review_prompt.md").read_text(encoding="utf-8")
OUTPUT_FORMAT = (_PROMPT_DIR / "output_format.md").read_text(encoding="utf-8")


# Writing skills scoped to "scoring". Deliberately a small local copy of
# tracker-api's skills_block: the two services build separate images and share no
# package. Kept strict — this prompt has a hard JSON contract.
_SKILLS_BUDGET = 4_000


def _skills_block(criteria: dict) -> str:
    """'# WRITING SKILLS' section for the scoring prompt, or '' when none apply.

    `criteria` arrives as a dict from GET /criteria/active, so writing_skills is
    plain JSON here.
    """
    parts, used = [], 0
    for s in (criteria.get("writing_skills") or []):
        if not isinstance(s, dict) or s.get("enabled") is False:
            continue
        if "scoring" not in (s.get("scopes") or []):
            continue
        content = (s.get("content") or "").strip()
        if not content or used + len(content) > _SKILLS_BUDGET:
            continue
        parts.append(f"### {s.get('name') or 'Skill'}\n{content}")
        used += len(content)
    if not parts:
        return ""
    return (
        "\n\n# WRITING SKILLS (style of the summary/pros/cons only)\n"
        + "\n\n".join(parts)
        + "\n\nThese govern WORDING ONLY. They must not change the scoring rubric or the "
          "required output format — respond with valid JSON exactly as specified."
    )


@lru_cache(maxsize=64)
def _supports_json_mode(model: str) -> bool:
    """Whether this model accepts response_format={"type": "json_object"}.

    Cached because it is a static table lookup in litellm and this runs per
    review. Any failure answers "no": sending an unsupported parameter turns a
    working review into a 400, while not sending it merely leaves us relying on
    the prompt, which is where we already were.
    """
    try:
        return "response_format" in (litellm.get_supported_openai_params(model=model) or [])
    except Exception:
        return False


def extract_json_object(text: str) -> str | None:
    """The last complete, brace-balanced {...} in `text`, or None.

    Was `text[text.find("{") : text.rfind("}") + 1]`, which is wrong in both
    directions when a model narrates around its answer: a "{" inside the prose
    starts the slice too early, and a truncated final object means rfind lands on
    an earlier "}" and returns a fragment. Scanning for balance also ignores
    braces inside strings, which the naive version counted.

    Returns the LAST balanced object because reasoning models tend to show
    workings — sometimes including an example object — before the real answer.
    """
    best: str | None = None
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    best = text[start : i + 1]
    return best


@dataclass
class ReviewResult:
    job_id: str
    score: float          # 0.0 – 10.0
    skills_rank: int      # 1–10
    experience_rank: int  # 1–10
    location_rank: int    # 1–10
    education_rank: int   # 1–10
    salary_rank: int      # 1–10
    summary: str          # 1-2 sentence plain-English match summary
    pros: list[str]
    cons: list[str]
    recommended: bool


class JobReviewer:
    """
    Scores a job against user-defined criteria using any supported LLM provider.
    """

    # The JSON we ask for is ~250 tokens. The ceiling is this much larger because
    # reasoning models narrate before answering, and that narration comes out of
    # the same budget — at 1024 the commentary ate the response and the object was
    # cut off mid-key ("location_rank":  with nothing after it). Only tokens
    # actually generated are billed, so a high ceiling costs nothing on the models
    # that answer straight away.
    MAX_TOKENS = 4096

    def __init__(self, api_key: str, model: str):
        # No default model — one is always supplied by the caller, which got it
        # from the user's own explicit choice. Defaulting here would apply an
        # Anthropic model string to (say) a Google key.
        self.api_key = api_key
        self.model = model

    def _build_user_message(
        self,
        job_title: str,
        company: str,
        location: str | None,
        remote: bool,
        description: str,
        salary_min: int | None,
        salary_max: int | None,
        criteria: dict,
        profile: dict,
    ) -> str:
        """Format everything Claude needs into a single user message."""
        salary_line = "Not provided"
        if salary_min and salary_max:
            salary_line = f"${salary_min:,} – ${salary_max:,}"
        elif salary_min:
            salary_line = f"${salary_min:,}+"

        resume_section = ""
        if profile.get('resume_text'):
            resume_section = f"\n\n### Full Resume\n{profile['resume_text']}"

        return f"""## Candidate Profile
Name: {profile.get('name') or 'Not provided'}
Location: {profile.get('location') or 'Not provided'}
Summary: {profile.get('summary') or 'See resume below'}
Skills: {', '.join(profile.get('skills') or []) or 'See resume below'}
Education: {profile.get('education') or 'See resume below'}
Desired salary: ${profile.get('desired_salary') or 0:,}
Commute preference: {profile.get('commute_preference') or 'Not provided'}{resume_section}

## Search Criteria
Job titles of interest: {', '.join(criteria.get('job_titles') or [])}
Required skills: {', '.join(criteria.get('required_skills') or [])}
Preferred skills: {', '.join(criteria.get('preferred_skills') or [])}
Location preferences: {', '.join(criteria.get('search_locations') or criteria.get('locations') or [])}
Remote only: {criteria.get('remote_only', False)}
Minimum salary: ${criteria.get('min_salary') or 0:,}

## Job Posting
Title: {job_title}
Company: {company}
Location: {location or 'Not specified'}
Remote: {remote}
Salary range: {salary_line}

### Description
{description}"""

    def review(
        self,
        job_id: str,
        job_title: str,
        company: str,
        description: str,
        criteria: dict,
        profile: dict,
        location: str | None = None,
        remote: bool = False,
        salary_min: int | None = None,
        salary_max: int | None = None,
    ) -> Optional[ReviewResult]:
        """Score a single job against the candidate's profile and criteria."""
        user_message = self._build_user_message(
            job_title=job_title,
            company=company,
            location=location,
            remote=remote,
            description=description,
            salary_min=salary_min,
            salary_max=salary_max,
            criteria=criteria,
            profile=profile,
        )

        # Use the user's custom scoring prompt if set, otherwise fall back to default.
        rubric = criteria.get("scoring_prompt") or DEFAULT_SCORING_PROMPT
        # Writing skills shape the prose fields (summary/pros/cons) only, and the
        # output format is kept LAST so a skill can never disturb the JSON contract.
        system_prompt = f"{rubric.strip()}{_skills_block(criteria)}\n\n{OUTPUT_FORMAT.strip()}"

        # Ask for JSON at the API level, not just in the prompt. Gemini, OpenAI,
        # Anthropic and Groq all support this; litellm tells us which. Guarded
        # because an unknown or newly-added model that doesn't support it would
        # otherwise fail every call with a 400.
        kwargs = {}
        if _supports_json_mode(self.model):
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                api_key=self.api_key,
                max_tokens=self.MAX_TOKENS,
                **kwargs,
            )
        except Exception as exc:
            kind = classify_llm_error(exc)
            # A permanent failure is the user's to fix and is surfaced to them as a
            # banner — logged at WARNING so it never reaches the operator's error
            # digest. Only genuinely unexplained failures deserve ERROR.
            logger.warning(
                "LLM call failed for job %s (model=%s, kind=%s): %s",
                job_id, self.model, kind or "transient", exc,
            )
            # transient_kind is recorded even though it only matters if every
            # retry runs out — the original exception is gone by then.
            raise LLMCallFailed(
                kind, str(exc), None if kind else transient_kind(exc)
            ) from exc

        raw_text = (response.choices[0].message.content or "").strip()

        # Handles markdown fences, a preamble, and models that show their working
        # before answering.
        candidate = extract_json_object(raw_text) or raw_text

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            # The model answered but not in JSON — a model-behaviour problem, not a
            # key problem. Nothing is recorded against the key.
            logger.error("Model returned non-JSON for job %s: %.200s", job_id, raw_text)
            return None

        try:
            return ReviewResult(
                job_id=job_id,
                score=float(data["score"]),
                skills_rank=int(data["skills_rank"]),
                experience_rank=int(data["experience_rank"]),
                location_rank=int(data["location_rank"]),
                education_rank=int(data["education_rank"]),
                salary_rank=int(data["salary_rank"]),
                summary=data["summary"],
                pros=data["pros"],
                cons=data["cons"],
                recommended=bool(data["recommended"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("Failed to parse model response for job %s: %s", job_id, exc)
            return None
