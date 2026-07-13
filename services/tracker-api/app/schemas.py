"""
Pydantic schemas for request / response validation.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

import re

from pydantic import BaseModel, EmailStr, field_validator

from app.models import (
    AgentEnvironment, AgentRunStatus,
    EmailCategory, EmailStatus,
    HitlStatus, ImportStatus,
    JobSource, JobStatus, LLMProvider,
)


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
    # Global feature flag (app_settings), not a per-user column. Populated on the
    # session endpoints (login, GET/PATCH /auth/me) so the frontend learns it at
    # startup; defaults False elsewhere (admin user lists), where it isn't read.
    email_agent_enabled: bool = False

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    """Returned on successful login (token is also set as httpOnly cookie)."""
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Global settings (admin feature flags) ─────────────────────

class AppSettingsOut(BaseModel):
    email_agent_enabled: bool


class AppSettingsUpdate(BaseModel):
    """Partial update — only provided fields change."""
    email_agent_enabled: Optional[bool] = None


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

    # Sourcing recruiter (optional)
    recruiter_id: Optional[UUID] = None
    recruiter_name: Optional[str] = None

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
            recruiter_id=review.recruiter_id,
            recruiter_name=(review.recruiter.name if review.recruiter else None),
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
    extra_instructions: Optional[str] = None   # deprecated — use scoring_prompt
    scoring_prompt: Optional[str] = None
    research_prompt: Optional[str] = None
    application_templates: Optional[List[dict]] = None  # [{label, prompt}]
    voice_guidelines: Optional[str] = None
    interview_prep_prompt: Optional[str] = None
    resume_tailor_prompt: Optional[str] = None   # editable style prompt (honesty core is server-side)
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
    # Résumé tailoring (Phase 1) — read-only; resume_structured is derived from
    # resume_text via /profile/resume/ingest.
    resume_structured: Optional[dict] = None
    resume_structured_stale: bool = True
    resume_template_settings: Optional[dict] = None  # Phase 4 default print knobs
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Résumé structured parse (tailoring) ───────────────────────

class ResumeContact(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    links: List[str] = []


class ResumeSkillGroup(BaseModel):
    label: str
    items: List[str] = []


class ResumePhase(BaseModel):
    label: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    bullets: List[str] = []


class ResumeExperience(BaseModel):
    company: str
    titles: List[str] = []
    start: Optional[str] = None
    end: Optional[str] = None
    bullets: List[str] = []
    phases: List[ResumePhase] = []
    notable: List[str] = []


class ResumeEducation(BaseModel):
    degree: Optional[str] = None
    school: Optional[str] = None


class ResumeProject(BaseModel):
    title: Optional[str] = None
    bullets: List[str] = []


class ResumeStructured(BaseModel):
    """The canonical structured résumé — output of the ingest parse and the unit
    of tailoring/diffing. Lenient: a parse that omits a section still validates."""
    contact: ResumeContact = ResumeContact()
    summary: Optional[str] = None
    skills: List[ResumeSkillGroup] = []
    experience: List[ResumeExperience] = []
    education: List[ResumeEducation] = []
    projects: List[ResumeProject] = []


class ResumeIngestOut(BaseModel):
    """Result of /profile/resume/ingest."""
    structured: ResumeStructured
    honesty_facts: dict
    stale: bool = False


# ── Print / format settings (Phase 4 knobs) ───────────────────

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class PrintSettings(BaseModel):
    """User print "knobs" — stored as a profile default and/or per-résumé override.
    Values are sanitized: bounded numerics, enum'd choices, and accent constrained to a
    6-digit hex (it is injected into a CSS variable on the client)."""
    template: Optional[str] = None
    fontPt: Optional[float] = None
    density: Optional[str] = None
    marginIn: Optional[float] = None
    accent: Optional[str] = None
    forceBreakBefore: List[str] = []

    @field_validator("template")
    @classmethod
    def _template(cls, v):
        return v if v in (None, "classic", "modern") else "classic"

    @field_validator("density")
    @classmethod
    def _density(cls, v):
        return v if v in (None, "compact", "normal", "roomy") else "normal"

    @field_validator("fontPt")
    @classmethod
    def _font(cls, v):
        return None if v is None else max(8.0, min(12.0, float(v)))

    @field_validator("marginIn")
    @classmethod
    def _margin(cls, v):
        return None if v is None else max(0.35, min(0.85, float(v)))

    @field_validator("accent")
    @classmethod
    def _accent(cls, v):
        # null = "template default"; anything not a clean hex is dropped (CSS safety).
        return v if (v is None or _HEX_RE.match(v)) else None

    @field_validator("forceBreakBefore")
    @classmethod
    def _breaks(cls, v):
        # Stable block ids only (defensive cap; opaque to the server).
        return [str(x)[:80] for x in (v or [])][:50]


class PrintSettingsIn(BaseModel):
    settings: PrintSettings


class TailorRefineIn(BaseModel):
    instruction: str   # "emphasize cloud architecture; leave the summary alone"


class ChangeDecisionsIn(BaseModel):
    decisions: dict[str, str]   # {change_id: "accepted" | "rejected" | "pending"}


# ── API Keys ──────────────────────────────────────────────────

class APIKeyUpsert(BaseModel):
    provider: LLMProvider
    api_key: Optional[str] = None        # plaintext — encrypted before storage (LLM/Tavily)
    preferred_model: Optional[str] = None  # LiteLLM model string
    # Adzuna uses a two-part credential instead of a single api_key.
    app_id: Optional[str] = None
    app_key: Optional[str] = None


class APIKeyModelUpdate(BaseModel):
    preferred_model: Optional[str] = None


class APIKeyOut(BaseModel):
    provider: LLMProvider
    key_hint: str                        # last 4 chars only, e.g. "…xYZ9"
    preferred_model: Optional[str] = None
    updated_at: datetime
    active: bool = False                 # the LLM key currently used (selected, else priority)

    class Config:
        from_attributes = True


class ActiveKeyUpdate(BaseModel):
    provider: Optional[LLMProvider] = None  # null clears the selection (back to priority order)


# ── Scraper per-user config (internal, in-cluster only) ───────

class ScraperAdzunaCreds(BaseModel):
    app_id: str
    app_key: str


class ScraperUserConfig(BaseModel):
    """One active user's scrape inputs: criteria + decrypted Adzuna creds."""
    user_id: UUID
    job_titles: List[str] = []
    search_locations: List[str] = []
    work_style: str = "any"
    adzuna: Optional[ScraperAdzunaCreds] = None


# ── LinkedIn connections ──────────────────────────────────────

class LinkedInConnectionOut(BaseModel):
    id: UUID
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]
    company: Optional[str]
    position: Optional[str]
    connected_on: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Recruiters ────────────────────────────────────────────────

from datetime import date as _date  # noqa: E402
from typing import Literal  # noqa: E402

RecruiterStatus = Literal["active", "ghosted", "archived", "do_not_contact"]
RecruiterType = Literal["agency", "in_house"]


class RecruiterJobBrief(BaseModel):
    """A job linked to a recruiter — minimal fields for the recruiter's job list."""
    id: UUID            # UserJobReview.id (use as the route param elsewhere)
    title: str
    company: str
    status: JobStatus

    class Config:
        from_attributes = True


class RecruiterBase(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    employer: Optional[str] = None
    companies_represented: Optional[List[str]] = None
    linkedin_url: Optional[str] = None
    type: Optional[RecruiterType] = None
    status: RecruiterStatus = "active"
    last_contacted: Optional[_date] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class RecruiterCreate(RecruiterBase):
    pass


class RecruiterUpdate(BaseModel):
    """All fields optional — partial update."""
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    employer: Optional[str] = None
    companies_represented: Optional[List[str]] = None
    linkedin_url: Optional[str] = None
    type: Optional[RecruiterType] = None
    status: Optional[RecruiterStatus] = None
    last_contacted: Optional[_date] = None
    notes: Optional[str] = None


class RecruiterOut(RecruiterBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    jobs: List[RecruiterJobBrief] = []

    class Config:
        from_attributes = True


class RecruiterJobLink(BaseModel):
    review_id: UUID     # UserJobReview.id to link to this recruiter


class RecruiterSuggestion(BaseModel):
    """A proposed recruiter parsed from an inbox recruiter_outreach email. The user
    confirms before it becomes a Recruiter row.

    Beyond the parsed sender (name/email), fields below are populated from the
    agent's `recruiter_contact` card (signature/body extraction) when present —
    all agent-derived and therefore untrusted (sanitized server-side; the client
    still escapes on render and routes linkedin_url through safeHref)."""
    name: str
    email: Optional[str] = None
    email_count: int    # how many recruiter emails from this address
    # Enriched from the agent's recruiter_contact card (all best-effort/optional)
    phone: Optional[str] = None
    title: Optional[str] = None
    employer: Optional[str] = None
    linkedin_url: Optional[str] = None
    type: Optional[RecruiterType] = None              # derived from is_agency
    companies_represented: Optional[List[str]] = None  # from `represents`
    recruiter_confidence: Optional[float] = None       # agent's extraction confidence


# ── Admin ─────────────────────────────────────────────────────

class AdminUserOut(UserOut):
    """Extended user view for admin panel."""
    pass


class PaginatedUsers(BaseModel):
    total: int
    items: List[AdminUserOut]


# ── Email agent — request/response schemas ────────────────────

# POST /agent/inbox
class AgentPostingIn(BaseModel):
    company: str
    role: str
    link: Optional[str] = None
    action_required: bool = False
    possible_duplicate: bool = False
    matched_review_id: Optional[UUID] = None


class AgentInboxIn(BaseModel):
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    category: EmailCategory
    confidence: float
    langfuse_trace_id: Optional[str] = None
    raw_extracted_json: Optional[dict] = None
    postings: List[AgentPostingIn] = []


class AgentInboxOut(BaseModel):
    inbox_email_id: UUID
    posting_ids: List[UUID]


# POST /agent/interactions
class AgentInteractionIn(BaseModel):
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    category: EmailCategory
    confidence: float
    langfuse_trace_id: Optional[str] = None
    matched_review_id: Optional[UUID] = None
    match_confidence: Optional[float] = None   # null = no match (paired with matched_review_id: null)
    new_status: Optional[JobStatus] = None
    timeline_note: Optional[str] = None


class AgentInteractionOut(BaseModel):
    interaction_id: UUID
    applied_status: Optional[str] = None  # the status written to the review, if any


# GET /agent/reviews
class AgentReviewOut(BaseModel):
    review_id: UUID
    company: str
    title: str
    status: JobStatus
    url: str

    class Config:
        from_attributes = True


# HITL
class AgentHitlRegisterIn(BaseModel):
    hitl_id: str
    candidates: List[UUID]  # review_ids the agent is asking about


class HitlDecisionOut(BaseModel):
    hitl_id: str
    status: HitlStatus
    choice_review_id: Optional[UUID]

    class Config:
        from_attributes = True


class AgentHitlConsumeIn(BaseModel):
    hitl_id: str


# POST /agent/runs
class AgentRunIn(BaseModel):
    environment: AgentEnvironment
    agent_version: str
    status: AgentRunStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    emails_processed: int = 0
    postings_created: int = 0
    interactions_recorded: int = 0
    escalations: int = 0
    retries: int = 0
    error_summary: Optional[str] = None


class AgentRunOut(BaseModel):
    run_id: UUID


# Frontend inbox views
class InboxPostingOut(BaseModel):
    id: UUID
    company: str
    role: str
    link: Optional[str]
    action_required: bool
    possible_duplicate: bool
    matched_review_id: Optional[UUID]
    import_status: ImportStatus
    imported_review_id: Optional[UUID]
    created_at: datetime

    class Config:
        from_attributes = True


class InboxInteractionOut(BaseModel):
    id: UUID
    matched_review_id: Optional[UUID]
    match_confidence: float
    previous_status: Optional[JobStatus]
    new_status: Optional[JobStatus]
    applied_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class InboxEmailOut(BaseModel):
    id: UUID
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    category: EmailCategory
    confidence: float
    status: EmailStatus
    escalation_reason: Optional[str]
    langfuse_trace_id: Optional[str]
    created_at: datetime
    postings: List[InboxPostingOut] = []
    interactions: List[InboxInteractionOut] = []

    class Config:
        from_attributes = True


class InboxEmailUpdate(BaseModel):
    status: Optional[EmailStatus] = None


class PaginatedInbox(BaseModel):
    total: int
    items: List[InboxEmailOut]


# GET /agent/stats
class AgentLastRunOut(BaseModel):
    run_id: UUID
    status: AgentRunStatus
    finished_at: Optional[datetime]
    emails_processed: int
    environment: AgentEnvironment

    class Config:
        from_attributes = True


class AgentStatsOut(BaseModel):
    emails_today: int
    emails_this_week: int
    category_breakdown: dict
    escalation_rate: float
    jobs_imported: int
    last_run: Optional[AgentLastRunOut]


# GET /agent/config
class AgentLLMConfig(BaseModel):
    provider: str
    preferred_model: Optional[str]
    api_key: str  # decrypted — in-cluster only (H6/H6a)


class AgentFolderConfig(BaseModel):
    root: Optional[str]
    interaction: Optional[str]
    postings: Optional[str]
    social: Optional[str]
    unprocessed: Optional[str]


class AgentSlackConfig(BaseModel):
    bot_token: str          # decrypted, workspace-scoped xoxb- — in-cluster only
    channel_id: str


class AgentConfigOut(BaseModel):
    provider: Optional[str]  # email provider: gmail | imap
    folders: AgentFolderConfig
    llm: Optional[AgentLLMConfig]
    email_credentials: Optional[dict]  # decrypted blob — in-cluster only
    enabled: bool = False    # per-user pause; agent skips this user when False
    slack: Optional[AgentSlackConfig] = None  # per-user notifier; None until connected + channel set


# Slack notifications (per-user OAuth install — JR-6). Status is masked; never the token.
class SlackStatusOut(BaseModel):
    connected: bool
    team_name: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None


class SlackChannelOut(BaseModel):
    id: str
    name: str


class SlackChannelUpdate(BaseModel):
    channel_id: str
    channel_name: Optional[str] = None


# Cloud enumeration (internal-token, in-cluster only — JR-5 §2.1b).
# No secrets: the runner uses this to discover users, then fetches one config at a time.
class CloudUserOut(BaseModel):
    user_id: UUID
    provider: Optional[str]
    enabled: bool


# Email-credential management (cloud Gmail users — JR-5).
# The refresh_token is never returned to the frontend; only connection status.
class EmailCredentialStatusOut(BaseModel):
    provider: Optional[str]            # gmail | imap | None
    connected: bool                    # usable creds stored (refresh_token or imap host)
    enabled: bool
    folders: AgentFolderConfig
    updated_at: Optional[datetime]
    imap_host: Optional[str] = None      # non-secret, for prefilling the IMAP form
    imap_username: Optional[str] = None  # the password is never returned


class EmailCredentialUpdateIn(BaseModel):
    folders: AgentFolderConfig
    enabled: bool


# Live folder/label list for the connected mailbox — powers the picker so users
# choose exact names (incl. hierarchy prefix) instead of typing them.
class MailboxFoldersOut(BaseModel):
    provider: Optional[str]            # gmail | imap
    delimiter: Optional[str] = None    # hierarchy separator the server uses (e.g. "/" or ".")
    folders: List[str] = []            # full hierarchical names, as the server reports them


# IMAP "Other" provider — host/port/user/password/SSL (cloud non-Gmail mailbox).
# Verified live (connection + folder existence) before storing.
class ImapCredentialsIn(BaseModel):
    host: str
    port: int = 993
    username: str
    password: str
    use_ssl: bool = True
    folders: Optional[AgentFolderConfig] = None


# Agent API key management
class AgentAPIKeyOut(BaseModel):
    id: UUID
    key_hint: str
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked: bool

    class Config:
        from_attributes = True


class AgentAPIKeyCreatedOut(AgentAPIKeyOut):
    raw_key: str  # shown once at creation, never stored
