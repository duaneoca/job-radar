"""
Pydantic schemas for request/response validation.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel

from app.models import JobSource, JobStatus


# ── Job Schemas ──────────────────────────────────────────────

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
    notes: Optional[str] = None


class JobCreate(JobBase):
    external_id: Optional[str] = None


class JobUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    remote: Optional[bool] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    description: Optional[str] = None
    status: Optional[JobStatus] = None
    notes: Optional[str] = None
    date_applied: Optional[datetime] = None


class JobAIUpdate(BaseModel):
    """Used by the ai-reviewer service to post scores."""
    ai_score: float
    ai_summary: str
    ai_pros: Optional[List[str]] = None
    ai_cons: Optional[List[str]] = None


class TimelineEventOut(BaseModel):
    id: UUID
    event_type: str
    description: Optional[str]
    occurred_at: datetime

    class Config:
        from_attributes = True


class JobOut(JobBase):
    id: UUID
    status: JobStatus
    external_id: Optional[str]
    ai_score: Optional[float]
    ai_summary: Optional[str]
    ai_pros: Optional[List[str]]
    ai_cons: Optional[List[str]]
    ai_reviewed_at: Optional[datetime]
    date_scraped: datetime
    date_applied: Optional[datetime]
    timeline: List[TimelineEventOut] = []

    class Config:
        from_attributes = True


class JobListOut(BaseModel):
    total: int
    items: List[JobOut]


# ── Criteria Schemas ─────────────────────────────────────────

class CriteriaBase(BaseModel):
    name: str = "default"
    job_titles: Optional[List[str]] = None
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    excluded_companies: Optional[List[str]] = None
    locations: Optional[List[str]] = None
    remote_only: bool = False
    min_salary: Optional[int] = None
    extra_instructions: Optional[str] = None


class CriteriaCreate(CriteriaBase):
    pass


class CriteriaUpdate(CriteriaBase):
    pass


class CriteriaOut(CriteriaBase):
    id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
