"""
Job reviewer using the Claude API — Phase 3
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import litellm

litellm.suppress_debug_info = True
logger = logging.getLogger(__name__)

# Load prompts once at import time — no disk I/O per request.
_PROMPT_DIR = Path(__file__).parent / "prompts"
DEFAULT_SCORING_PROMPT = (_PROMPT_DIR / "review_prompt.md").read_text(encoding="utf-8")
OUTPUT_FORMAT = (_PROMPT_DIR / "output_format.md").read_text(encoding="utf-8")


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

    MAX_TOKENS = 1024

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5"):
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
        system_prompt = f"{rubric.strip()}\n\n{OUTPUT_FORMAT.strip()}"

        try:
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                api_key=self.api_key,
                max_tokens=self.MAX_TOKENS,
            )
        except Exception as exc:
            logger.error("LLM API error for job %s (model=%s): %s", job_id, self.model, exc)
            return None

        raw_text = response.choices[0].message.content.strip()

        # Extract the JSON object robustly — handles markdown fences,
        # preamble text, or any other wrapper Claude might add.
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw_text = raw_text[start : end + 1]

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Claude returned non-JSON for job %s: %.200s", job_id, raw_text)
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
            logger.error("Failed to parse Claude response for job %s: %s", job_id, exc)
            return None
