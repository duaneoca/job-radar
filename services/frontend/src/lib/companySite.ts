/**
 * "Find on company site": a job from an aggregator (Adzuna above all) often has
 * a dead link — they re-post ads under new ids every few days — but the same job
 * is usually still live on the company's own careers site.
 *
 * The button searches for it and remembers which job it was. When the user then
 * imports from the company's page (bookmarklet or paste), Add Job offers to
 * update that job instead of adding a second one: POST /jobs/manual?replaces=.
 * The automatic same-role match can't do this on its own, because Adzuna's
 * company names rarely match the careers page's ("NVIDIA" vs "NVIDIA Corporation").
 *
 * Kept in this origin's localStorage: the bookmarklet opens Add Job in a new tab,
 * which shares it. Per-browser convenience only, so every access is guarded.
 */

/** Mirrors dedup.AGGREGATOR_SOURCES in tracker-api. */
export const AGGREGATOR_SOURCES = new Set(["adzuna", "jsearch", "the_muse", "remotive"]);

const KEY = "jr:find-on-company-site";
/** Long enough to search, read and import; short enough that a later, unrelated
 *  Add Job isn't offered a stale match. */
const FRESH_MS = 2 * 60 * 60 * 1000;

export interface PendingReplace {
  reviewId: string;
  title: string;
  company: string;
  at: number;
}

export function companySearchUrl(title: string, company: string): string {
  return `https://www.google.com/search?q=${encodeURIComponent(`"${title}" ${company} careers`)}`;
}

/** Remember the job, then open the search in a new tab. */
export function findOnCompanySite(job: { id: string; title: string; company: string }): void {
  try {
    const pending: PendingReplace = { reviewId: job.id, title: job.title, company: job.company, at: Date.now() };
    localStorage.setItem(KEY, JSON.stringify(pending));
  } catch {
    // Storage blocked: the search still works, Add Job just won't offer the update.
  }
  window.open(companySearchUrl(job.title, job.company), "_blank", "noopener");
}

export function readPendingReplace(): PendingReplace | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as PendingReplace;
    if (!p?.reviewId || typeof p.at !== "number" || Date.now() - p.at > FRESH_MS) {
      localStorage.removeItem(KEY);
      return null;
    }
    return p;
  } catch {
    return null;
  }
}

export function clearPendingReplace(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to clear */
  }
}
