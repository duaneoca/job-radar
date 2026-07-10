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

export function scoreColor(score?: number | null) {
  if (score == null) return "text-muted-foreground";
  if (score >= 7) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 5) return "text-amber-600 dark:text-amber-400";
  return "text-rose-600 dark:text-rose-400";
}

export function statusBadgeVariant(status: JobStatus) {
  const map: Record<JobStatus, string> = {
    new:          "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
    reviewed:     "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    applied:      "bg-violet-100 text-violet-700 dark:bg-violet-900 dark:text-violet-300",
    dismissed:    "bg-slate-100 text-slate-400 dark:bg-slate-900 dark:text-slate-500",
    interviewing: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
    offer:        "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300",
    rejected:     "bg-rose-100 text-rose-700 dark:bg-rose-900 dark:text-rose-300",
    expired:      "bg-slate-100 text-slate-400 dark:bg-slate-900 dark:text-slate-500",
  };
  return map[status] ?? map.new;
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
  { value: "manual",      label: "Manual" },
];

export const STATUS_OPTIONS: { value: JobStatus; label: string }[] = [
  { value: "new",          label: "New" },
  { value: "reviewed",     label: "Reviewed" },
  { value: "applied",      label: "Applied" },
  { value: "interviewing", label: "Interviewing" },
  { value: "offer",        label: "Offer" },
  { value: "rejected",     label: "Rejected" },
  { value: "dismissed",    label: "Dismissed" },
];
