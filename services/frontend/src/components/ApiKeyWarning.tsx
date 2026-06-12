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
  return {
    isLoading,
    hasAdzuna,
    hasAI,
    hasTavily,
    // Required to use the product at all:
    missingRequired: !hasAdzuna || !hasAI,
  };
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
