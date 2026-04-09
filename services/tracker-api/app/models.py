"""
SQLAlchemy ORM models for JobRadar.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, Integer,
    String, Text, ForeignKey, JSON, Uuid,
)
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ── Enums ────────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    APPLIED = "applied"
    DISMISSED = "dismissed"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"


class JobSource(str, enum.Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GLASSDOOR = "glassdoor"
    DICE = "dice"
    MANUAL = "manual"


# ── Models ───────────────────────────────────────────────────

class Job(Base):
    """A single job posting."""
    __tablename__ = "jobs"

    id = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    external_id = Column(String(255), nullable=True)    # ID from the job board
    source = Column(Enum(JobSource), nullable=False, default=JobSource.MANUAL)

    # Core job details
    title = Column(String(255), nullable=False)
    company = Column(String(255), nullable=False)
    location = Column(String(255), nullable=True)
    remote = Column(Boolean, default=False)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    salary_currency = Column(String(10), default="USD")
    description = Column(Text, nullable=True)
    url = Column(String(2048), nullable=False)

    # Tracking
    status = Column(Enum(JobStatus), nullable=False, default=JobStatus.NEW)
    notes = Column(Text, nullable=True)
    date_posted = Column(DateTime(timezone=True), nullable=True)
    date_scraped = Column(DateTime(timezone=True), default=utcnow)
    date_applied = Column(DateTime(timezone=True), nullable=True)

    # AI review results (populated by ai-reviewer service)
    ai_score = Column(Float, nullable=True)        # 0.0 – 10.0
    ai_summary = Column(Text, nullable=True)
    ai_pros = Column(JSON, nullable=True)          # list of strings
    ai_cons = Column(JSON, nullable=True)          # list of strings
    ai_reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    timeline = relationship(
        "TimelineEvent", back_populates="job", cascade="all, delete-orphan",
        order_by="TimelineEvent.occurred_at"
    )


class TimelineEvent(Base):
    """An event in a job's application history (status change, email received, etc.)."""
    __tablename__ = "timeline_events"

    id = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    job_id = Column(Uuid(), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)   # status_change | email_received | note
    description = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), default=utcnow)

    job = relationship("Job", back_populates="timeline")


class Criteria(Base):
    """User-defined job matching criteria used by the AI reviewer."""
    __tablename__ = "criteria"

    id = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, default="default")
    is_active = Column(Boolean, default=True)

    # What the user is looking for
    job_titles = Column(JSON, nullable=True)           # e.g. ["Software Engineer", "Backend Engineer"]
    required_skills = Column(JSON, nullable=True)      # e.g. ["Python", "Kubernetes"]
    preferred_skills = Column(JSON, nullable=True)     # nice-to-have
    excluded_companies = Column(JSON, nullable=True)   # e.g. ["Enron"]
    locations = Column(JSON, nullable=True)            # e.g. ["Remote", "Austin, TX"]
    remote_only = Column(Boolean, default=False)
    min_salary = Column(Integer, nullable=True)
    extra_instructions = Column(Text, nullable=True)  # freeform prompt additions

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
