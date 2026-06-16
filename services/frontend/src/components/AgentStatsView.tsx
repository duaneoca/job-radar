import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { agentApi } from "../lib/api";
import { formatDate } from "../lib/utils";
import type { AgentStats, AgentRunStatus } from "../lib/types";

function RunStatusDot({ status }: { status: AgentRunStatus }) {
  const color =
    status === "success" ? "bg-emerald-500"
    : status === "partial" ? "bg-amber-500"
    : "bg-rose-500";
  return <span className={`h-2.5 w-2.5 rounded-full ${color} shrink-0`} />;
}

/** Agent business stats — per-user (`me`) or global/admin (`global`). */
export function AgentStatsView({ scope }: { scope: "me" | "global" }) {
  const path = scope === "global" ? "/agent/stats/global" : "/agent/stats";
  const { data: stats, isLoading } = useQuery<AgentStats>({
    queryKey: ["agent-stats", scope],
    queryFn: () => agentApi.get(path).then((r) => r.data),
  });

  if (isLoading) return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
  if (!stats) return null;

  const lastRun = stats.last_run;
  const cats = Object.entries(stats.category_breakdown).filter(([, n]) => n > 0);
  const tiles = [
    { label: "Emails today", value: stats.emails_today },
    { label: "This week", value: stats.emails_this_week },
    { label: "Jobs imported", value: stats.jobs_imported },
    { label: "Escalation rate", value: `${Math.round(stats.escalation_rate * 100)}%` },
  ];

  return (
    <div className="space-y-3">
      <div className="rounded-lg border px-4 py-3 flex items-center gap-3">
        {lastRun ? (
          <>
            <RunStatusDot status={lastRun.status} />
            <div className="text-sm">
              <span className="font-medium">Last run: {lastRun.status}</span>
              <span className="text-muted-foreground">
                {" · "}{lastRun.finished_at ? formatDate(lastRun.finished_at) : "running"}
                {" · "}{lastRun.environment}{" · "}{lastRun.emails_processed} emails
              </span>
            </div>
          </>
        ) : (
          <span className="text-sm text-muted-foreground">No agent runs yet.</span>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-lg border px-3 py-2">
            <div className="text-lg font-semibold tabular-nums">{t.value}</div>
            <div className="text-xs text-muted-foreground">{t.label}</div>
          </div>
        ))}
      </div>

      {cats.length > 0 && (
        <div className="rounded-lg border px-4 py-3">
          <p className="text-xs font-medium text-muted-foreground mb-2">By category</p>
          <div className="space-y-1">
            {cats.map(([cat, n]) => (
              <div key={cat} className="flex justify-between text-sm">
                <span className="capitalize">{cat.replace(/_/g, " ")}</span>
                <span className="tabular-nums text-muted-foreground">{n}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
