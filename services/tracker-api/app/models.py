"""
SQLAlchemy ORM models for JobRadar — multi-user edition.

Architecture
────────────
Jobs are a shared pool (scraped once, visible to all users).
Everything user-specific lives in separate tables keyed by user_id:
  • UserJobReview  — per-user AI scores, status, notes, timeline
  • Criteria       — per-user search criteria
  • Profile        — per-user résumé / candidate info
  • UserAPIKey     — per-user encrypted provider keys (Anthropic, OpenAI, …)
  • LinkedInConnection — per-user imported contacts
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, Integer,
    String, Text, ForeignKey, JSON, Uuid, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ── Enums ────────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    NEW          = "new"
    REVIEWED     = "reviewed"
    APPLIED      = "applied"
    DISMISSED    = "dismissed"
    INTERVIEWING = "interviewing"
    OFFER        = "offer"
    REJECTED     = "rejected"
    EXPIRED      = "expired"


class JobSource(str, enum.Enum):
    ADZUNA    = "adzuna"
    THE_MUSE  = "the_muse"
    REMOTIVE  = "remotive"
    LINKEDIN  = "linkedin"
    INDEED    = "indeed"
    GLASSDOOR = "glassdoor"
    DICE      = "dice"
    BUILTIN   = "builtin"
    MANUAL    = "manual"


class LLMProvider(str, enum.Enum):
    ANTHROPIC = "anthropic"
    OPENAI    = "openai"
    GOOGLE    = "google"
    GROQ      = "groq"
    TAVILY    = "tavily"   # web search, not LLM — stored here for convenience


# ── Users ────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id                  = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    email               = Column(String(255), unique=True, nullable=False, index=True)
    password_hash       = Column(String(255), nullable=False)
    full_name           = Column(String(255), nullable=True)
    is_approved         = Column(Boolean, default=False)
    is_admin            = Column(Boolean, default=False)
    must_change_password = Column(Boolean, default=False)
    created_at          = Column(DateTime(timezone=True), default=utcnow)
    updated_at          = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    reviews     = relationship("UserJobReview", back_populates="user", cascade="all, delete-orphan")
    criteria    = relationship("Criteria", back_populates="user", cascade="all, delete-orphan")
    profiles    = relationship("Profile", back_populates="user", cascade="all, delete-orphan")
    api_keys    = relationship("UserAPIKey", back_populates="user", cascade="all, delete-orphan")
    connections = relationship("LinkedInConnection", back_populates="user", cascade="all, delete-orphan")


# ── Shared job pool ──────────────────────────────────────────

class Job(Base):
    """A raw job posting from any source. Shared across all users."""
    __tablename__ = "jobs"

    id              = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    external_id     = Column(String(255), nullable=True)
    source          = Column(String(50), nullable=False, default=JobSource.MANUAL.value)

    title           = Column(String(255), nullable=False)
    company         = Column(String(255), nullable=False)
    location        = Column(String(255), nullable=True)
    remote          = Column(Boolean, default=False)
    salary_min      = Column(Integer, nullable=True)
    salary_max      = Column(Integer, nullable=True)
    salary_currency = Column(String(10), default="USD")
    description     = Column(Text, nullable=True)
    url             = Column(String(2048), nullable=False)

    date_posted     = Column(DateTime(timezone=True), nullable=True)
    date_scraped    = Column(DateTime(timezone=True), default=utcnow)

    # Relationships
    user_reviews = relationship("UserJobReview", back_populates="job", cascade="all, delete-orphan")


# ── Per-user job data ────────────────────────────────────────

class UserJobReview(Base):
    """
    Per-user view of a job: AI scores, application status, notes, timeline.
    One row per (user, job) pair.
    """
    __tablename__ = "user_job_reviews"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_user_job"),)

    id      = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id  = Column(Uuid(), ForeignKey("jobs.id",  ondelete="CASCADE"), nullable=False, index=True)

    # AI review (populated by ai-reviewer service)
    ai_score        = Column(Float, nullable=True)
    ai_summary      = Column(Text, nullable=True)
    ai_pros         = Column(JSON, nullable=True)
    ai_cons         = Column(JSON, nullable=True)
    skills_rank     = Column(Integer, nullable=True)
    experience_rank = Column(Integer, nullable=True)
    location_rank   = Column(Integer, nullable=True)
    education_rank  = Column(Integer, nullable=True)
    salary_rank     = Column(Integer, nullable=True)
    recommended     = Column(Boolean, nullable=True)
    ai_reviewed_at  = Column(DateTime(timezone=True), nullable=True)

    # Application tracking
    status       = Column(Enum(JobStatus), nullable=False, default=JobStatus.NEW)
    notes        = Column(Text, nullable=True)
    date_applied = Column(DateTime(timezone=True), nullable=True)

    # Network — does the user know someone at this company?
    has_contact      = Column(Boolean, default=False)
    contact_notes    = Column(Text, nullable=True)   # "Sarah at Acme, 2nd-degree via John"

    # AI-generated per-job content
    research_summary     = Column(Text, nullable=True)
    application_answers  = Column(JSON, nullable=True)  # {str(template_idx): answer}
    interview_questions  = Column(JSON, nullable=True)  # [{id, category, question, coaching, story_refs, notes}]

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    user     = relationship("User", back_populates="reviews")
    job      = relationship("Job", back_populates="user_reviews")
    timeline = relationship(
        "TimelineEvent", back_populates="review",
        cascade="all, delete-orphan",
        order_by="TimelineEvent.occurred_at",
    )


class TimelineEvent(Base):
    """An event in a user's job application journey."""
    __tablename__ = "timeline_events"

    id        = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    review_id = Column(Uuid(), ForeignKey("user_job_reviews.id", ondelete="CASCADE"), nullable=False)
    event_type  = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), default=utcnow)

    review = relationship("UserJobReview", back_populates="timeline")


# ── Per-user criteria & profile ──────────────────────────────

class Criteria(Base):
    __tablename__ = "criteria"

    id      = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name    = Column(String(100), nullable=False, default="default")
    is_active = Column(Boolean, default=True)

    job_titles           = Column(JSON, nullable=True)
    search_locations     = Column(JSON, nullable=True)
    work_style           = Column(String(20), default="any")
    home_city            = Column(String(255), nullable=True)
    max_commute_miles    = Column(Integer, nullable=True)
    min_salary           = Column(Integer, nullable=True)
    excluded_companies   = Column(JSON, nullable=True)
    target_companies     = Column(JSON, nullable=True)
    extra_instructions   = Column(Text, nullable=True)   # deprecated — use scoring_prompt
    scoring_prompt       = Column(Text, nullable=True)
    research_prompt      = Column(Text, nullable=True)
    application_templates = Column(JSON, nullable=True)  # [{label, prompt}]
    voice_guidelines     = Column(Text, nullable=True)
    interview_prep_prompt = Column(Text, nullable=True)
    # Legacy columns preserved for data migration
    required_skills      = Column(JSON, nullable=True)
    preferred_skills     = Column(JSON, nullable=True)
    nice_to_have_skills  = Column(JSON, nullable=True)
    locations            = Column(JSON, nullable=True)
    remote_only          = Column(Boolean, default=False)
    years_experience     = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="criteria")


class Profile(Base):
    __tablename__ = "profiles"

    id      = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name    = Column(String(100), nullable=False, default="default")
    is_active = Column(Boolean, default=True)

    full_name          = Column(String(255), nullable=True)
    location           = Column(String(255), nullable=True)
    resume_text        = Column(Text, nullable=True)
    summary            = Column(Text, nullable=True)
    skills             = Column(JSON, nullable=True)
    education          = Column(Text, nullable=True)
    desired_salary     = Column(Integer, nullable=True)
    commute_preference = Column(String(255), nullable=True)
    extra_context      = Column(Text, nullable=True)
    career_stories     = Column(JSON, nullable=True)  # [{title, content}]

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="profiles")


# ── Per-user API keys ────────────────────────────────────────

class UserAPIKey(Base):
    """Encrypted API keys per user per provider."""
    __tablename__ = "user_api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_provider"),)

    id            = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id       = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider      = Column(Enum(LLMProvider), nullable=False)
    encrypted_key = Column(Text, nullable=False)   # Fernet-encrypted, never plaintext
    created_at    = Column(DateTime(timezone=True), default=utcnow)
    updated_at    = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="api_keys")


# ── LinkedIn connections ─────────────────────────────────────

class LinkedInConnection(Base):
    """Imported from LinkedIn data export (Connections.csv)."""
    __tablename__ = "linkedin_connections"

    id           = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id      = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    first_name   = Column(String(100), nullable=True)
    last_name    = Column(String(100), nullable=True)
    email        = Column(String(255), nullable=True)
    company      = Column(String(255), nullable=True, index=True)
    position     = Column(String(255), nullable=True)
    connected_on = Column(String(50), nullable=True)   # keep as string, format varies
    created_at   = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="connections")
