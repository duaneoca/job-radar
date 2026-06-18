import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Search, SlidersHorizontal, ExternalLink, RefreshCw, ChevronLeft, ChevronRight,
  MapPin, Building2, DollarSign, Star, Check, UserCheck, Loader2, Plus, Trash2,
} from "lucide-react";
import { ColumnFilter } from "../components/ColumnFilter";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Badge } from "../components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Switch } from "../components/ui/switch";
import { Label } from "../components/ui/label";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../components/ui/sheet";
import { Separator } from "../components/ui/separator";
import { Textarea } from "../components/ui/textarea";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "../components/ui/dialog";
import { jobsApi } from "../lib/api";
import {
  formatDate, formatSalary, formatSource, scoreColor, statusBadgeVariant,
  STATUS_OPTIONS, SOURCE_OPTIONS,
} from "../lib/utils";
import { toast } from "../hooks/useToast";
import type { JobReview, JobListResponse } from "../lib/types";

const PAGE_SIZE_OPTIONS = [25, 50, 100];

// Excel-style header filters hold multi-selections; empty array = no filter.
interface Filters {
  status: string[];
  source: string[];
  contact: string[];   // subset of ["yes", "no"]
  remote_only: boolean;
  min_score: string;
  search: string;
}

const CONTACT_OPTIONS = [
  { value: "yes", label: "Has contact" },
  { value: "no", label: "No contact" },
];

const FILTERS_KEY = "jr-jobs-filters";
const DEFAULT_FILTERS: Filters = {
  status: [], source: [], contact: [], remote_only: false, min_score: "", search: "",
};

function loadFilters(): Filters {
  try {
    const raw = localStorage.getItem(FILTERS_KEY);
    if (raw) return { ...DEFAULT_FILTERS, ...JSON.parse(raw) };
  } catch { /* corrupt value — fall back to defaults */ }
  return DEFAULT_FILTERS;
}

// ─── Score breakdown dialog ───────────────────────────────────────────────────

function ScoreBreakdownDialog({ job, onClose }: { job: JobReview; onClose: () => void }) {
  const dims = [
    { label: "Skills",     value: job.skills_rank },
    { label: "Experience", value: job.experience_rank },
    { label: "Location",   value: job.location_rank },
    { label: "Education",  value: job.education_rank },
    { label: "Salary",     value: job.salary_rank },
  ];

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xs">
        <DialogHeader>
          <DialogTitle>Score Breakdown</DialogTitle>
          <DialogDescription>Five equal-weight dimensions averaged to the overall score.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-1">
          {dims.map(({ label, value }) => (
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
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function JobsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [filters, setFilters] = useState<Filters>(loadFilters);
  const [showFilters, setShowFilters] = useState(false);

  // Persist filters across refreshes.
  useEffect(() => {
    localStorage.setItem(FILTERS_KEY, JSON.stringify(filters));
  }, [filters]);

  function updateFilter(patch: Partial<Filters>) {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(1);
  }
  const [selected, setSelected] = useState<JobReview | null>(null);
  const [notesDraft, setNotesDraft] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);
  const [scoreBreakdown, setScoreBreakdown] = useState<JobReview | null>(null);

  const params: Record<string, any> = {
    skip: (page - 1) * pageSize,
    limit: pageSize,
  };
  if (filters.status.length) params.status = filters.status;
  if (filters.source.length) params.source = filters.source;
  // contact: one option selected = filter; both or none = no filter.
  if (filters.contact.length === 1) params.has_contact = filters.contact[0] === "yes";
  if (filters.remote_only) params.remote_only = true;
  if (filters.min_score) params.min_score = Number(filters.min_score);
  if (filters.search) params.search = filters.search;

  const { data, isLoading, isFetching } = useQuery<JobListResponse>({
    queryKey: ["jobs", params],
    // indexes:null → arrays serialize as `status=a&status=b` (FastAPI list[...] shape).
    queryFn: () => jobsApi.get("/jobs", { params, paramsSerializer: { indexes: null } }).then((r) => r.data),
    placeholderData: (prev) => prev,
  });

  const updateReview = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<JobReview> }) =>
      jobsApi.patch(`/jobs/${id}`, patch).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const deleteReview = useMutation({
    mutationFn: (id: string) => jobsApi.delete(`/jobs/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setSelected(null);
      toast({ title: "Job deleted" });
    },
    onError: () => toast({ title: "Failed to delete job", variant: "destructive" }),
  });

  const enqueueReview = useMutation({
    mutationFn: () => jobsApi.post("/jobs/enqueue-review"),
    onSuccess: () => toast({ title: "Review tasks queued", description: "AI will process new jobs shortly." }),
    onError: () => toast({ title: "Failed to queue reviews", variant: "destructive" }),
  });

  function openJob(job: JobReview) {
    setSelected(job);
    setNotesDraft(job.notes ?? "");
  }

  async function saveNotes() {
    if (!selected) return;
    setSavingNotes(true);
    try {
      await updateReview.mutateAsync({ id: selected.id, patch: { notes: notesDraft } });
      setSelected((s) => s ? { ...s, notes: notesDraft } : s);
      toast({ title: "Notes saved" });
    } catch {
      toast({ title: "Failed to save notes", variant: "destructive" });
    } finally {
      setSavingNotes(false);
    }
  }

  async function changeStatus(job: JobReview, status: string) {
    try {
      await updateReview.mutateAsync({ id: job.id, patch: { status: status as any } });
      if (selected?.job_id === job.job_id) setSelected((s) => s ? { ...s, status: status as any } : s);
    } catch {
      toast({ title: "Failed to update status", variant: "destructive" });
    }
  }

  function confirmDeleteJob(job: JobReview) {
    if (!confirm(`Delete "${job.title}" from your list? This cannot be undone.`)) return;
    deleteReview.mutate(job.id);
  }

  const totalJobs = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalJobs / pageSize));
  const jobs = data?.items ?? [];

  function resetFilters() {
    setFilters(DEFAULT_FILTERS);
    setPage(1);
  }

  const activeFilterCount =
    filters.status.length + filters.source.length + filters.contact.length +
    (filters.remote_only ? 1 : 0) + (filters.min_score ? 1 : 0);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold flex-1">Jobs</h1>
        <Button
          size="sm"
          onClick={() => navigate("/jobs/add")}
        >
          <Plus className="h-4 w-4 mr-1" />
          Add job
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => enqueueReview.mutate()}
          disabled={enqueueReview.isPending}
        >
          {enqueueReview.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
          Re-run AI review
        </Button>
      </div>

      {/* Search + filter bar — Status/Source/Contact filters now live in the column headers */}
      <div className="flex flex-wrap gap-2 items-center">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search title, company, source…"
            className="pl-8"
            value={filters.search}
            onChange={(e) => updateFilter({ search: e.target.value })}
          />
        </div>

        <Button
          variant={showFilters ? "secondary" : "outline"}
          size="sm"
          onClick={() => setShowFilters((v) => !v)}
        >
          <SlidersHorizontal className="h-4 w-4 mr-1" />
          Filters
        </Button>
        {activeFilterCount > 0 && (
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            Clear filters ({activeFilterCount})
          </Button>
        )}
      </div>

      {/* Extra filters */}
      {showFilters && (
        <div className="flex flex-wrap items-center gap-4 p-3 rounded-lg border bg-muted/30">
          <div className="flex items-center gap-2">
            <Switch
              id="remote"
              checked={filters.remote_only}
              onCheckedChange={(v) => updateFilter({ remote_only: v })}
            />
            <Label htmlFor="remote">Remote only</Label>
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="minscore" className="whitespace-nowrap">Min score</Label>
            <Input
              id="minscore"
              type="number"
              min={0}
              max={10}
              className="w-20"
              value={filters.min_score}
              onChange={(e) => updateFilter({ min_score: e.target.value })}
              placeholder="0–10"
            />
          </div>
          <Button variant="ghost" size="sm" onClick={resetFilters}>Reset</Button>
        </div>
      )}

      {/* Table */}
      <div className="rounded-lg border overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="text-left px-3 py-2.5 font-medium">Job</th>
              <th className="text-left px-3 py-2.5 font-medium hidden md:table-cell">Location</th>
              <th className="text-left px-3 py-2.5 font-medium hidden lg:table-cell">Salary</th>
              <th className="text-center px-3 py-2.5 font-medium w-16">Score</th>
              <th className="text-center px-3 py-2.5 font-medium w-20">
                <ColumnFilter label="Contact" options={CONTACT_OPTIONS}
                  selected={filters.contact} onChange={(v) => updateFilter({ contact: v })} />
              </th>
              <th className="text-left px-3 py-2.5 font-medium w-32">
                <ColumnFilter label="Status" options={STATUS_OPTIONS}
                  selected={filters.status} onChange={(v) => updateFilter({ status: v })} />
              </th>
              <th className="text-left px-3 py-2.5 font-medium hidden sm:table-cell w-24">
                <ColumnFilter label="Source" options={SOURCE_OPTIONS}
                  selected={filters.source} onChange={(v) => updateFilter({ source: v })} />
              </th>
              <th className="text-center px-3 py-2.5 font-medium hidden sm:table-cell w-10"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={8} className="text-center py-12 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin inline mr-2" />
                  Loading…
                </td>
              </tr>
            ) : jobs.length === 0 ? (
              <tr>
                <td colSpan={8} className="text-center py-12 text-muted-foreground">
                  No jobs found. Try adjusting your filters.
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr
                  key={job.job_id}
                  className={`border-b transition-colors cursor-pointer hover:bg-accent/40 ${selected?.job_id === job.job_id ? "bg-accent/60" : ""}`}
                  onClick={() => openJob(job)}
                >
                  <td className="px-3 py-2.5">
                    <div className="flex items-start gap-2">
                      <div className="min-w-0">
                        <div className="font-medium truncate max-w-[220px] sm:max-w-xs">{job.title}</div>
                        <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
                          <Building2 className="h-3 w-3 shrink-0" />
                          <span className="truncate">{job.company}</span>
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-2.5 hidden md:table-cell">
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <MapPin className="h-3 w-3 shrink-0" />
                      {job.remote ? <span className="text-green-600 dark:text-green-400 font-medium">Remote</span> : job.location ?? "—"}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 hidden lg:table-cell text-xs text-muted-foreground">
                    {formatSalary(job.salary_min, job.salary_max)}
                  </td>
                  <td className="px-3 py-2.5 text-center" onClick={(e) => e.stopPropagation()}>
                    {job.ai_score != null ? (
                      <button
                        className={`font-bold text-sm ${scoreColor(job.ai_score)} hover:underline cursor-pointer`}
                        title="Click to see score breakdown"
                        onClick={() => setScoreBreakdown(job)}
                      >
                        {job.ai_score}
                      </button>
                    ) : (
                      <span className="text-muted-foreground text-xs">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    {job.has_contact ? (
                      <span
                        className="inline-flex h-4 w-4 items-center justify-center rounded-[3px] bg-blue-500 text-white"
                        title="You have a LinkedIn contact at this company"
                      >
                        <Check className="h-3 w-3" strokeWidth={3} />
                      </span>
                    ) : (
                      <span className="inline-flex h-4 w-4 rounded-[3px] border border-muted-foreground/25" />
                    )}
                  </td>
                  <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                    <Select value={job.status} onValueChange={(v) => changeStatus(job, v)}>
                      <SelectTrigger className="h-7 text-xs w-28">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {STATUS_OPTIONS.map((s) => (
                          <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </td>
                  <td className="px-3 py-2.5 hidden sm:table-cell">
                    <span className="text-xs text-muted-foreground">{formatSource(job.source)}</span>
                  </td>
                  <td className="px-3 py-2.5 hidden sm:table-cell text-center" onClick={(e) => e.stopPropagation()}>
                    <a
                      href={job.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Rows per page:</span>
          <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPage(1); }}>
            <SelectTrigger className="h-7 w-16 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZE_OPTIONS.map((n) => (
                <SelectItem key={n} value={String(n)}>{n}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-3 text-sm">
          {isFetching && !isLoading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          <span className="text-muted-foreground">
            Page {page} of {totalPages} &mdash; {totalJobs} total
          </span>
          <div className="flex gap-1">
            <Button variant="outline" size="icon" className="h-7 w-7" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="icon" className="h-7 w-7" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      {/* Quick-view drawer */}
      <Sheet open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
          {selected && (
            <>
              <SheetHeader className="pr-6">
                <SheetTitle className="text-base leading-snug">{selected.title}</SheetTitle>
                <p className="text-sm text-muted-foreground flex items-center gap-1">
                  <Building2 className="h-3.5 w-3.5" /> {selected.company}
                </p>
              </SheetHeader>

              <div className="mt-4 space-y-4">
                {/* Meta chips */}
                <div className="flex flex-wrap gap-2 text-xs">
                  {selected.remote && <Badge className="bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">Remote</Badge>}
                  {selected.location && !selected.remote && <Badge variant="outline">{selected.location}</Badge>}
                  {formatSalary(selected.salary_min, selected.salary_max) !== "—" && (
                    <Badge variant="outline">
                      <DollarSign className="h-3 w-3 mr-0.5" />
                      {formatSalary(selected.salary_min, selected.salary_max)}
                    </Badge>
                  )}
                  {selected.source && <Badge variant="outline">{formatSource(selected.source)}</Badge>}
                  <Badge variant="outline">{formatDate(selected.date_posted)}</Badge>
                </div>

                {/* Score + recommendation */}
                {selected.ai_score != null && (
                  <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
                    <div className="text-center">
                      <button
                        className={`text-3xl font-bold ${scoreColor(selected.ai_score)} hover:underline cursor-pointer`}
                        title="Click to see score breakdown"
                        onClick={() => setScoreBreakdown(selected)}
                      >
                        {selected.ai_score}
                      </button>
                      <div className="text-xs text-muted-foreground">AI score</div>
                    </div>
                    {selected.recommended && (
                      <div className="flex items-center gap-1 text-sm text-yellow-600 dark:text-yellow-400">
                        <Star className="h-4 w-4 fill-current" />
                        Recommended
                      </div>
                    )}
                    {selected.ai_summary && (
                      <p className="text-xs text-muted-foreground flex-1 leading-relaxed">{selected.ai_summary}</p>
                    )}
                  </div>
                )}

                {/* Status */}
                <div className="space-y-1.5">
                  <Label>Status</Label>
                  <Select value={selected.status} onValueChange={(v) => changeStatus(selected, v)}>
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

                {/* Known contact */}
                {selected.has_contact && selected.contact_notes && (
                  <div className="flex items-start gap-2 p-3 rounded-lg border border-blue-200 bg-blue-50 dark:bg-blue-950 dark:border-blue-800 text-xs">
                    <UserCheck className="h-4 w-4 text-blue-600 shrink-0 mt-0.5" />
                    <div>
                      <div className="font-medium text-blue-800 dark:text-blue-300">Known contact</div>
                      <p className="text-blue-700 dark:text-blue-400 mt-0.5">{selected.contact_notes}</p>
                    </div>
                  </div>
                )}

                {/* Notes */}
                <div className="space-y-1.5">
                  <Label>Notes</Label>
                  <Textarea
                    rows={4}
                    placeholder="Your notes about this job…"
                    value={notesDraft}
                    onChange={(e) => setNotesDraft(e.target.value)}
                  />
                  <Button size="sm" onClick={saveNotes} disabled={savingNotes || notesDraft === (selected.notes ?? "")}>
                    {savingNotes ? "Saving…" : "Save notes"}
                  </Button>
                </div>

                {/* Actions */}
                <div className="flex gap-2 pt-2">
                  <Button variant="outline" size="sm" asChild className="flex-1">
                    <a href={selected.url} target="_blank" rel="noopener noreferrer">
                      <ExternalLink className="h-4 w-4 mr-1" />
                      Open posting
                    </a>
                  </Button>
                  <Button size="sm" variant="secondary" className="flex-1" onClick={() => navigate(`/jobs/${selected.id}`)}>
                    Full details →
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                    title="Delete this job"
                    onClick={() => confirmDeleteJob(selected)}
                    disabled={deleteReview.isPending}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      {/* Score breakdown popup */}
      {scoreBreakdown && (
        <ScoreBreakdownDialog job={scoreBreakdown} onClose={() => setScoreBreakdown(null)} />
      )}
    </div>
  );
}
