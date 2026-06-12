export type JobStatus =
  | "new" | "reviewed" | "applied" | "dismissed"
  | "interviewing" | "offer" | "rejected" | "expired";

export type JobSource =
  | "adzuna" | "the_muse" | "remotive" | "linkedin"
  | "indeed" | "glassdoor" | "dice" | "manual";

export type LLMProvider = "anthropic" | "openai" | "google" | "groq" | "tavily" | "adzuna";

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
  created_at: string;
  updated_at: string;
  timeline: TimelineEvent[];
}

export interface JobListResponse {
  total: number;
  items: JobReview[];
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
  created_at: string;
  updated_at: string;
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
