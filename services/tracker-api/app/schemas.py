"""
Pydantic schemas for request / response validation.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.models import JobSource, JobStatus, LLMProvider


# ── Auth ─────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None


class AdminResetPasswordRequest(BaseModel):
    new_password: str


class UserOut(BaseModel):
    id: UUID
    email: str
    full_name: Optional[str]
    is_approved: bool
    is_admin: bool
    must_change_password: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    """Returned on successful login (token is also set as httpOnly cookie)."""
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Jobs (shared pool) ────────────────────────────────────────

class JobBase(BaseModel):
    title: str
    company: str
    url: str
    source: JobSource = JobSource.MANUAL
    location: Optional[str] = None
    remote: bool = False
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: str = "USD"
    description: Optional[str] = None
    date_posted: Optional[datetime] = None


class JobCreate(JobBase):
    external_id: Optional[str] = None


class JobOut(JobBase):
    id: UUID
    external_id: Optional[str]
    date_scraped: datetime

    class Config:
        from_attributes = True


class JobListOut(BaseModel):
    total: int
    items: List["UserJobReviewOut"]   # always return the per-user view


# ── Per-user job review ───────────────────────────────────────

class TimelineEventOut(BaseModel):
    id: UUID
    event_type: str
    description: Optional[str]
    occurred_at: datetime

    class Config:
        from_attributes = True


class UserJobReviewUpdate(BaseModel):
    """Fields the user can manually update on their job view."""
    status: Optional[JobStatus] = None
    notes: Optional[str] = None
    date_applied: Optional[datetime] = None
    has_contact: Optional[bool] = None
    contact_notes: Optional[str] = None
    application_answers: Optional[dict] = None  # {str(template_idx): answer}
    interview_questions: Optional[list] = None  # [{id, category, question, coaching, story_refs, notes}]


class JobAIUpdate(BaseModel):
    """Posted by the ai-reviewer service."""
    ai_score: float
    ai_summary: str
    ai_pros: Optional[List[str]] = None
    ai_cons: Optional[List[str]] = None
    skills_rank: Optional[int] = None
    experience_rank: Optional[int] = None
    location_rank: Optional[int] = None
    education_rank: Optional[int] = None
    salary_rank: Optional[int] = None
    recommended: Optional[bool] = None


class UserJobReviewOut(BaseModel):
    """Full per-user view of a job — job fields flattened in."""
    # Review identity
    id: UUID
    user_id: UUID
    job_id: UUID

    # Flattened job fields
    title: str
    company: str
    url: str
    source: str
    location: Optional[str]
    remote: bool
    salary_min: Optional[int]
    salary_max: Optional[int]
    salary_currency: str
    description: Optional[str]
    date_posted: Optional[datetime]
    date_scraped: datetime
    external_id: Optional[str]

    # AI review
    ai_score: Optional[float]
    ai_summary: Optional[str]
    ai_pros: Optional[List[str]]
    ai_cons: Optional[List[str]]
    skills_rank: Optional[int]
    experience_rank: Optional[int]
    location_rank: Optional[int]
    education_rank: Optional[int]
    salary_rank: Optional[int]
    recommended: Optional[bool]
    ai_reviewed_at: Optional[datetime]

    # User tracking
    status: JobStatus
    notes: Optional[str]
    date_applied: Optional[datetime]
    has_contact: bool
    contact_notes: Optional[str]
    research_summary: Optional[str]
    application_answers: Optional[dict]
    interview_questions: Optional[list]

    created_at: datetime
    updated_at: datetime
    timeline: List[TimelineEventOut] = []

    class Config:
        from_attributes = True

    @classmethod
    def from_review(cls, review) -> "UserJobReviewOut":
        """Build from a UserJobReview ORM object (with .job loaded)."""
        job = review.job
        return cls(
            id=review.id,
            user_id=review.user_id,
            job_id=review.job_id,
            title=job.title,
            company=job.company,
            url=job.url,
            source=job.source,
            location=job.location,
            remote=job.remote,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            description=job.description,
            date_posted=job.date_posted,
            date_scraped=job.date_scraped,
            external_id=job.external_id,
            ai_score=review.ai_score,
            ai_summary=review.ai_summary,
            ai_pros=review.ai_pros,
            ai_cons=review.ai_cons,
            skills_rank=review.skills_rank,
            experience_rank=review.experience_rank,
            location_rank=review.location_rank,
            education_rank=review.education_rank,
            salary_rank=review.salary_rank,
            recommended=review.recommended,
            ai_reviewed_at=review.ai_reviewed_at,
            status=review.status,
            notes=review.notes,
            date_applied=review.date_applied,
            has_contact=review.has_contact,
            contact_notes=review.contact_notes,
            research_summary=review.research_summary,
            application_answers=review.application_answers,
            interview_questions=review.interview_questions,
            created_at=review.created_at,
            updated_at=review.updated_at,
            timeline=review.timeline,
        )


# ── Criteria ──────────────────────────────────────────────────

class CriteriaBase(BaseModel):
    name: str = "default"
    job_titles: Optional[List[str]] = None
    search_locations: Optional[List[str]] = None   # keywords fed to scrapers
    work_style: str = "any"                        # remote | hybrid | onsite | any
    home_city: Optional[str] = None                # for commute distance scoring
    max_commute_miles: Optional[int] = None
    min_salary: Optional[int] = None
    excluded_companies: Optional[List[str]] = None
    target_companies: Optional[List[str]] = None   # score boost
    extra_instructions: Optional[str] = None
    research_prompt: Optional[str] = None
    application_templates: Optional[List[dict]] = None  # [{label, prompt}]
    voice_guidelines: Optional[str] = None
    interview_prep_prompt: Optional[str] = None
    # Legacy fields — preserved in DB, no longer shown in UI
    required_skills: Optional[List[str]] = None
    nice_to_have_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    locations: Optional[List[str]] = None
    remote_only: bool = False
    years_experience: Optional[int] = None


class CriteriaCreate(CriteriaBase):
    pass


class CriteriaUpdate(CriteriaBase):
    pass


class CriteriaOut(CriteriaBase):
    id: UUID
    user_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Profile ───────────────────────────────────────────────────

class ProfileBase(BaseModel):
    name: str = "default"
    full_name: Optional[str] = None
    location: Optional[str] = None
    resume_text: Optional[str] = None
    summary: Optional[str] = None
    skills: Optional[List[str]] = None
    education: Optional[str] = None
    desired_salary: Optional[int] = None
    commute_preference: Optional[str] = None
    extra_context: Optional[str] = None
    career_stories: Optional[List[dict]] = None  # [{title, content}]


class ProfileCreate(ProfileBase):
    pass


class ProfileUpdate(ProfileBase):
    pass


class ProfileOut(ProfileBase):
    id: UUID
    user_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── API Keys ──────────────────────────────────────────────────

class APIKeyUpsert(BaseModel):
    provider: LLMProvider
    api_key: str   # plaintext — encrypted before storage


class APIKeyOut(BaseModel):
    provider: LLMProvider
    key_hint: str   # last 4 chars only, e.g. "…xYZ9"
    updated_at: datetime

    class Config:
        from_attributes = True


# ── LinkedIn connections ──────────────────────────────────────

class LinkedInConnectionOut(BaseModel):
    id: UUID
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]
    company: Optional[str]
    position: Optional[str]
    connected_on: Optional[str]

    class Config:
        from_attributes = True


# ── Admin ─────────────────────────────────────────────────────

class AdminUserOut(UserOut):
    """Extended user view for admin panel."""
    pass


class PaginatedUsers(BaseModel):
    total: int
    items: List[AdminUserOut]
