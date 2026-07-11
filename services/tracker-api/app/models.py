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
    Boolean, Column, Date, DateTime, Enum, Float, Integer,
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
    MONSTER      = "monster"
    ZIPRECRUITER = "ziprecruiter"
    ASHBY        = "ashby"
    GREENHOUSE   = "greenhouse"
    LEVER        = "lever"
    JSEARCH      = "jsearch"
    MANUAL       = "manual"


class LLMProvider(str, enum.Enum):
    ANTHROPIC = "anthropic"
    OPENAI    = "openai"
    GOOGLE    = "google"
    GROQ      = "groq"
    TAVILY    = "tavily"   # web search, not LLM — stored here for convenience
    ADZUNA    = "adzuna"   # job-board API (BYOK) — stored as {app_id, app_key} JSON
    JSEARCH   = "jsearch"  # job-board API (BYOK, RapidAPI) — plain key


# Providers that are actual chat/generation LLMs. TAVILY (web search) and
# ADZUNA/JSEARCH (job sources) live in the same user_api_keys table but must
# never be selected as the user's LLM key.
LLM_PROVIDERS = [
    LLMProvider.ANTHROPIC,
    LLMProvider.OPENAI,
    LLMProvider.GOOGLE,
    LLMProvider.GROQ,
]


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
    # Explicitly-chosen active LLM key. NULL = fall back to priority order
    # (Anthropic → OpenAI → Google → Groq). Set via the API Keys radio button.
    selected_llm_provider = Column(Enum(LLMProvider), nullable=True)
    created_at          = Column(DateTime(timezone=True), default=utcnow)
    updated_at          = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    reviews     = relationship("UserJobReview", back_populates="user", cascade="all, delete-orphan")
    criteria    = relationship("Criteria", back_populates="user", cascade="all, delete-orphan")
    profiles    = relationship("Profile", back_populates="user", cascade="all, delete-orphan")
    api_keys    = relationship("UserAPIKey", back_populates="user", cascade="all, delete-orphan")
    connections = relationship("LinkedInConnection", back_populates="user", cascade="all, delete-orphan")
    recruiters  = relationship("Recruiter", back_populates="user", cascade="all, delete-orphan")
    # Email agent
    inbox_emails      = relationship("InboxEmail", back_populates="user", cascade="all, delete-orphan")
    inbox_postings    = relationship("InboxPosting", back_populates="user", cascade="all, delete-orphan")
    inbox_interactions = relationship("InboxInteraction", back_populates="user", cascade="all, delete-orphan")
    agent_api_keys    = relationship("AgentAPIKey", back_populates="user", cascade="all, delete-orphan")
    email_credential  = relationship("EmailCredential", back_populates="user", cascade="all, delete-orphan", uselist=False)
    slack_connection  = relationship("SlackConnection", back_populates="user", cascade="all, delete-orphan", uselist=False)
    hitl_decisions    = relationship("HitlDecision", back_populates="user", cascade="all, delete-orphan")
    agent_runs        = relationship("AgentRun", back_populates="user", cascade="all, delete-orphan")


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

    # Recruiter who sourced this role (optional). SET NULL so deleting a recruiter
    # never deletes the job — it just unlinks.
    recruiter_id = Column(
        Uuid(), ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # AI-generated per-job content
    research_summary     = Column(Text, nullable=True)
    application_answers  = Column(JSON, nullable=True)  # {str(template_idx): answer}
    interview_questions  = Column(JSON, nullable=True)  # [{id, category, question, coaching, story_refs, notes}]
    # Per-job tailored résumé (Phase 2): {original, tailored, changes[], status,
    # model, generated_at, total_years}. Snapshot — not mutated when the base résumé changes.
    resume_tailor        = Column(JSON, nullable=True)
    # Per-job print/format override (Phase 4): {template, fontPt, density, marginIn,
    # accent, forceBreakBefore[]}. Falls back to the profile default when null.
    resume_print_settings = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    user     = relationship("User", back_populates="reviews")
    job      = relationship("Job", back_populates="user_reviews")
    recruiter = relationship("Recruiter", back_populates="jobs")
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
    resume_tailor_prompt  = Column(Text, nullable=True)  # editable style prompt (honesty core is server-side, not here)
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

    # Résumé tailoring (Phase 1): structured parse of resume_text + freshness flag.
    # resume_structured is the parsed sections JSON; stale=True means resume_text
    # changed since the last parse (re-ingest lazily on next tailor).
    resume_structured       = Column(JSON, nullable=True)
    resume_structured_stale = Column(Boolean, nullable=False, default=True)
    # Default print/format "knobs" (Phase 4): {template, fontPt, density, marginIn,
    # accent}. Per-job copies (UserJobReview.resume_print_settings) override this.
    resume_template_settings = Column(JSON, nullable=True)

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
    encrypted_key   = Column(Text, nullable=False)   # Fernet-encrypted, never plaintext
    preferred_model = Column(String(100), nullable=True)  # LiteLLM model string, e.g. "gpt-4o"
    created_at      = Column(DateTime(timezone=True), default=utcnow)
    updated_at      = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

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


# Recruiter CRM — status/type kept as plain strings (validated in schemas) to
# avoid native-enum migration overhead; values mirror the Literals in schemas.
RECRUITER_STATUSES = ("active", "ghosted", "archived", "do_not_contact")
RECRUITER_TYPES = ("agency", "in_house")


class Recruiter(Base):
    """A recruiter the user has connected with (manual entry or confirmed from an
    inbox recruiter_outreach email). Linked to the jobs they sourced via
    UserJobReview.recruiter_id."""
    __tablename__ = "recruiters"

    id           = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id      = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name         = Column(String(200), nullable=False)
    email        = Column(String(255), nullable=True, index=True)
    phone        = Column(String(50), nullable=True)
    title        = Column(String(200), nullable=True)            # e.g. "Senior Technical Recruiter"
    employer     = Column(String(200), nullable=True)            # their own firm
    companies_represented = Column(JSON, nullable=True)          # list[str] — agency clients
    linkedin_url = Column(String(500), nullable=True)
    type         = Column(String(20), nullable=True)             # 'agency' | 'in_house'
    status       = Column(String(20), nullable=False, default="active")
    last_contacted = Column(Date, nullable=True)
    notes        = Column(Text, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=utcnow)
    updated_at   = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="recruiters")
    # SET NULL at the DB; passive_deletes lets the DB handle unlink on delete.
    jobs = relationship("UserJobReview", back_populates="recruiter", passive_deletes=True)


# ── Email agent — enums ──────────────────────────────────────

class EmailCategory(str, enum.Enum):
    RECRUITER_OUTREACH       = "recruiter_outreach"
    APPLICATION_CONFIRMATION = "application_confirmation"
    JOB_ALERT                = "job_alert"
    NETWORK_NOTIFICATION     = "network_notification"


class EmailStatus(str, enum.Enum):
    PENDING      = "pending"
    PROCESSED    = "processed"
    NEEDS_REVIEW = "needs_review"
    DISCARDED    = "discarded"


class ImportStatus(str, enum.Enum):
    PENDING   = "pending"
    IMPORTED  = "imported"
    DISMISSED = "dismissed"


class EmailProvider(str, enum.Enum):
    GMAIL = "gmail"
    IMAP  = "imap"


class HitlStatus(str, enum.Enum):
    PENDING   = "pending"
    RESOLVED  = "resolved"
    ABANDONED = "abandoned"


class AgentRunStatus(str, enum.Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED  = "failed"


class AgentEnvironment(str, enum.Enum):
    LOCAL = "local"
    CLOUD = "cloud"


def value_enum(py_enum):
    """SQLAlchemy Enum that persists each member's ``.value`` (lowercase) rather
    than its ``.name`` (uppercase, SQLAlchemy's default).

    The native PG enum types created in migration 0011 use the lowercase values
    (e.g. ``'cloud'``), so the default name-based persistence sends ``'CLOUD'``
    and Postgres rejects it (InvalidTextRepresentation). ``values_callable``
    aligns both the write and read paths with the native type's values.

    Only used for the email-agent tables, whose native enum types this matches.
    JobStatus / LLMProvider columns are VARCHAR-backed and intentionally keep the
    default name-based behaviour to preserve existing data.
    """
    return Enum(py_enum, values_callable=lambda obj: [m.value for m in obj])


# ── Email agent — tables ─────────────────────────────────────

class InboxEmail(Base):
    """One row per processed source email. Idempotent on (user_id, message_id)."""
    __tablename__ = "inbox_emails"
    __table_args__ = (UniqueConstraint("user_id", "message_id", name="uq_inbox_user_message"),)

    id                 = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id            = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id         = Column(Text, nullable=False)   # RFC 822 Message-ID
    subject            = Column(Text, nullable=False)
    sender             = Column(Text, nullable=False)
    received_at        = Column(DateTime(timezone=True), nullable=False)
    category           = Column(value_enum(EmailCategory), nullable=False)
    confidence         = Column(Float, nullable=False)
    raw_extracted_json = Column(JSON, nullable=True)
    validation_attempts = Column(Integer, nullable=False, default=0)
    escalation_reason  = Column(Text, nullable=True)
    status             = Column(value_enum(EmailStatus), nullable=False, default=EmailStatus.PENDING)
    langfuse_trace_id  = Column(Text, nullable=True)
    created_at         = Column(DateTime(timezone=True), default=utcnow)

    user     = relationship("User", back_populates="inbox_emails")
    postings = relationship("InboxPosting", back_populates="inbox_email", cascade="all, delete-orphan")
    interactions = relationship("InboxInteraction", back_populates="inbox_email", cascade="all, delete-orphan")


class InboxPosting(Base):
    """One row per extracted job posting from an email. Capped at 30 per email (enforced in API)."""
    __tablename__ = "inbox_postings"

    id             = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    inbox_email_id = Column(Uuid(), ForeignKey("inbox_emails.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id        = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    company        = Column(Text, nullable=False)
    role           = Column(Text, nullable=False)
    link           = Column(Text, nullable=True)   # http/https only, validated at write (C2)
    action_required    = Column(Boolean, nullable=False, default=False)
    possible_duplicate = Column(Boolean, nullable=False, default=False)
    matched_review_id  = Column(Uuid(), ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True)
    import_status      = Column(value_enum(ImportStatus), nullable=False, default=ImportStatus.PENDING)
    imported_review_id = Column(Uuid(), ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True)
    created_at         = Column(DateTime(timezone=True), default=utcnow)

    user        = relationship("User", back_populates="inbox_postings")
    inbox_email = relationship("InboxEmail", back_populates="postings")


class InboxInteraction(Base):
    """One row per application-status-update email."""
    __tablename__ = "inbox_interactions"

    id             = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    inbox_email_id = Column(Uuid(), ForeignKey("inbox_emails.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id        = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    matched_review_id = Column(Uuid(), ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True)
    match_confidence  = Column(Float, nullable=True)   # null = no match
    previous_status   = Column(Enum(JobStatus), nullable=True)
    new_status        = Column(Enum(JobStatus), nullable=True)  # writable subset enforced in API (C1)
    applied_at        = Column(DateTime(timezone=True), nullable=True)
    created_at        = Column(DateTime(timezone=True), default=utcnow)

    user        = relationship("User", back_populates="inbox_interactions")
    inbox_email = relationship("InboxEmail", back_populates="interactions")


class AgentAPIKey(Base):
    """Per-user agent auth key. User derived from key; user_id never trusted from request (H1)."""
    __tablename__ = "agent_api_keys"

    id           = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id      = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key_hash     = Column(Text, nullable=False, unique=True, index=True)
    key_hint     = Column(Text, nullable=False)   # last 4 chars for display
    created_at   = Column(DateTime(timezone=True), default=utcnow)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked      = Column(Boolean, nullable=False, default=False)

    user = relationship("User", back_populates="agent_api_keys")


class EmailCredential(Base):
    """Per-user mailbox credentials, encrypted with ENCRYPTION_KEY (C3/H5)."""
    __tablename__ = "email_credentials"

    id             = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id        = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    provider       = Column(value_enum(EmailProvider), nullable=False)
    encrypted_blob = Column(Text, nullable=False)   # Fernet via ENCRYPTION_KEY, never SECRET_KEY
    folder_root          = Column(Text, nullable=True)
    folder_interaction   = Column(Text, nullable=True)
    folder_postings      = Column(Text, nullable=True)
    folder_social        = Column(Text, nullable=True)
    folder_unprocessed   = Column(Text, nullable=True)
    enabled        = Column(Boolean, nullable=False, server_default="true")  # per-user pause (JR-5)
    created_at     = Column(DateTime(timezone=True), default=utcnow)
    updated_at     = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="email_credential", uselist=False)


class SlackConnection(Base):
    """Per-user Slack workspace install (OAuth v2). The bot token is workspace-scoped
    and encrypted with ENCRYPTION_KEY; the channel is where the agent posts (JR-6)."""
    __tablename__ = "slack_connections"

    id                   = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id              = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    encrypted_bot_token  = Column(Text, nullable=False)   # xoxb-… Fernet via ENCRYPTION_KEY
    team_id              = Column(String(64), nullable=True)
    team_name            = Column(String(255), nullable=True)
    bot_user_id          = Column(String(64), nullable=True)
    scopes               = Column(Text, nullable=True)
    channel_id           = Column(String(64), nullable=True)   # chosen post target
    channel_name         = Column(String(255), nullable=True)
    created_at           = Column(DateTime(timezone=True), default=utcnow)
    updated_at           = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="slack_connection", uselist=False)


class HitlDecision(Base):
    """Interactive HITL resolution record (C4)."""
    __tablename__ = "hitl_decisions"

    id               = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id          = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    hitl_id          = Column(Text, nullable=False, unique=True, index=True)  # agent checkpoint correlator
    status           = Column(value_enum(HitlStatus), nullable=False, default=HitlStatus.PENDING)
    choice_review_id = Column(Uuid(), ForeignKey("user_job_reviews.id", ondelete="SET NULL"), nullable=True)
    created_at       = Column(DateTime(timezone=True), default=utcnow)
    resolved_at      = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="hitl_decisions")


class AgentRun(Base):
    """Operational heartbeat — counts only, no email content (H2)."""
    __tablename__ = "agent_runs"

    id                    = Column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id               = Column(Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    environment           = Column(value_enum(AgentEnvironment), nullable=False)
    agent_version         = Column(Text, nullable=False)
    status                = Column(value_enum(AgentRunStatus), nullable=False)
    started_at            = Column(DateTime(timezone=True), nullable=False)
    finished_at           = Column(DateTime(timezone=True), nullable=True)
    emails_processed      = Column(Integer, nullable=False, default=0)
    postings_created      = Column(Integer, nullable=False, default=0)
    interactions_recorded = Column(Integer, nullable=False, default=0)
    escalations           = Column(Integer, nullable=False, default=0)
    retries               = Column(Integer, nullable=False, default=0)
    error_summary         = Column(Text, nullable=True)

    user = relationship("User", back_populates="agent_runs")
