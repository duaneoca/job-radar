"""
Job reviewer using the Claude API — Phase 3
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import litellm

from app.llm_errors import (
    LLMCallFailed, ResponseTruncated, classify_llm_error, transient_kind,
)

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


# Providers whose API has a NATIVE json_object mode.
#
# Keyed on PROVIDER, not on model names. Anthropic's problem belongs to litellm's
# Anthropic adapter, not to any particular Claude model — and a list of model
# prefixes rots the moment a model is retired, which is the same reason this
# codebase has no default model.
#
# NOT litellm.get_supported_openai_params(), which reports Anthropic as
# supporting response_format but implements it by forcing a tool call with an
# EMPTY schema (properties: {}, additionalProperties: true). Claude then fills
# that tool with whatever fields suit the input and ignores the output format the
# prompt asked for. Measured, same prompt and model:
#
#   without response_format → {"score": 0.5, "summary": "…"}          correct
#   with    response_format → {"position": "Senior Engineer", …}      wrong keys
#
# The JSON parses; it is simply not our JSON. That reaches the KeyError path,
# scores nothing, and after three strikes tells the user their model is broken —
# blaming Anthropic for our parameter. Passing a real response_schema doesn't
# help either: litellm then nests it under a "values" key, which our parser
# would also miss.
#
# An allow-list, not a deny-list: a provider nobody has verified gets prompt-only
# JSON, which works everywhere and is where we were before. Sending an emulated
# parameter is the failure mode, so "don't" is the safe default. Values are
# models.LLMProvider members, delivered by GET /keys/internal/{user}/llm.
_NATIVE_JSON_MODE_PROVIDERS = frozenset({"openai", "google", "groq"})


def _supports_json_mode(provider: str | None) -> bool:
    """Whether asking this provider for JSON at the API level actually helps."""
    return (provider or "").lower() in _NATIVE_JSON_MODE_PROVIDERS


# Providers that need an explicit cache_control breakpoint to cache a prompt.
#
# A scrape scores a burst of jobs for one user, and everything but the posting —
# rubric, skills, output format, profile, résumé, criteria — is identical across
# that burst. Anthropic caches that prefix only when asked; reads then bill at a
# fraction of the input price. OpenAI and Gemini cache a repeated prefix on their
# own, so all they need is the ordering, which every provider gets.
#
# Anthropic only, because in litellm==1.57.3 only the Anthropic adapter knows the
# field; the others would forward it in the content block to an API that never
# asked for it. Same allow-list reasoning as JSON mode: unverified means "don't".
#
# The default 5-minute TTL, deliberately: jobs in a burst start seconds apart and
# every read refreshes it. The 1-hour TTL doubles the write price for nothing here.
_PROMPT_CACHE_PROVIDERS = frozenset({"anthropic"})


def _supports_prompt_cache(provider: str | None) -> bool:
    """Whether this provider needs (and understands) a cache_control breakpoint."""
    return (provider or "").lower() in _PROMPT_CACHE_PROVIDERS


def build_messages(
    system_prompt: str, candidate: str, posting: str, provider: str | None,
) -> list[dict]:
    """The chat messages for one review: stable candidate context, then the posting.

    Every provider sees the same text in the same order. Only the shape differs:
    for a caching provider the user turn is two blocks, with the breakpoint on the
    candidate block so the cached prefix ends exactly where the per-job text begins.
    """
    if not _supports_prompt_cache(provider):
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{candidate}\n\n{posting}"},
        ]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": candidate, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": posting},
        ]},
    ]


def cache_usage(response) -> tuple[int, int, int]:
    """(input, cache_read, cache_write) tokens, 0 where the provider is silent.

    litellm normalises cache reads into prompt_tokens_details.cached_tokens for
    every provider that reports them (OpenAI's automatic cache included); writes
    are Anthropic-only. A caching bug raises nothing — the request just costs full
    price — so these numbers are the only way to see whether it works.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    details = getattr(usage, "prompt_tokens_details", None)
    return (
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(details, "cached_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


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

    def __init__(self, api_key: str, model: str, provider: str | None = None):
        # No default model — one is always supplied by the caller, which got it
        # from the user's own explicit choice. Defaulting here would apply an
        # Anthropic model string to (say) a Google key.
        #
        # `provider` decides whether we ask for JSON at the API level. It arrives
        # in the same payload as the key, so it cannot disagree with the model.
        # Optional only so the class stays constructible from a script; None means
        # prompt-only, which is the safe direction.
        self.api_key = api_key
        self.model = model
        self.provider = provider

    def _build_candidate_context(self, criteria: dict, profile: dict) -> str:
        """Profile, résumé and criteria — identical for every job in a user's burst.

        Kept separate from the posting, and ahead of it, so it can be cached.
        Nothing per-job (or per-request: no timestamps, no ids) may go in here,
        or every job writes a fresh cache entry at a premium instead of reading one.
        """
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
Minimum salary: ${criteria.get('min_salary') or 0:,}"""

    @staticmethod
    def _build_job_posting(
        job_title: str,
        company: str,
        location: str | None,
        remote: bool,
        description: str,
        salary_min: int | None,
        salary_max: int | None,
    ) -> str:
        """The per-job part of the user message. Always last."""
        salary_line = "Not provided"
        if salary_min and salary_max:
            salary_line = f"${salary_min:,} – ${salary_max:,}"
        elif salary_min:
            salary_line = f"${salary_min:,}+"

        return f"""## Job Posting
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
        candidate = self._build_candidate_context(criteria=criteria, profile=profile)
        posting = self._build_job_posting(
            job_title=job_title,
            company=company,
            location=location,
            remote=remote,
            description=description,
            salary_min=salary_min,
            salary_max=salary_max,
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
        if _supports_json_mode(self.provider):
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = litellm.completion(
                model=self.model,
                messages=build_messages(system_prompt, candidate, posting, self.provider),
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

        # Truncation is OUR ceiling, not the model misbehaving. Without this the
        # partial object falls through to the parser, returns None, and gets
        # reported as unusable_output — blaming the user's model for our
        # configuration and eventually raising a banner telling them to switch.
        if getattr(response.choices[0], "finish_reason", None) == "length":
            raise ResponseTruncated(
                f"truncated at max_tokens={self.MAX_TOKENS} (model={self.model})"
            )

        raw_text = (response.choices[0].message.content or "").strip()

        # Sizes, so a memory delta can be attributed. A steady leak is roughly
        # constant per task regardless of these; a leak driven by one enormous
        # résumé or job description tracks them. Without this the RSS numbers
        # say something grew but never which input caused it.
        in_tok, cache_read, cache_write = cache_usage(response)
        logger.info(
            "size job=%s prompt=%dB reply=%dB model=%s in_tok=%d cache_read=%d cache_write=%d",
            job_id, len(system_prompt) + len(candidate) + len(posting), len(raw_text),
            self.model, in_tok, cache_read, cache_write,
        )

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
