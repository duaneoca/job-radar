import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, X } from "lucide-react";
import { keysApi } from "../lib/api";
import type { APIKey, LLMProvider } from "../lib/types";
import { useAuthStore } from "../store/auth";

const AI_PROVIDERS: LLMProvider[] = ["anthropic", "openai", "google", "groq"];
const DISMISS_KEY = "jr-keys-banner-dismissed";

/** Shared key-status hook. Reuses the ["keys"] query so adding a key in
 *  Settings updates the banner + nav indicator immediately. */
export function useApiKeyStatus() {
  const { user } = useAuthStore();
  const { data: keys = [], isLoading } = useQuery<APIKey[]>({
    queryKey: ["keys"],
    queryFn: () => keysApi.get("/keys").then((r) => r.data),
    enabled: !!user,
  });
  const have = new Set(keys.map((k) => k.provider));
  const hasAdzuna = have.has("adzuna");
  const hasAI = AI_PROVIDERS.some((p) => have.has(p));
  const hasTavily = have.has("tavily");

  // The key AI features actually use. The backend marks it, so this always agrees
  // with what scoring and the agent do.
  const activeKey = keys.find((k) => k.active) ?? null;

  return {
    isLoading,
    keys,
    hasAdzuna,
    hasAI,
    hasTavily,
    activeKey,
    // Job Radar picks no model on the user's behalf, so a key without one is a
    // broken key, not a defaulted one.
    activeNeedsModel: !!activeKey && !activeKey.preferred_model,
    activeKeyError: activeKey?.last_error_kind ?? null,
    // Required to use the product at all:
    missingRequired: !hasAdzuna || !hasAI,
  };
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  groq: "Groq",
};

/** Banner for an AI key that exists but cannot be used: no model chosen, or the
 *  provider rejected the model/key. Both are the user's to fix — nothing here is
 *  reported to the operator.
 *
 *  Deliberately hidden while MissingKeysBanner is showing: two stacked amber bars
 *  read as one broken page rather than two problems. */
export function LlmKeyProblemBanner() {
  const { isLoading, activeKey, activeNeedsModel, activeKeyError, missingRequired } =
    useApiKeyStatus();

  // Re-dismissable when the *nature* of the problem changes, so dismissing "pick
  // a model" doesn't also silence a later "that model was rejected".
  const problem = activeNeedsModel ? "no_model" : activeKeyError;
  const dismissKey = `jr-llm-key-banner:${activeKey?.provider ?? ""}:${problem ?? ""}:${
    activeKey?.preferred_model ?? ""
  }`;
  const [dismissedKey, setDismissedKey] = useState(() =>
    sessionStorage.getItem("jr-llm-key-banner-dismissed")
  );

  if (isLoading || missingRequired || !activeKey || !problem) return null;
  if (dismissedKey === dismissKey) return null;

  const provider = PROVIDER_LABELS[activeKey.provider] ?? activeKey.provider;

  let message: string;
  if (problem === "no_model") {
    message = `Your ${provider} key has no model selected, so AI features can't run. Job Radar doesn't pick one for you — that's a cost decision on your own account.`;
  } else if (problem === "invalid_key") {
    message = `${provider} rejected your API key, so AI features aren't running.`;
  } else if (problem === "provider_unavailable") {
    // Not throttling, and not a misconfiguration. Naming the wrong cause would
    // send them to change a quota that was never the problem.
    message = `${provider} isn't responding, so some jobs went unscored. Scoring retries on its own — nothing to change unless this keeps up.`;
  } else if (problem === "rate_limited") {
    // Deliberately different advice from the two rejections: nothing is broken
    // and nothing needs changing if they're willing to wait. Saying "choose a
    // model" here would send them to fix a setting that is already correct.
    message = `${provider} is rate-limiting Job Radar, so some jobs went unscored. Scoring retries on its own — if it keeps happening, raise your plan's quota or pick a cheaper model.`;
  } else {
    message = `${provider} rejected the model "${activeKey.preferred_model}" — it has most likely been retired. AI features aren't running until you choose another.`;
  }

  function dismiss() {
    sessionStorage.setItem("jr-llm-key-banner-dismissed", dismissKey);
    setDismissedKey(dismissKey);
  }

  return (
    <div className="border-b border-amber-500/30 bg-amber-500/10">
      <div className="container px-4 py-2.5 flex items-start gap-3 text-sm">
        <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-500 shrink-0 mt-0.5" />
        <div className="flex-1 leading-relaxed">
          <span className="text-foreground">{message}</span>{" "}
          <Link to="/settings?tab=keys" className="font-medium underline">
            {problem === "rate_limited" || problem === "provider_unavailable"
              ? "Review your AI key"
              : "Choose a model"}
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

/** Dismissible banner shown across pages when required keys are missing.
 *  Dismissal is per browser session (re-appears next login until resolved). */
export function MissingKeysBanner() {
  const { isLoading, hasAdzuna, hasAI, hasTavily, missingRequired } = useApiKeyStatus();
  const [dismissed, setDismissed] = useState(
    () => sessionStorage.getItem(DISMISS_KEY) === "1"
  );

  if (isLoading || dismissed || !missingRequired) return null;

  const missing: string[] = [];
  if (!hasAdzuna) missing.push("an Adzuna key (job source)");
  if (!hasAI) missing.push("an AI key (job scoring)");
  const recommend = !hasTavily;

  function dismiss() {
    sessionStorage.setItem(DISMISS_KEY, "1");
    setDismissed(true);
  }

  return (
    <div className="border-b border-amber-500/30 bg-amber-500/10">
      <div className="container px-4 py-2.5 flex items-start gap-3 text-sm">
        <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-500 shrink-0 mt-0.5" />
        <div className="flex-1 leading-relaxed">
          <span className="text-foreground">
            To start finding jobs you need {missing.join(" and ")}.
          </span>{" "}
          {recommend && (
            <span className="text-muted-foreground">
              A Tavily key is also recommended for company research.{" "}
            </span>
          )}
          <Link to="/settings?tab=keys" className="font-medium underline">
            Add keys
          </Link>
          {" · "}
          <Link to="/help?tab=keys" className="font-medium underline">
            Where to get them
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
