import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { JobStatus } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * [C2] Render-time URL guard for agent-derived links (attacker-controlled).
 * Returns the URL only if it parses as http/https; otherwise null so the caller
 * renders inert text instead of an anchor (blocks javascript:/data: etc.).
 */
export function safeHref(url?: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);   // no base → only absolute URLs with a scheme parse
    return u.protocol === "http:" || u.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

export function formatSalary(min?: number | null, max?: number | null, currency = "USD"): string {
  if (!min && !max) return "—";
  const fmt = (n: number) =>
    new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(n);
  if (min && max) return `${fmt(min)} – ${fmt(max)}`;
  if (min) return `${fmt(min)}+`;
  return fmt(max!);
}

export function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function formatAge(iso?: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "today";
  const days = Math.floor(ms / 86_400_000);
  if (days === 0) return "today";
  if (days < 7) return `${days}d`;
  if (days < 60) return `${Math.floor(days / 7)}w`;
  return `${Math.floor(days / 30)}mo`;
}

export function scoreColor(score?: number | null) {
  if (score == null) return "text-muted-foreground";
  if (score >= 7) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 5) return "text-amber-600 dark:text-amber-400";
  return "text-rose-600 dark:text-rose-400";
}

export const SOURCE_LABELS: Record<string, string> = {
  adzuna:      "Adzuna",
  the_muse:    "The Muse",
  remotive:    "Remotive",
  linkedin:    "LinkedIn",
  indeed:      "Indeed",
  glassdoor:   "Glassdoor",
  dice:        "Dice",
  builtin:     "BuiltIn",
  monster:     "Monster",
  ziprecruiter:"ZipRecruiter",
  ashby:       "Ashby",
  greenhouse:  "Greenhouse",
  lever:       "Lever",
  jsearch:     "JSearch",
  manual:      "Manual",
};

export function formatSource(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

export const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "linkedin",    label: "LinkedIn" },
  { value: "indeed",      label: "Indeed" },
  { value: "dice",        label: "Dice" },
  { value: "builtin",     label: "BuiltIn" },
  { value: "monster",     label: "Monster" },
  { value: "ziprecruiter",label: "ZipRecruiter" },
  { value: "adzuna",      label: "Adzuna" },
  { value: "the_muse",    label: "The Muse" },
  { value: "remotive",    label: "Remotive" },
  { value: "jsearch",     label: "JSearch" },
  { value: "ashby",       label: "Ashby" },
  { value: "greenhouse",  label: "Greenhouse" },
  { value: "lever",       label: "Lever" },
  { value: "manual",      label: "Manual" },
];

// Insertion order is the dropdown order. Typing this as Record<JobStatus, …> makes
// the build fail if a status is added to JobStatus without a label here: a missing
// entry leaves the Select trigger blank, which is what hid the status on ~1.1k
// Expired jobs in production.
const STATUS_LABELS: Record<JobStatus, string> = {
  new:                "New",
  reviewed:           "Reviewed",
  referral_requested: "Referral req.",
  applied:            "Applied",
  interviewing:       "Interviewing",
  offer:              "Offer",
  rejected:           "Rejected",
  dismissed:          "Dismissed",
  expired:            "Expired",
};

export const STATUS_OPTIONS: { value: JobStatus; label: string }[] =
  (Object.entries(STATUS_LABELS) as [JobStatus, string][])
    .map(([value, label]) => ({ value, label }));
