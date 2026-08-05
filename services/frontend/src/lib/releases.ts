/**
 * User-facing release notes.
 *
 * Deliberately hand-written and deliberately NOT the GitHub release notes. Those
 * are generated from PR titles and exist for whoever is bisecting a regression;
 * "fix(ai-reviewer): stop the OOM loop" tells a user nothing except that
 * something they rely on was broken in a way they hadn't noticed.
 *
 * The top entry is the source of truth for "latest" — the banner reads it, and
 * the Help tab renders the list. Tagging without adding an entry therefore shows
 * nobody anything, which is the right way for this to fail: a quiet hotfix
 * shouldn't have to pretend to be news, and a forgotten entry shouldn't produce
 * an empty or wrong announcement.
 *
 * Adding a release: newest FIRST, `date` is the day it ships to production.
 * Write for someone who uses the app, not someone who works on it, and say when
 * something was broken — a release note that only lists new features while
 * quietly repairing a month of silent failures isn't honest.
 */

export interface ReleaseNote {
  /** Short, plain, in the user's terms. */
  title: string;
  /** One or two sentences. What changed for them, and what to do if anything. */
  detail: string;
}

export interface Release {
  /** Matches the git tag, e.g. "v1.13.0". Identifies the entry for dismissal. */
  version: string;
  /** ISO date (YYYY-MM-DD) it reached production. Compared against signup date
   *  so people aren't told about releases that predate their account. */
  date: string;
  /** One line for the banner. No version number — the banner adds it. */
  headline: string;
  notes: ReleaseNote[];
}

export const RELEASES: Release[] = [
  // Newest first. See the module comment before adding one.
];

/** The release the banner announces, or null when there are none yet. */
export const LATEST: Release | null = RELEASES[0] ?? null;
