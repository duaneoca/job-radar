import { useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles, X } from "lucide-react";
import { LATEST } from "../lib/releases";
import { useApiKeyStatus } from "./ApiKeyWarning";
import { useAuthStore } from "../store/auth";

const SEEN_KEY = "jr-release-seen";

/** "There's a new version" — pointing at Help → What's new.
 *
 *  Three guards, and they are most of the point:
 *
 *  1. Released AFTER you signed up. Announcing v1.13.0 to someone who joined
 *     yesterday is noise about a version they never missed. Comparing the
 *     release date to the account's creation date answers this exactly, and
 *     avoids the usual "seed localStorage on first run" hack — which would have
 *     silently swallowed the announcement for every EXISTING user, i.e. the only
 *     people who actually wanted it.
 *
 *  2. Not while something is wrong. If a key is broken or missing, the user has
 *     a real problem on screen; release news is the wrong thing to compete with
 *     it. Same no-stacking rule LlmKeyProblemBanner follows.
 *
 *  3. Informational styling, not amber. The other two banners are warnings; a
 *     third amber bar teaches people to ignore all three.
 *
 *  Dismissal is per browser, so two devices means two dismissals. That beats a
 *  database column and an endpoint for something this small. */
export function ReleaseBanner() {
  const { user } = useAuthStore();
  const { isLoading, missingRequired, activeNeedsModel, activeKeyError } = useApiKeyStatus();
  const [seen, setSeen] = useState(() => localStorage.getItem(SEEN_KEY));

  if (!LATEST || !user || isLoading) return null;
  if (seen === LATEST.version) return null;
  if (missingRequired || activeNeedsModel || activeKeyError) return null;

  // Date-only compare: `date` is YYYY-MM-DD and created_at is a full timestamp,
  // so slice rather than parse — no timezone to get wrong.
  const joined = (user.created_at ?? "").slice(0, 10);
  if (joined && LATEST.date <= joined) return null;

  function dismiss() {
    localStorage.setItem(SEEN_KEY, LATEST!.version);
    setSeen(LATEST!.version);
  }

  return (
    <div className="border-b border-primary/25 bg-primary/5">
      <div className="container px-4 py-2.5 flex items-start gap-3 text-sm">
        <Sparkles className="h-4 w-4 text-primary shrink-0 mt-0.5" />
        <div className="flex-1 leading-relaxed">
          <span className="text-foreground">
            <span className="font-medium">{LATEST.version}</span> — {LATEST.headline}
          </span>{" "}
          <Link
            to="/help?tab=whats-new"
            onClick={dismiss}
            className="font-medium underline"
          >
            What's new
          </Link>
        </div>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss"
          className="text-muted-foreground hover:text-foreground shrink-0"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
