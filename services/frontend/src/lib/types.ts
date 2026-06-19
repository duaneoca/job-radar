export type JobStatus =
  | "new" | "reviewed" | "applied" | "dismissed"
  | "interviewing" | "offer" | "rejected" | "expired";

export type JobSource =
  | "adzuna" | "the_muse" | "remotive" | "linkedin"
  | "indeed" | "glassdoor" | "dice" | "manual";

export type LLMProvider = "anthropic" | "openai" | "google" | "groq" | "tavily" | "adzuna";

// ─── Email agent — keys & stats ───────────────────────────────
export type AgentRunStatus = "success" | "partial" | "failed";
export type AgentEnvironment = "local" | "cloud";

export interface AgentApiKey {
  id: string;
  key_hint: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}
export interface AgentApiKeyCreated extends AgentApiKey {
  raw_key: string;
}

export interface AgentLastRun {
  run_id: string;
  status: AgentRunStatus;
  finished_at: string | null;
  emails_processed: number;
  environment: AgentEnvironment;
}
export interface AgentStats {
  emails_today: number;
  emails_this_week: number;
  category_breakdown: Record<string, number>;
  escalation_rate: number;
  jobs_imported: number;
  last_run: AgentLastRun | null;
}

// ─── Cloud mailbox connection (Gmail OAuth — JR-5) ────────────
export interface AgentFolderConfig {
  root: string | null;
  interaction: string | null;
  postings: string | null;
  social: string | null;
  unprocessed: string | null;
}
export interface EmailCredentialStatus {
  provider: "gmail" | "imap" | null;
  connected: boolean;
  enabled: boolean;
  folders: AgentFolderConfig;
  updated_at: string | null;
  imap_host?: string | null;
  imap_username?: string | null;
}

// ─── Slack notifications (per-user OAuth install — JR-6) ──────
export interface SlackStatus {
  connected: boolean;
  team_name?: string | null;
  channel_id?: string | null;
  channel_name?: string | null;
}
export interface SlackChannel {
  id: string;
  name: string;
}

// ─── Email agent inbox ────────────────────────────────────────
export type EmailCategory =
  | "recruiter_outreach" | "application_confirmation" | "job_alert" | "network_notification";
export type EmailStatus = "pending" | "processed" | "needs_review" | "discarded";
export type ImportStatus = "pending" | "imported" | "dismissed";

export interface InboxPosting {
  id: string;
  company: string;
  role: string;
  link: string | null;
  action_required: boolean;
  possible_duplicate: boolean;
  matched_review_id: string | null;
  import_status: ImportStatus;
  imported_review_id: string | null;
  created_at: string;
}

export interface InboxInteraction {
  id: string;
  matched_review_id: string | null;
  match_confidence: number;
  previous_status: JobStatus | null;
  new_status: JobStatus | null;
  applied_at: string | null;
  created_at: string;
}

export interface InboxEmail {
  id: string;
  message_id: string;
  subject: string;
  sender: string;
  received_at: string;
  category: EmailCategory;
  confidence: number;
  status: EmailStatus;
  escalation_reason: string | null;
  langfuse_trace_id: string | null;
  created_at: string;
  postings: InboxPosting[];
  interactions: InboxInteraction[];
}

export interface PaginatedInbox {
  total: number;
  items: InboxEmail[];
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_approved: boolean;
  is_admin: boolean;
  must_change_password: boolean;
  created_at: string;
}

export interface TimelineEvent {
  id: string;
  event_type: string;
  description: string | null;
  occurred_at: string;
}

export interface JobReview {
  id: string;
  user_id: string;
  job_id: string;
  // Job fields (flattened)
  title: string;
  company: string;
  url: string;
  source: JobSource;
  location: string | null;
  remote: boolean;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string;
  description: string | null;
  date_posted: string | null;
  date_scraped: string;
  external_id: string | null;
  // AI review
  ai_score: number | null;
  ai_summary: string | null;
  ai_pros: string[] | null;
  ai_cons: string[] | null;
  skills_rank: number | null;
  experience_rank: number | null;
  location_rank: number | null;
  education_rank: number | null;
  salary_rank: number | null;
  recommended: boolean | null;
  ai_reviewed_at: string | null;
  // User tracking
  status: JobStatus;
  notes: string | null;
  date_applied: string | null;
  has_contact: boolean;
  contact_notes: string | null;
  research_summary: string | null;
  application_answers: Record<string, string> | null;
  interview_questions: InterviewQuestion[] | null;
  recruiter_id: string | null;
  recruiter_name: string | null;
  created_at: string;
  updated_at: string;
  timeline: TimelineEvent[];
}

export interface JobListResponse {
  total: number;
  items: JobReview[];
}

// ─── Recruiters ───────────────────────────────────────────────
export type RecruiterStatus = "active" | "ghosted" | "archived" | "do_not_contact";
export type RecruiterType = "agency" | "in_house";

export interface RecruiterJobBrief {
  id: string;          // UserJobReview.id
  title: string;
  company: string;
  status: JobStatus;
}

export interface Recruiter {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  title: string | null;
  employer: string | null;
  companies_represented: string[] | null;
  linkedin_url: string | null;
  type: RecruiterType | null;
  status: RecruiterStatus;
  last_contacted: string | null;   // ISO date (YYYY-MM-DD)
  notes: string | null;
  created_at: string;
  updated_at: string;
  jobs: RecruiterJobBrief[];
}

export interface RecruiterSuggestion {
  name: string;
  email: string | null;
  email_count: number;
  // Enriched from the agent's recruiter_contact card (all optional)
  phone: string | null;
  title: string | null;
  employer: string | null;
  linkedin_url: string | null;
  type: RecruiterType | null;
  companies_represented: string[] | null;
  recruiter_confidence: number | null;
}

export type WorkStyle = "remote" | "hybrid" | "onsite" | "any";

export interface ApplicationTemplate {
  label: string;
  prompt: string;
}

export interface CareerStory {
  title: string;
  content: string;
}

export interface InterviewQuestion {
  id: string;
  category: "Behavioral" | "Technical" | "Situational" | "Culture/Motivation" | "General";
  question: string;
  coaching: string;
  story_refs: string[];
  notes: string;
}

export interface Criteria {
  id: string;
  user_id: string;
  name: string;
  is_active: boolean;
  job_titles: string[] | null;
  search_locations: string[] | null;
  work_style: WorkStyle;
  home_city: string | null;
  max_commute_miles: number | null;
  min_salary: number | null;
  excluded_companies: string[] | null;
  target_companies: string[] | null;
  extra_instructions: string | null;  // deprecated — use scoring_prompt
  scoring_prompt: string | null;
  research_prompt: string | null;
  application_templates: ApplicationTemplate[] | null;
  voice_guidelines: string | null;
  interview_prep_prompt: string | null;
  resume_tailor_prompt: string | null;
  created_at: string;
  updated_at: string;
}

// ─── Résumé tailoring (Phase 2) ───────────────────────────────
export type TailorChangeType = "vocabulary" | "emphasis" | "reorder" | "factual" | "wording";
export type TailorDecision = "pending" | "accepted" | "rejected";

export interface TailorChange {
  id: string;
  path: string;
  section: string;       // summary | skills | experience | education | projects
  before: string | null;
  after: string | null;
  kind: string;          // modified | added | removed
  type: TailorChangeType;
  rationale: string;
  decision: TailorDecision;
}

export interface TailorState {
  original: Record<string, any>;
  tailored: Record<string, any>;
  changes: TailorChange[];
  status: string;
  model: string;
  generated_at: string;
  total_years: number | null;
  flagged_count: number;
  base_changed?: boolean;
}

export interface Profile {
  id: string;
  user_id: string;
  name: string;
  is_active: boolean;
  full_name: string | null;
  location: string | null;
  resume_text: string | null;
  summary: string | null;
  skills: string[] | null;
  education: string | null;
  desired_salary: number | null;
  commute_preference: string | null;
  extra_context: string | null;
  career_stories: CareerStory[] | null;
  created_at: string;
  updated_at: string;
}

export interface APIKey {
  provider: LLMProvider;
  key_hint: string;
  preferred_model?: string;
  updated_at: string;
  active?: boolean;   // the LLM key currently used (explicit selection, else priority)
}

export interface LinkedInConnection {
  id: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  company: string | null;
  position: string | null;
  connected_on: string | null;
  created_at: string;
}

export interface AdminUser extends User {}

export interface PaginatedUsers {
  total: number;
  items: AdminUser[];
}
