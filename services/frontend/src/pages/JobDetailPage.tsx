import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, ExternalLink, Star, Building2, MapPin, DollarSign,
  UserCheck, Users, Loader2, Calendar, Sparkles, MessageSquarePlus, Plus, AlertTriangle,
  Send, Trash2, Wand2, FileText,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Separator } from "../components/ui/separator";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
  DialogDescription, DialogFooter,
} from "../components/ui/dialog";
import { jobsApi, criteriaApi } from "../lib/api";
import { formatDate, formatSalary, formatSource, scoreColor, STATUS_OPTIONS } from "../lib/utils";
import { toast } from "../hooks/useToast";
import type { JobReview, Criteria, ApplicationTemplate, InterviewQuestion, LinkedInConnection, TailorState } from "../lib/types";
import { useState } from "react";
import { RefinementDrawer } from "../components/RefinementDrawer";
import { InterviewPrepCard } from "../components/InterviewPrepCard";

const DEFAULT_APP_TEMPLATES: ApplicationTemplate[] = [
  {
    label: "Cover Letter",
    prompt: "Write a compelling cover letter for this position (under 300 words). Reference specific details from the job description and draw on relevant experience from the resume. Be genuine and specific, avoid generic phrases.",
  },
  {
    label: "Why do you want to work here?",
    prompt: "Write 2-3 sentences explaining why the candidate wants to work at this specific company in this role. Focus on genuine alignment between their background/goals and the company's mission or product.",
  },
  {
    label: "About me",
    prompt: "Write a 2-3 sentence professional summary tailored for this specific application, highlighting the most relevant skills and experience from the resume.",
  },
];

// ─── Timeline tab ─────────────────────────────────────────────────────────────

const EVENT_LABELS: Record<string, string> = {
  status_change: "Status update",
  ai_review:     "AI review",
  note:          "Note",
};

function TimelineTab({ reviewId, events }: { reviewId: string; events: import("../lib/types").TimelineEvent[] }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  async function addNote() {
    if (!note.trim()) return;
    setSaving(true);
    try {
      await jobsApi.post(`/jobs/${reviewId}/timeline/note`, null, { params: { note: note.trim() } });
      qc.invalidateQueries({ queryKey: ["job", reviewId] });
      setNote("");
    } catch {
      toast({ title: "Failed to save note", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4 max-w-lg">
      {/* Event list */}
      <div className="relative">
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">No events yet.</p>
        ) : (
          <ol className="space-y-0">
            {[...events].reverse().map((ev, idx) => (
              <li key={ev.id} className="flex gap-3">
                {/* Spine */}
                <div className="flex flex-col items-center">
                  <div className="mt-1 h-2 w-2 rounded-full bg-primary shrink-0" />
                  {idx < events.length - 1 && <div className="w-px flex-1 bg-border mt-1" />}
                </div>
                {/* Content */}
                <div className="pb-4 min-w-0">
                  <p className="text-xs text-muted-foreground">
                    {EVENT_LABELS[ev.event_type] ?? ev.event_type} ·{" "}
                    {formatDate(ev.occurred_at)}
                  </p>
                  {ev.description && (
                    <p className="text-sm mt-0.5">{ev.description}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>

      <Separator />

      {/* Add note */}
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Add a note</p>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            placeholder="Phone screen scheduled, follow-up sent…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") addNote(); }}
          />
          <Button size="sm" onClick={addNote} disabled={saving || !note.trim()}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ─── Tailor résumé tab (launchpad — the workspace is the /tailor pop-out) ──────

function TailorTab({ reviewId }: { reviewId: string }) {
  const { data: state, isLoading } = useQuery<TailorState | null>({
    queryKey: ["tailor", reviewId],
    // 404 = not tailored yet → treat as null rather than an error.
    queryFn: () =>
      jobsApi.get(`/jobs/${reviewId}/tailor-resume`).then((r) => r.data).catch(() => null),
  });

  function openTailor() {
    window.open(`/jobs/${reviewId}/tailor`, "_blank", "noopener,noreferrer");
  }

  if (isLoading) {
    return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="max-w-lg space-y-4">
      <div className="flex items-start gap-2">
        <Wand2 className="h-5 w-5 text-primary shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-medium">Tailor your résumé to this job</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Realigns your real skills and wording to the posting — honestly. Every change is shown
            in a side-by-side diff you approve. Opens in a new tab.
          </p>
        </div>
      </div>

      {state?.base_changed && (
        <div className="flex items-start gap-2 rounded-md border border-amber-300/60 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900 px-3 py-2 text-xs">
          <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
          <span>Your résumé changed since this was tailored — re-tailor to refresh.</span>
        </div>
      )}

      {state ? (
        <div className="rounded-lg border bg-card p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <FileText className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">Tailored for this role</span>
            <span className="text-muted-foreground">· {state.changes.length} change{state.changes.length === 1 ? "" : "s"}</span>
            {state.flagged_count > 0 && (
              <Badge variant="outline" className="border-amber-500/40 text-amber-700 dark:text-amber-400">
                {state.flagged_count} flagged
              </Badge>
            )}
          </div>
          <div className="flex gap-2 pt-1">
            <Button size="sm" onClick={openTailor}>
              <ExternalLink className="h-4 w-4 mr-1" /> Open review
            </Button>
          </div>
        </div>
      ) : (
        <Button onClick={openTailor}>
          <Wand2 className="h-4 w-4 mr-1" /> Open résumé tailor →
        </Button>
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: job, isLoading } = useQuery<JobReview>({
    queryKey: ["job", id],
    queryFn: () => jobsApi.get(`/jobs/${id}`).then((r) => r.data),
    enabled: !!id,
  });

  const { data: contacts = [] } = useQuery<LinkedInConnection[]>({
    queryKey: ["job-contacts", id],
    queryFn: () => jobsApi.get(`/jobs/${id}/contacts`).then((r) => r.data),
    enabled: !!id,
  });

  const { data: criteria } = useQuery<Criteria>({
    queryKey: ["criteria"],
    queryFn: () => criteriaApi.get("/criteria").then((r) => r.data),
  });

  const [notesDraft, setNotesDraft] = useState<string | null>(null);
  const notes = notesDraft ?? job?.notes ?? "";

  // Research tab state
  const [researchSummary, setResearchSummary] = useState<string | null>(null);
  const [generatingResearch, setGeneratingResearch] = useState(false);

  // Application tab state — local overrides for generated/edited answers
  const [answerDrafts, setAnswerDrafts] = useState<Record<number, string>>({});
  const [generating, setGenerating] = useState<Record<number, boolean>>({});

  // Refinement drawer state
  const [refinement, setRefinement] = useState<{ open: boolean; templateIdx: number }>({
    open: false,
    templateIdx: 0,
  });

  // Interview prep state
  const [prepQuestions, setPrepQuestions] = useState<InterviewQuestion[] | null>(null);
  const [generatingPrep, setGeneratingPrep] = useState(false);
  const [confirmRegenerate, setConfirmRegenerate] = useState(false);
  const [prepHasChanges, setPrepHasChanges] = useState(false);

  const [scoreBreakdownOpen, setScoreBreakdownOpen] = useState(false);

  const updateReview = useMutation({
    mutationFn: (patch: Partial<JobReview>) => jobsApi.patch(`/jobs/${id}`, patch).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["job", id] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const deleteReview = useMutation({
    mutationFn: () => jobsApi.delete(`/jobs/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      navigate("/jobs");
      toast({ title: "Job deleted" });
    },
    onError: () => toast({ title: "Failed to delete job", variant: "destructive" }),
  });

  function handleDeleteJob() {
    if (!job) return;
    if (!confirm(`Delete "${job.title}" from your list? This cannot be undone.`)) return;
    deleteReview.mutate();
  }

  async function saveNotes() {
    try {
      await updateReview.mutateAsync({ notes });
      setNotesDraft(null);
      toast({ title: "Notes saved" });
    } catch {
      toast({ title: "Failed to save", variant: "destructive" });
    }
  }

  async function changeStatus(status: string) {
    try {
      await updateReview.mutateAsync({ status: status as any });
    } catch {
      toast({ title: "Failed to update status", variant: "destructive" });
    }
  }

  function apiErrorMessage(err: any): string {
    // FastAPI detail string or array
    const detail = err?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d: any) => d.msg ?? JSON.stringify(d)).join("; ");
    // Axios / fetch network error
    if (err?.message) return err.message;
    return "Unexpected error — check the browser console for details.";
  }

  async function generateResearch() {
    setGeneratingResearch(true);
    try {
      const res = await jobsApi.post(`/jobs/${id}/research`);
      setResearchSummary(res.data.summary);
      qc.invalidateQueries({ queryKey: ["job", id] });
    } catch (err: any) {
      console.error("Research generation error:", err?.response ?? err);
      toast({
        title: "Research generation failed",
        description: apiErrorMessage(err),
        variant: "destructive",
      });
    } finally {
      setGeneratingResearch(false);
    }
  }

  async function generateAnswer(templateIdx: number) {
    setGenerating((g) => ({ ...g, [templateIdx]: true }));
    try {
      const res = await jobsApi.post(`/jobs/${id}/application/${templateIdx}`);
      setAnswerDrafts((d) => ({ ...d, [templateIdx]: res.data.answer }));
    } catch (err: any) {
      console.error("Application generation error:", err?.response ?? err);
      toast({
        title: "Generation failed",
        description: apiErrorMessage(err),
        variant: "destructive",
      });
    } finally {
      setGenerating((g) => ({ ...g, [templateIdx]: false }));
    }
  }

  async function saveAnswers() {
    // Merge local drafts into saved answers
    const current = { ...(job?.application_answers ?? {}) };
    Object.entries(answerDrafts).forEach(([k, v]) => { current[k] = v; });
    try {
      await updateReview.mutateAsync({ application_answers: current } as any);
      setAnswerDrafts({});
      toast({ title: "Answers saved" });
    } catch {
      toast({ title: "Failed to save answers", variant: "destructive" });
    }
  }

  async function generatePrep() {
    setGeneratingPrep(true);
    setConfirmRegenerate(false);
    try {
      const res = await jobsApi.post(`/jobs/${id}/interview-prep`);
      setPrepQuestions(res.data.questions);
      setPrepHasChanges(false);
      qc.invalidateQueries({ queryKey: ["job", id] });
    } catch (err: any) {
      console.error("Interview prep error:", err?.response ?? err);
      toast({
        title: "Generation failed",
        description: apiErrorMessage(err),
        variant: "destructive",
      });
    } finally {
      setGeneratingPrep(false);
    }
  }

  function updatePrepQuestion(idx: number, updated: InterviewQuestion) {
    const qs = [...(prepQuestions ?? displayedPrep ?? [])];
    qs[idx] = updated;
    setPrepQuestions(qs);
    setPrepHasChanges(true);
  }

  function deletePrepQuestion(idx: number) {
    const qs = [...(prepQuestions ?? displayedPrep ?? [])].filter((_, i) => i !== idx);
    setPrepQuestions(qs);
    setPrepHasChanges(true);
  }

  function addPrepQuestion() {
    const newQ: InterviewQuestion = {
      id: crypto.randomUUID(),
      category: "General",
      question: "",
      coaching: "",
      story_refs: [],
      notes: "",
    };
    const qs = [...(prepQuestions ?? displayedPrep ?? []), newQ];
    setPrepQuestions(qs);
    setPrepHasChanges(true);
  }

  async function savePrepChanges() {
    if (!prepQuestions) return;
    try {
      await updateReview.mutateAsync({ interview_questions: prepQuestions } as any);
      setPrepHasChanges(false);
      toast({ title: "Interview prep saved" });
    } catch {
      toast({ title: "Failed to save", variant: "destructive" });
    }
  }

  async function updateVoiceGuidelines(merged: string) {
    if (!criteria) throw new Error("No criteria loaded");
    await criteriaApi.put("/criteria", { ...criteria, voice_guidelines: merged });
    qc.invalidateQueries({ queryKey: ["criteria"] });
  }

  async function updateApplicationPrompt(merged: string) {
    if (!criteria) throw new Error("No criteria loaded");
    const templates = [...(criteria.application_templates ?? DEFAULT_APP_TEMPLATES)];
    templates[refinement.templateIdx] = {
      ...templates[refinement.templateIdx],
      prompt: merged,
    };
    await criteriaApi.put("/criteria", { ...criteria, application_templates: templates });
    qc.invalidateQueries({ queryKey: ["criteria"] });
  }

  const templates = criteria?.application_templates ?? DEFAULT_APP_TEMPLATES;
  const hasDraftChanges = Object.keys(answerDrafts).length > 0;
  const displayedPrep: InterviewQuestion[] = prepQuestions ?? job?.interview_questions ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="py-12 text-center text-muted-foreground">
        Job not found.{" "}
        <Button variant="link" onClick={() => navigate("/jobs")}>
          Back to list
        </Button>
      </div>
    );
  }

  const displayedResearch = researchSummary ?? job.research_summary ?? null;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Back */}
      <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="-ml-1">
        <ArrowLeft className="h-4 w-4 mr-1" />
        Back
      </Button>

      {/* Header */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-start gap-3">
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold leading-snug">{job.title}</h1>
            <p className="flex items-center gap-1 text-muted-foreground mt-1">
              <Building2 className="h-4 w-4 shrink-0" />
              {job.company}
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            <Button variant="outline" size="sm" asChild>
              <a href={job.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-4 w-4 mr-1" />
                Apply
              </a>
            </Button>
          </div>
        </div>

        {/* Meta row */}
        <div className="flex flex-wrap gap-2 text-sm">
          {job.remote && <Badge className="bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">Remote</Badge>}
          {job.location && !job.remote && (
            <Badge variant="outline" className="gap-1">
              <MapPin className="h-3 w-3" />
              {job.location}
            </Badge>
          )}
          {formatSalary(job.salary_min, job.salary_max) !== "—" && (
            <Badge variant="outline" className="gap-1">
              <DollarSign className="h-3 w-3" />
              {formatSalary(job.salary_min, job.salary_max)}
            </Badge>
          )}
          {job.source && <Badge variant="outline">{formatSource(job.source)}</Badge>}
          {job.date_posted && (
            <Badge variant="outline" className="gap-1">
              <Calendar className="h-3 w-3" />
              {formatDate(job.date_posted)}
            </Badge>
          )}
          {job.has_contact && (
            <Badge className="gap-1 bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">
              <UserCheck className="h-3 w-3" />
              Known contact
            </Badge>
          )}
        </div>
      </div>

      {/* AI Score card */}
      {job.ai_score != null && (
        <>
          <div className="flex items-center gap-4 p-4 rounded-lg border bg-card">
            <div className="text-center shrink-0">
              <button
                className={`text-4xl font-bold ${scoreColor(job.ai_score)} hover:underline cursor-pointer`}
                title="Click to see score breakdown"
                onClick={() => setScoreBreakdownOpen(true)}
              >
                {job.ai_score}
              </button>
              <div className="text-xs text-muted-foreground">AI score</div>
            </div>
            <Separator orientation="vertical" className="h-12" />
            <div className="flex-1 space-y-1">
              {job.recommended && (
                <div className="flex items-center gap-1 text-sm font-medium text-yellow-600 dark:text-yellow-400">
                  <Star className="h-4 w-4 fill-current" />
                  Recommended for you
                </div>
              )}
              {job.ai_summary && <p className="text-sm text-muted-foreground leading-relaxed">{job.ai_summary}</p>}
            </div>
          </div>

          {/* Score breakdown dialog */}
          <Dialog open={scoreBreakdownOpen} onOpenChange={setScoreBreakdownOpen}>
            <DialogContent className="max-w-xs">
              <DialogHeader>
                <DialogTitle>Score Breakdown</DialogTitle>
                <DialogDescription>Five equal-weight dimensions averaged to the overall score.</DialogDescription>
              </DialogHeader>
              <div className="space-y-3 py-1">
                {[
                  { label: "Skills",     value: job.skills_rank },
                  { label: "Experience", value: job.experience_rank },
                  { label: "Location",   value: job.location_rank },
                  { label: "Education",  value: job.education_rank },
                  { label: "Salary",     value: job.salary_rank },
                ].map(({ label, value }) => (
                  <div key={label} className="flex items-center gap-3">
                    <span className="text-sm w-24 shrink-0 text-muted-foreground">{label}</span>
                    <div className="flex-1 bg-muted rounded-full h-1.5 overflow-hidden">
                      <div
                        className={`h-1.5 rounded-full transition-all ${
                          value == null ? "bg-muted-foreground/30" :
                          value >= 7 ? "bg-emerald-500" :
                          value >= 5 ? "bg-amber-500" : "bg-rose-500"
                        }`}
                        style={{ width: `${(value ?? 0) * 10}%` }}
                      />
                    </div>
                    <span className={`text-sm font-bold w-5 text-right ${scoreColor(value)}`}>
                      {value ?? "—"}
                    </span>
                  </div>
                ))}
                <Separator />
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold w-24 shrink-0">Overall</span>
                  <div className="flex-1" />
                  <span className={`text-xl font-bold ${scoreColor(job.ai_score)}`}>{job.ai_score}</span>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </>
      )}

      {/* Your contacts at this company (from your LinkedIn upload) */}
      {contacts.length > 0 && (
        <div className="p-4 rounded-lg border bg-card space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Users className="h-4 w-4 text-blue-500" />
            Your contacts at {job.company}
            <Badge variant="secondary" className="ml-0.5">{contacts.length}</Badge>
          </div>
          <ul className="space-y-1.5">
            {contacts.map((c) => {
              const name = [c.first_name, c.last_name].filter(Boolean).join(" ") || "Unknown";
              return (
                <li key={c.id} className="text-sm flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{name}</span>
                  {c.position && <span className="text-xs text-muted-foreground">{c.position}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Status + delete */}
      <div className="flex flex-wrap gap-4 items-end">
        <div className="space-y-1.5 w-40">
          <Label>Status</Label>
          <Select value={job.status} onValueChange={changeStatus}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive hover:bg-destructive/10 hover:text-destructive mb-0.5"
          onClick={handleDeleteJob}
          disabled={deleteReview.isPending}
        >
          <Trash2 className="h-4 w-4 mr-1" />
          Delete job
        </Button>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="description">
        {/* Mobile: full-width flex that wraps so the 7 tabs don't run off the
            right edge (h-auto lets the wrapped rows grow). Desktop (sm+): restore
            the original compact inline pill, single row, unchanged. */}
        <TabsList className="flex w-full flex-wrap justify-start h-auto gap-1 sm:inline-flex sm:w-auto sm:flex-nowrap sm:justify-center sm:h-9 sm:gap-0">
          <TabsTrigger value="description">Description</TabsTrigger>
          <TabsTrigger value="research">Research</TabsTrigger>
          <TabsTrigger value="application">Application</TabsTrigger>
          <TabsTrigger value="tailor">Tailor résumé</TabsTrigger>
          <TabsTrigger value="prep">Interview Prep</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
          <TabsTrigger value="notes">Notes</TabsTrigger>
        </TabsList>

        {/* ── Description ── */}
        <TabsContent value="description" className="mt-4 space-y-3">
          {job.description ? (
            <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap text-sm leading-relaxed">
              {job.description}
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">No description available.</p>
          )}
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary underline underline-offset-2 hover:opacity-80"
          >
            Open full job posting ↗
          </a>
        </TabsContent>

        {/* ── Research ── */}
        <TabsContent value="research" className="mt-4 space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <p className="text-sm font-medium">Company research — {job.company}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                AI summary based on the job posting and your profile.
                {!criteria?.research_prompt && (
                  <> Customise the prompt in <a href="/profile?tab=criteria" className="underline underline-offset-2">Settings → Job Criteria</a>.</>
                )}
              </p>
            </div>
            <Button size="sm" onClick={generateResearch} disabled={generatingResearch}>
              {generatingResearch
                ? <><Loader2 className="h-4 w-4 animate-spin mr-1" />Generating…</>
                : <><Sparkles className="h-4 w-4 mr-1" />{displayedResearch ? "Regenerate" : "Generate"}</>}
            </Button>
          </div>

          {displayedResearch ? (
            <div className="rounded-lg border bg-muted/30 p-4 text-sm leading-relaxed whitespace-pre-wrap">
              {displayedResearch}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              Click "Generate" to get an AI summary of {job.company}.
            </div>
          )}

          {/* Quick links */}
          <div className="flex items-center justify-between gap-2 pt-1">
            <span className="text-xs text-muted-foreground shrink-0">External search:</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" asChild>
                <a href={`https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(job.company)}`} target="_blank" rel="noopener noreferrer">
                  LinkedIn
                </a>
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={`https://glassdoor.com/Search/Results.htm?keyword=${encodeURIComponent(job.company)}`} target="_blank" rel="noopener noreferrer">
                  Glassdoor
                </a>
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={`https://www.google.com/search?q=${encodeURIComponent(job.company + " company culture")}`} target="_blank" rel="noopener noreferrer">
                  Google
                </a>
              </Button>
            </div>
          </div>
        </TabsContent>

        {/* ── Application Assistance ── */}
        <TabsContent value="application" className="mt-4 space-y-6">
          <div className="flex items-start justify-between flex-wrap gap-2">
            <div>
              <p className="text-sm font-medium">Application assistance</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                AI-drafted answers tailored to this posting and your resume.
                Edit before using. <a href="/profile?tab=criteria" className="underline underline-offset-2">Manage templates →</a>
              </p>
            </div>
            {hasDraftChanges && (
              <Button size="sm" onClick={saveAnswers} disabled={updateReview.isPending}>
                {updateReview.isPending ? "Saving…" : "Save all answers"}
              </Button>
            )}
          </div>

          {templates.map((tmpl, idx) => {
            const savedAnswer = job.application_answers?.[String(idx)] ?? "";
            const draft = answerDrafts[idx];
            const displayAnswer = draft !== undefined ? draft : savedAnswer;
            const isGenerating = generating[idx] ?? false;

            return (
              <div key={idx} className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Label className="text-sm font-medium">{tmpl.label}</Label>
                  <div className="flex gap-2">
                    {displayAnswer && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setRefinement({ open: true, templateIdx: idx })}
                      >
                        <MessageSquarePlus className="h-3.5 w-3.5 mr-1" />
                        Refine
                      </Button>
                    )}
                    <Button
                      size="sm"
                      onClick={() => generateAnswer(idx)}
                      disabled={isGenerating}
                    >
                      {isGenerating
                        ? <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />Generating…</>
                        : <><Sparkles className="h-3.5 w-3.5 mr-1" />{displayAnswer ? "Regenerate" : "Generate"}</>}
                    </Button>
                  </div>
                </div>
                {!displayAnswer ? (
                  <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                    Click "Generate" to draft your {tmpl.label.toLowerCase()}.
                  </div>
                ) : (
                  <Textarea
                    rows={6}
                    value={displayAnswer}
                    onChange={(e) => setAnswerDrafts((d) => ({ ...d, [idx]: e.target.value }))}
                  />
                )}
                {draft !== undefined && draft !== savedAnswer && (
                  <p className="text-xs text-muted-foreground">Unsaved changes — click "Save all answers" above.</p>
                )}
                {idx < templates.length - 1 && <Separator className="mt-2" />}
              </div>
            );
          })}
        </TabsContent>

        {/* ── Tailor résumé (launchpad) ── */}
        <TabsContent value="tailor" className="mt-4">
          <TailorTab reviewId={job.id} />
        </TabsContent>

        {/* ── Interview Prep ── */}
        <TabsContent value="prep" className="mt-4 space-y-4">
          {/* Header row */}
          <div className="flex items-start gap-2">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">Interview preparation</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                AI-generated questions a hiring manager might ask, with coaching notes tailored to this role.{" "}
                <a href="/profile?tab=prompts" className="underline underline-offset-2">Customise prompt →</a>
              </p>
            </div>
            <div className="flex gap-2 shrink-0">
              {prepHasChanges && (
                <Button size="sm" variant="outline" onClick={savePrepChanges} disabled={updateReview.isPending}>
                  {updateReview.isPending ? "Saving…" : "Save changes"}
                </Button>
              )}
              <Button
                size="sm"
                onClick={() => displayedPrep.length > 0 ? setConfirmRegenerate(true) : generatePrep()}
                disabled={generatingPrep}
              >
                {generatingPrep
                  ? <><Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />Generating…</>
                  : <><Sparkles className="h-3.5 w-3.5 mr-1" />{displayedPrep.length > 0 ? "Regenerate" : "Generate prep"}</>}
              </Button>
            </div>
          </div>

          {/* Empty state */}
          {displayedPrep.length === 0 && !generatingPrep && (
            <div className="rounded-lg border border-dashed p-8 text-center space-y-2">
              <Sparkles className="h-6 w-6 mx-auto text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                Click "Generate prep" to get AI-suggested interview questions with coaching notes for this role.
              </p>
              <p className="text-xs text-muted-foreground opacity-70">
                Add your career stories in Settings → Resume to get personalised coaching.
              </p>
            </div>
          )}

          {/* Question cards */}
          <div className="space-y-3">
            {displayedPrep.map((q, idx) => (
              <InterviewPrepCard
                key={q.id}
                question={q}
                onSave={(updated) => updatePrepQuestion(idx, updated)}
                onDelete={() => deletePrepQuestion(idx)}
              />
            ))}
          </div>

          {/* Add question */}
          {displayedPrep.length > 0 && (
            <Button variant="outline" size="sm" onClick={addPrepQuestion}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              Add question
            </Button>
          )}
        </TabsContent>

        {/* ── Notes ── */}
        <TabsContent value="notes" className="mt-4 space-y-3">
          <Textarea
            rows={8}
            placeholder="Your notes, talking points, follow-up actions…"
            value={notes}
            onChange={(e) => setNotesDraft(e.target.value)}
          />
          <Button
            onClick={saveNotes}
            disabled={updateReview.isPending || notes === (job.notes ?? "")}
          >
            {updateReview.isPending ? "Saving…" : "Save notes"}
          </Button>
        </TabsContent>

        {/* ── Timeline ── */}
        <TabsContent value="timeline" className="mt-4">
          <TimelineTab reviewId={job.id} events={job.timeline ?? []} />
        </TabsContent>
      </Tabs>

      {/* Regenerate warning dialog */}
      <Dialog open={confirmRegenerate} onOpenChange={setConfirmRegenerate}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Regenerate interview prep?
            </DialogTitle>
            <DialogDescription>
              This will replace all {displayedPrep.length} current question cards — including any edits and notes you've added. This can't be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmRegenerate(false)}>Cancel</Button>
            <Button variant="destructive" onClick={generatePrep} disabled={generatingPrep}>
              {generatingPrep ? "Generating…" : "Yes, regenerate"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Refinement drawer — rendered outside the tabs so it's always mounted */}
      {id && (
        <RefinementDrawer
          open={refinement.open}
          onClose={() => setRefinement((r) => ({ ...r, open: false }))}
          reviewId={id}
          templateIdx={refinement.templateIdx}
          templateLabel={templates[refinement.templateIdx]?.label ?? ""}
          currentAnswer={(() => {
            const savedAnswer = job.application_answers?.[String(refinement.templateIdx)] ?? "";
            const draft = answerDrafts[refinement.templateIdx];
            return draft !== undefined ? draft : savedAnswer;
          })()}
          currentVoiceGuidelines={criteria?.voice_guidelines ?? null}
          currentPrompt={templates[refinement.templateIdx]?.prompt ?? ""}
          onUseAnswer={(answer) => {
            setAnswerDrafts((d) => ({ ...d, [refinement.templateIdx]: answer }));
          }}
          onUpdateVoiceGuidelines={updateVoiceGuidelines}
          onUpdateApplicationPrompt={updateApplicationPrompt}
        />
      )}
    </div>
  );
}
