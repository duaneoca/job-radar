import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { agentApi } from "../lib/api";
import { formatDate, formatDateTime } from "../lib/utils";
import type { AgentStats, AgentRunStatus, AgentHealth, AgentUserHealth } from "../lib/types";

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
  const { data: stats, isLoading, isError } = useQuery<AgentStats>({
    queryKey: ["agent-stats", scope],
    queryFn: () => agentApi.get(path).then((r) => r.data),
  });

  if (isLoading) return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
  if (isError) return <p className="text-sm text-muted-foreground">Couldn't load agent stats right now.</p>;
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
      {scope === "me" && stats.health && <HealthPanel health={stats.health} />}
      {scope === "global" && <HealthByUser />}
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

const STATE_STYLE: Record<AgentHealth["state"], { dot: string; label: string }> = {
  healthy: { dot: "bg-emerald-500", label: "Reaching Job Radar" },
  unreachable: { dot: "bg-rose-500", label: "Not reaching Job Radar" },
  never_connected: { dot: "bg-muted-foreground/40", label: "Never connected" },
};

function cadence(h: AgentHealth): string {
  if (!h.expected_interval_minutes) return "";
  const m = h.expected_interval_minutes;
  return m >= 90 ? `runs about every ${Math.round(m / 60)} h` : `runs about every ${Math.round(m)} min`;
}

/** The user's own agent: reachable or not, and recent outages. Inferred from
 *  its runs, because an agent that can't reach us can't say so. */
function HealthPanel({ health }: { health: AgentHealth }) {
  const style = STATE_STYLE[health.state];
  return (
    <div className="rounded-lg border px-4 py-3 space-y-2">
      <div className="flex items-center gap-3 text-sm">
        <span className={`h-2.5 w-2.5 rounded-full ${style.dot} shrink-0`} />
        <span className="font-medium">{style.label}</span>
        <span className="text-muted-foreground">
          {health.state === "unreachable" && health.unreachable_since
            ? `· last heard from ${formatDateTime(health.unreachable_since)}`
            : health.last_seen_at
              ? `· last run ${formatDateTime(health.last_seen_at)}`
              : ""}
          {cadence(health) && ` · ${cadence(health)}`}
        </span>
      </div>
      {health.state === "unreachable" && (
        <p className="text-xs text-muted-foreground">
          The agent hasn't reported a run for a while. If it's running on your own machine, check
          that the machine is awake and online. Runs it couldn't report are sent once it reconnects.
        </p>
      )}
      {health.periods.length > 0 && (
        <div className="text-xs text-muted-foreground space-y-0.5">
          <p className="font-medium">Last 7 days</p>
          {health.periods.slice(0, 6).map((p, i) => (
            <p key={i}>
              {p.kind === "late" ? "Ran, but couldn't reach Job Radar: " : "No runs: "}
              {formatDateTime(p.start)} – {formatDateTime(p.end)}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/** Admin: every agent user's reachability, unreachable first. A hosted user has
 *  no log of their own, so this is where their outage shows up. */
function HealthByUser() {
  const { data } = useQuery<AgentUserHealth[]>({
    queryKey: ["agent-health-users"],
    queryFn: () => agentApi.get("/agent/health/users").then((r) => r.data),
  });
  if (!data || data.length === 0) return null;
  return (
    <div className="rounded-lg border px-4 py-3">
      <p className="text-xs font-medium text-muted-foreground mb-2">Agents by user</p>
      <div className="space-y-1">
        {data.map((u) => (
          <div key={u.user_id} className="flex items-center gap-2 text-sm">
            <span className={`h-2 w-2 rounded-full ${STATE_STYLE[u.state].dot} shrink-0`} />
            <span className="truncate">{u.email}</span>
            <span className="ml-auto text-xs text-muted-foreground shrink-0">
              {u.state === "unreachable" && u.unreachable_since
                ? `silent since ${formatDateTime(u.unreachable_since)}`
                : u.state === "never_connected" ? "never connected"
                : `${u.environment ?? ""} · ${u.last_seen_at ? formatDateTime(u.last_seen_at) : ""}`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
