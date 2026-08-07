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
  {
    version: "v1.13.2",
    date: "2026-08-07",
    headline: "Scoring works again if you use Claude, and long AI answers no longer time out.",
    notes: [
      {
        title: "Job scoring is fixed for Anthropic keys",
        detail:
          "Last week's release broke scoring for anyone using an Anthropic (Claude) key — Job " +
          "Radar was asking Claude for its answer in a way that made it reply with the right " +
          "kind of data but the wrong fields, so nothing could be scored. If your jobs stopped " +
          "getting scores on August 5th, that's why. It's fixed, and unscored jobs should start " +
          "filling in. Other providers were unaffected.",
      },
      {
        title: "Long AI answers aren't cut off any more",
        detail:
          "Interview prep and other generation could fail with \"Generation failed\" after about " +
          "a minute, even when the answer had finished and been saved correctly — refreshing the " +
          "page would show work that had been reported as an error. Requests now have the time " +
          "they need.",
      },
    ],
  },
  {
    version: "v1.13.0",
    date: "2026-08-05",
    headline: "Job scoring is fixed, and your LinkedIn connections are now a searchable table.",
    notes: [
      {
        title: "Job scoring is working again",
        detail:
          "Scoring had stopped for some accounts. The service that scores jobs was running out " +
          "of memory and restarting, and some AI models were replying with commentary instead of " +
          "the exact format Job Radar needs. Both are fixed. Jobs that have been sitting unscored " +
          "should start filling in.",
      },
      {
        title: "You'll be told when your AI model can't be used",
        detail:
          "If your provider rejects your model, rate-limits you, or returns answers that can't be " +
          "read as a score, you now get a notice instead of jobs quietly going unscored. Scoring " +
          "pauses rather than repeatedly spending your quota on answers it can't use.",
      },
      {
        title: "Interview prep works with your writing skills",
        detail:
          "Attaching a detailed writing skill to interview prep made it fail — the answer ran " +
          "past an internal limit and was thrown away, reported as \"AI returned malformed " +
          "JSON\". There's now room for a full set of questions with your skills applied, and " +
          "if a response is ever cut short you'll be told that plainly instead of it being " +
          "blamed on your model.",
      },
      {
        title: "Writing skills are used in full",
        detail:
          "Long writing skills were being trimmed to their first few thousand characters before " +
          "reaching the AI on some features, so a detailed style guide was only partly in force. " +
          "The whole skill is now sent.",
      },
      {
        title: "Your connections are a real table now",
        detail:
          "Under Jobs → Connections. Sort by name, company, position or when you connected, and " +
          "search across every column. Profile links from LinkedIn's export are included — " +
          "re-upload your Connections.csv to fill them in, as they weren't captured before.",
      },
      {
        title: "See who you know where you're applying",
        detail:
          "A Job checkbox marks any connection whose employer has a role on your list, and " +
          "clicking the company name jumps to those jobs.",
      },
      {
        title: "Recruiters moved",
        detail:
          "Now a tab alongside Jobs and Connections instead of a link in the top bar.",
      },
    ],
  },
];

/** The release the banner announces, or null when there are none yet. */
export const LATEST: Release | null = RELEASES[0] ?? null;
