import { useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Wand2, Loader2, Sparkles, AlertTriangle, Check, X, RefreshCw, Printer, Crosshair, Briefcase } from "lucide-react";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Textarea } from "../components/ui/textarea";
import { jobsApi } from "../lib/api";
import { cn } from "../lib/utils";
import { toast } from "../hooks/useToast";
import type { JobReview, TailorState, TailorChange, TailorDecision } from "../lib/types";

// ─── Rendered résumé (plain structured text — template/PDF is Phase 3) ──────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide font-semibold text-muted-foreground border-b mb-1 pb-0.5">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

// data-path values mirror the backend diff paths so a change can locate its element.
function ResumeView({ data }: { data: any }) {
  if (!data) return null;
  return (
    <div className="space-y-3 text-xs leading-relaxed">
      {data.summary && <Section title="Summary"><p data-path="summary">{data.summary}</p></Section>}
      {data.skills?.length > 0 && (
        <Section title="Skills">
          {data.skills.map((g: any, i: number) => (
            <p key={i}><b data-path={`skills/${i}/label`}>{g.label}:</b> <span data-path={`skills/${i}/items`}>{(g.items ?? []).join(" · ")}</span></p>
          ))}
        </Section>
      )}
      {data.experience?.length > 0 && (
        <Section title="Experience">
          {data.experience.map((e: any, i: number) => (
            <div key={i} className="mb-2">
              <div className="font-medium">
                <span data-path={`experience/${i}/company`}>{e.company}</span>{" "}
                {(e.start || e.end) && (
                  <span className="text-muted-foreground font-normal">· <span data-path={`experience/${i}/start`}>{e.start}</span>–<span data-path={`experience/${i}/end`}>{e.end}</span></span>
                )}
              </div>
              {e.titles?.length > 0 && <div className="italic text-muted-foreground" data-path={`experience/${i}/titles`}>{e.titles.join(" → ")}</div>}
              {e.bullets?.length > 0 && <ul className="list-disc ml-4">{e.bullets.map((b: string, j: number) => <li key={j} data-path={`experience/${i}/bullets/${j}`}>{b}</li>)}</ul>}
              {e.phases?.map((p: any, k: number) => (
                <div key={k} className="mt-1">
                  {p.label && <div className="font-medium">{p.label}</div>}
                  <ul className="list-disc ml-4">{(p.bullets ?? []).map((b: string, j: number) => <li key={j} data-path={`experience/${i}/phases/${k}/bullets/${j}`}>{b}</li>)}</ul>
                </div>
              ))}
              {e.notable?.length > 0 && <p className="text-muted-foreground mt-0.5">Notable: {e.notable.map((n: string, j: number) => <span key={j} data-path={`experience/${i}/notable/${j}`}>{n}{j < e.notable.length - 1 ? ", " : ""}</span>)}</p>}
            </div>
          ))}
        </Section>
      )}
      {data.education?.length > 0 && (
        <Section title="Education">
          {data.education.map((ed: any, i: number) => (
            <p key={i}><span data-path={`education/${i}/degree`}>{ed.degree}</span>{ed.school && <> · <span data-path={`education/${i}/school`}>{ed.school}</span></>}</p>
          ))}
        </Section>
      )}
      {data.projects?.length > 0 && (
        <Section title="Projects">
          {data.projects.map((pr: any, i: number) => (
            <div key={i}>
              {pr.title && <div className="font-medium" data-path={`projects/${i}/title`}>{pr.title}</div>}
              <ul className="list-disc ml-4">{(pr.bullets ?? []).map((b: string, j: number) => <li key={j} data-path={`projects/${i}/bullets/${j}`}>{b}</li>)}</ul>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

// ─── Change card ────────────────────────────────────────────────────────────────

function ChangeCard({ c, onDecide, onLocate, busy }: {
  c: TailorChange; onDecide: (id: string, d: TailorDecision) => void; onLocate: (path: string) => void; busy: boolean;
}) {
  const flagged = c.type === "factual";
  return (
    <div className={cn(
      "rounded-md border p-2.5 text-xs space-y-1.5",
      flagged && "border-amber-400/60 bg-amber-50/40 dark:bg-amber-950/20",
      c.decision === "rejected" && "opacity-60",
    )}>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="text-[10px] capitalize">{c.section}</Badge>
        <Badge variant="outline" className={cn("text-[10px] capitalize", flagged ? "border-amber-500/40 text-amber-700 dark:text-amber-400" : "text-muted-foreground")}>{c.type}</Badge>
        {flagged && <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-700 dark:text-amber-400"><AlertTriangle className="h-3 w-3" />review carefully</span>}
        <button
          type="button"
          title="Show where this is in the résumé"
          onClick={() => onLocate(c.path)}
          className="ml-auto inline-flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-foreground"
        >
          <Crosshair className="h-3 w-3" /> locate
        </button>
        {c.decision !== "pending" && (
          <Badge className={cn("text-[10px]", c.decision === "accepted" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" : "bg-rose-500/15 text-rose-700 dark:text-rose-400")}>{c.decision}</Badge>
        )}
      </div>

      {c.trigger && (
        <div className="flex items-start gap-1 rounded bg-blue-500/10 px-1.5 py-1 text-[11px] text-blue-800 dark:text-blue-300">
          <Briefcase className="h-3 w-3 shrink-0 mt-0.5" />
          <span>Posting asks: <span className="italic">“{c.trigger}”</span></span>
        </div>
      )}

      {c.before && <p className="text-rose-600 dark:text-rose-400 line-through decoration-rose-400/50">{c.before}</p>}
      {c.after && <p className="text-emerald-700 dark:text-emerald-400">{c.after}</p>}
      {c.rationale && <p className="text-muted-foreground italic">{c.rationale}</p>}
      <div className="flex gap-1.5 pt-0.5">
        <Button size="sm" variant={c.decision === "accepted" ? "default" : "outline"} className="h-7 px-2" disabled={busy} onClick={() => onDecide(c.id, "accepted")}>
          <Check className="h-3 w-3 mr-1" /> Accept
        </Button>
        <Button size="sm" variant={c.decision === "rejected" ? "destructive" : "outline"} className="h-7 px-2" disabled={busy} onClick={() => onDecide(c.id, "rejected")}>
          <X className="h-3 w-3 mr-1" /> Reject
        </Button>
      </div>
    </div>
  );
}

// ─── Page ───────────────────────────────────────────────────────────────────────

export function TailorReviewPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const [instruction, setInstruction] = useState("");

  const { data: job } = useQuery<JobReview>({
    queryKey: ["job", id],
    queryFn: () => jobsApi.get(`/jobs/${id}`).then((r) => r.data),
    enabled: !!id,
  });

  const { data: state, isLoading } = useQuery<TailorState | null>({
    queryKey: ["tailor", id],
    queryFn: () => jobsApi.get(`/jobs/${id}/tailor-resume`).then((r) => r.data).catch(() => null),
    enabled: !!id,
  });

  const tailorMut = useMutation({
    mutationFn: () => jobsApi.post(`/jobs/${id}/tailor-resume`).then((r) => r.data),
    onSuccess: (data) => qc.setQueryData(["tailor", id], data),
    onError: (e: any) => toast({ title: "Tailoring failed", description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const refineMut = useMutation({
    mutationFn: () => jobsApi.post(`/jobs/${id}/tailor-resume/refine`, { instruction }).then((r) => r.data),
    onSuccess: (data) => { qc.setQueryData(["tailor", id], data); setInstruction(""); },
    onError: (e: any) => toast({ title: "Refine failed", description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const decideMut = useMutation({
    mutationFn: (vars: { id: string; decision: TailorDecision }) =>
      jobsApi.patch(`/jobs/${id}/tailor-resume/decisions`, { decisions: { [vars.id]: vars.decision } }).then((r) => r.data),
    onSuccess: (data) => qc.setQueryData(["tailor", id], data),
    onError: () => toast({ title: "Couldn't save decision", variant: "destructive" }),
  });

  const busy = tailorMut.isPending || refineMut.isPending;

  // Scroll both panes to a change's element and flash it.
  const origRef = useRef<HTMLDivElement>(null);
  const tailRef = useRef<HTMLDivElement>(null);
  function locate(path: string) {
    let found = false;
    for (const ref of [origRef, tailRef]) {
      const el = ref.current?.querySelector(`[data-path="${CSS.escape(path)}"]`) as HTMLElement | null;
      if (!el) continue;
      found = true;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.remove("rt-flash");
      void el.offsetWidth;            // restart the animation
      el.classList.add("rt-flash");
      setTimeout(() => el.classList.remove("rt-flash"), 1600);
    }
    if (!found) toast({ title: "Couldn't locate that change in the résumé" });
  }

  return (
    <div className="max-w-[1400px] mx-auto p-4 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" asChild className="-ml-1">
          <Link to={`/jobs/${id}`}><ArrowLeft className="h-4 w-4 mr-1" /> Back to job</Link>
        </Button>
        <div className="flex items-center gap-2">
          <Wand2 className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-bold">Tailor résumé</h1>
        </div>
        {job && <span className="text-sm text-muted-foreground">— {job.title} · {job.company}</span>}
        {state && (
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" disabled={busy} onClick={() => tailorMut.mutate()}>
              {tailorMut.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
              Re-tailor
            </Button>
            <Button size="sm" onClick={() => window.open(`/jobs/${id}/tailor/print`, "_blank", "noopener,noreferrer")}>
              <Printer className="h-4 w-4 mr-1" /> Download PDF
            </Button>
          </div>
        )}
      </div>

      {state?.base_changed && (
        <div className="flex items-start gap-2 rounded-md border border-amber-300/60 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900 px-3 py-2 text-xs">
          <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
          <span>Your base résumé changed since this was tailored. Re-tailor to refresh.</span>
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
      ) : !state ? (
        // Empty state — generate.
        <div className="rounded-lg border border-dashed p-10 text-center space-y-3 max-w-lg mx-auto">
          <Wand2 className="h-7 w-7 mx-auto text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            Tailor your résumé to <span className="font-medium text-foreground">{job?.title}</span> — honestly.
            Every change is shown for your approval.
          </p>
          <Button onClick={() => tailorMut.mutate()} disabled={busy}>
            {tailorMut.isPending ? <><Loader2 className="h-4 w-4 animate-spin mr-1" />Tailoring…</> : <><Sparkles className="h-4 w-4 mr-1" />Tailor résumé</>}
          </Button>
        </div>
      ) : (
        <>
          {/* Summary line */}
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium">{state.changes.length} change{state.changes.length === 1 ? "" : "s"}</span>
            {state.flagged_count > 0 && (
              <Badge variant="outline" className="border-amber-500/40 text-amber-700 dark:text-amber-400 gap-1">
                <AlertTriangle className="h-3 w-3" /> {state.flagged_count} touch a factual claim — review
              </Badge>
            )}
            <span className="text-xs text-muted-foreground">Accept or reject each; rejected changes keep your original wording.</span>
          </div>

          {/* flash highlight used by the locate buttons */}
          <style>{`@keyframes rtflash{0%{background:rgba(59,130,246,.35)}100%{background:transparent}}.rt-flash{animation:rtflash 1.5s ease-out;border-radius:3px}`}</style>

          {/* 3-pane: original | changes | tailored */}
          <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr_1fr]">
            <div className="rounded-lg border bg-card p-3">
              <div className="text-xs font-semibold text-muted-foreground mb-2">Original</div>
              <div ref={origRef}><ResumeView data={state.original} /></div>
            </div>

            <div className="space-y-2">
              <div className="text-xs font-semibold text-muted-foreground">Changes</div>
              {state.changes.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4">No changes — the AI left your résumé as-is for this posting.</p>
              ) : (
                state.changes.map((c) => (
                  <ChangeCard key={c.id} c={c} busy={decideMut.isPending} onLocate={locate} onDecide={(cid, d) => decideMut.mutate({ id: cid, decision: d })} />
                ))
              )}
            </div>

            <div className="rounded-lg border bg-card p-3">
              <div className="text-xs font-semibold text-muted-foreground mb-2">Tailored</div>
              <div ref={tailRef}><ResumeView data={state.tailored} /></div>
            </div>
          </div>

          {/* Refine */}
          <div className="rounded-lg border bg-muted/30 p-3 space-y-2 max-w-2xl">
            <p className="text-xs font-medium">Refine</p>
            <p className="text-xs text-muted-foreground">
              Tell the AI what to adjust (it keeps your rejected wording and stays within the honesty rules).
            </p>
            <div className="flex gap-2">
              <Textarea
                rows={2}
                className="text-sm"
                placeholder="e.g. emphasize cloud architecture; leave the summary alone"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
              />
              <Button disabled={busy || !instruction.trim()} onClick={() => refineMut.mutate()}>
                {refineMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Refine"}
              </Button>
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            PDF export and template rendering are coming next — this view confirms the tailored content.
          </p>
        </>
      )}
    </div>
  );
}
