import { useEffect, useMemo, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Loader2, Radar, ShieldCheck } from "lucide-react";
import { Button } from "../components/ui/button";
import { authApi } from "../lib/api";
import { loginUrl } from "../lib/loginRedirect";
import { useAuthStore } from "../store/auth";
import type { OAuthAuthorizeContext } from "../lib/types";

/**
 * The OAuth consent page. An AI assistant (Claude, ChatGPT, Gemini, Grok)
 * sends the user here; the server validates who is asking and we show it.
 * Allow / Deny go to the API, which answers with where to send the browser.
 *
 * Two rules the layout follows, from the MCP authorization spec:
 *  - the trustworthy name is the vendor derived from the redirect host, which
 *    the server computed; what the client called itself is shown second;
 *  - errors found before the redirect address was verified stay on this page.
 */
export function OAuthAuthorizePage() {
  const location = useLocation();
  const { isAuthenticated } = useAuthStore();
  const params = useMemo(() => Object.fromEntries(new URLSearchParams(location.search)), [location.search]);
  const [deciding, setDeciding] = useState<"allow" | "deny" | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const ctx = useQuery<OAuthAuthorizeContext, any>({
    queryKey: ["oauth-context", location.search],
    queryFn: () => authApi.get("/oauth/authorize/context", { params }).then((r) => r.data),
    enabled: isAuthenticated() && !!params.client_id,
    retry: false,
  });

  // A protocol error the server could safely hand back to the client: go.
  useEffect(() => {
    const to = ctx.error?.response?.data?.detail?.redirect_to;
    if (typeof to === "string") window.location.assign(to);
  }, [ctx.error]);

  if (!isAuthenticated()) {
    return <Navigate to={loginUrl(location)} replace />;
  }

  async function decide(decision: "allow" | "deny") {
    setDeciding(decision);
    setFailure(null);
    try {
      const r = await authApi.post("/oauth/authorize/decide", { params, decision });
      window.location.assign(r.data.redirect_to);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (typeof detail?.redirect_to === "string") { window.location.assign(detail.redirect_to); return; }
      setFailure(detail?.error_description ?? (typeof detail === "string" ? detail : "Something went wrong."));
      setDeciding(null);
    }
  }

  const errorDetail = ctx.error?.response?.data?.detail;
  const pageError = !params.client_id
    ? "This page is only reached from an AI assistant asking to connect to Job Radar."
    : errorDetail && !errorDetail.redirect_to
      ? (errorDetail.error_description ?? "This request can't be completed.")
      : null;

  return (
    <div className="min-h-screen bg-background flex items-start sm:items-center justify-center px-4 py-10">
      <div className="w-full max-w-md space-y-6">
        <div className="flex items-center gap-2 font-bold text-primary justify-center">
          <Radar className="h-5 w-5" /> Job Radar
        </div>

        {pageError ? (
          <div className="rounded-lg border p-5 space-y-3">
            <div className="flex items-start gap-2 text-sm">
              <AlertTriangle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
              <span>{pageError}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              Nothing was connected. You can close this tab.
            </p>
          </div>
        ) : ctx.isLoading || !ctx.data ? (
          <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : (
          <div className="rounded-lg border p-5 space-y-5">
            <div className="space-y-1">
              <h1 className="text-lg font-semibold flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-primary" />
                Connect {ctx.data.vendor_label} to your Job Radar account?
              </h1>
              <p className="text-sm text-muted-foreground">
                {ctx.data.client_name && ctx.data.client_name !== ctx.data.vendor_label.split(" (")[0]
                  ? <>It calls itself “{ctx.data.client_name}”. </> : null}
                After you allow it, you'll be sent back to <span className="font-medium text-foreground">{ctx.data.redirect_host}</span>.
              </p>
            </div>

            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">It will be able to</p>
              <ul className="space-y-1.5 text-sm">
                {ctx.data.scopes.map((s) => (
                  <li key={s.id} className="flex items-start gap-2">
                    <Check className="h-4 w-4 text-primary shrink-0 mt-0.5" />
                    <span>{s.label}</span>
                  </li>
                ))}
              </ul>
            </div>

            <p className="text-xs text-muted-foreground">
              It can't see your API keys, change your settings, or delete anything. Research and
              tailoring it starts bill your own AI key, as they do here. You can disconnect it any
              time in Settings → Connected apps.
            </p>

            {failure && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/40 px-3 py-2 text-xs">
                <AlertTriangle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
                <span>{failure}</span>
              </div>
            )}

            <div className="flex gap-2 justify-end">
              <Button variant="outline" disabled={!!deciding} onClick={() => decide("deny")}>
                {deciding === "deny" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Don't allow"}
              </Button>
              <Button disabled={!!deciding} onClick={() => decide("allow")}>
                {deciding === "allow" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Allow"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
