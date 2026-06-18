import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Users, Plus, Search, Mail, Phone, Building2, Trash2, Pencil, Loader2,
  ExternalLink, Linkedin, Link2, X, Sparkles, Briefcase,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Badge } from "../components/ui/badge";
import { Textarea } from "../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../components/ui/sheet";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "../components/ui/dialog";
import { recruitersApi, jobsApi } from "../lib/api";
import { safeHref } from "../lib/utils";
import { toast } from "../hooks/useToast";
import type {
  Recruiter, RecruiterStatus, RecruiterType, RecruiterSuggestion, JobListResponse,
} from "../lib/types";

const STATUS_OPTIONS: { value: RecruiterStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "ghosted", label: "Ghosted" },
  { value: "archived", label: "Archived" },
  { value: "do_not_contact", label: "Do not contact" },
];

const TYPE_OPTIONS: { value: RecruiterType; label: string }[] = [
  { value: "agency", label: "Agency" },
  { value: "in_house", label: "In-house" },
];

const STATUS_TONE: Record<RecruiterStatus, string> = {
  active: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20",
  ghosted: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20",
  archived: "bg-muted text-muted-foreground",
  do_not_contact: "bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/20",
};

function statusLabel(s: RecruiterStatus) {
  return STATUS_OPTIONS.find((o) => o.value === s)?.label ?? s;
}

// Blank draft for the "Add recruiter" form.
type Draft = {
  name: string; email: string; phone: string; employer: string;
  companies_represented: string;   // comma-separated in the form
  linkedin_url: string; type: RecruiterType | ""; status: RecruiterStatus;
  last_contacted: string; notes: string;
};

const EMPTY_DRAFT: Draft = {
  name: "", email: "", phone: "", employer: "", companies_represented: "",
  linkedin_url: "", type: "", status: "active", last_contacted: "", notes: "",
};

function toDraft(r: Recruiter): Draft {
  return {
    name: r.name, email: r.email ?? "", phone: r.phone ?? "", employer: r.employer ?? "",
    companies_represented: (r.companies_represented ?? []).join(", "),
    linkedin_url: r.linkedin_url ?? "", type: r.type ?? "", status: r.status,
    last_contacted: r.last_contacted ?? "", notes: r.notes ?? "",
  };
}

// Form → API payload (trim, split companies, drop empties to null).
function toPayload(d: Draft) {
  const clean = (s: string) => (s.trim() ? s.trim() : null);
  return {
    name: d.name.trim(),
    email: clean(d.email),
    phone: clean(d.phone),
    employer: clean(d.employer),
    companies_represented: d.companies_represented
      .split(",").map((c) => c.trim()).filter(Boolean),
    linkedin_url: clean(d.linkedin_url),
    type: d.type || null,
    status: d.status,
    last_contacted: clean(d.last_contacted),
    notes: clean(d.notes),
  };
}

// ─── Job linker (search the user's jobs, link one to the recruiter) ───────────

function JobLinker({ recruiterId, linkedIds }: { recruiterId: string; linkedIds: Set<string> }) {
  const qc = useQueryClient();
  const [term, setTerm] = useState("");
  const { data } = useQuery<JobListResponse>({
    queryKey: ["jobs-linker", term],
    queryFn: () => jobsApi.get("/jobs", { params: { search: term, limit: 8 } }).then((r) => r.data),
    enabled: term.trim().length > 0,
  });

  const link = useMutation({
    mutationFn: (review_id: string) =>
      recruitersApi.post(`/recruiters/${recruiterId}/jobs`, { review_id }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recruiters"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setTerm("");
    },
    onError: () => toast({ title: "Failed to link job", variant: "destructive" }),
  });

  const results = (data?.items ?? []).filter((j) => !linkedIds.has(j.id));

  return (
    <div className="space-y-1.5">
      <div className="relative">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-8 h-9"
          placeholder="Search your jobs to link…"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
        />
      </div>
      {term.trim() && (
        <div className="rounded-md border divide-y max-h-40 overflow-y-auto">
          {results.length === 0 ? (
            <p className="text-xs text-muted-foreground px-3 py-2">No unlinked matches.</p>
          ) : (
            results.map((j) => (
              <button
                key={j.id}
                type="button"
                disabled={link.isPending}
                onClick={() => link.mutate(j.id)}
                className="w-full text-left px-3 py-1.5 text-xs hover:bg-accent/50 flex items-center gap-2"
              >
                <Link2 className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="font-medium truncate">{j.title}</span>
                <span className="text-muted-foreground truncate">· {j.company}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ─── Add / edit drawer ────────────────────────────────────────────────────────

function RecruiterSheet({
  recruiter, draft, onClose,
}: {
  recruiter: Recruiter | null;          // null = creating
  draft: Draft;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState<Draft>(draft);
  const isEdit = !!recruiter;

  function patch(p: Partial<Draft>) { setForm((f) => ({ ...f, ...p })); }

  const save = useMutation({
    mutationFn: () =>
      isEdit
        ? recruitersApi.patch(`/recruiters/${recruiter!.id}`, toPayload(form)).then((r) => r.data)
        : recruitersApi.post("/recruiters", toPayload(form)).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recruiters"] });
      qc.invalidateQueries({ queryKey: ["recruiter-suggestions"] });
      toast({ title: isEdit ? "Recruiter updated" : "Recruiter added" });
      onClose();
    },
    onError: (e: any) =>
      toast({ title: "Save failed", description: e?.response?.data?.detail?.toString(), variant: "destructive" }),
  });

  const unlink = useMutation({
    mutationFn: (reviewId: string) =>
      recruitersApi.delete(`/recruiters/${recruiter!.id}/jobs/${reviewId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recruiters"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const ln = safeHref(form.linkedin_url);
  const linkedIds = new Set((recruiter?.jobs ?? []).map((j) => j.id));

  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
        <SheetHeader className="pr-6">
          <SheetTitle>{isEdit ? "Edit recruiter" : "Add recruiter"}</SheetTitle>
        </SheetHeader>

        <div className="mt-4 space-y-4">
          <Field label="Name *">
            <Input value={form.name} onChange={(e) => patch({ name: e.target.value })} placeholder="Jane Recruiter" />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Email">
              <Input type="email" value={form.email} onChange={(e) => patch({ email: e.target.value })} placeholder="jane@agency.com" />
            </Field>
            <Field label="Phone">
              <Input value={form.phone} onChange={(e) => patch({ phone: e.target.value })} placeholder="555-1212" />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Type">
              <Select value={form.type || "none"} onValueChange={(v) => patch({ type: v === "none" ? "" : v as RecruiterType })}>
                <SelectTrigger><SelectValue placeholder="—" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">—</SelectItem>
                  {TYPE_OPTIONS.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Status">
              <Select value={form.status} onValueChange={(v) => patch({ status: v as RecruiterStatus })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
          </div>

          <Field label={form.type === "in_house" ? "Employer" : "Employer / agency"}>
            <Input value={form.employer} onChange={(e) => patch({ employer: e.target.value })} placeholder="Best Recruiting Co." />
          </Field>

          {/* Companies represented — only meaningful for agencies */}
          {form.type !== "in_house" && (
            <Field label="Companies represented" hint="Comma-separated">
              <Input
                value={form.companies_represented}
                onChange={(e) => patch({ companies_represented: e.target.value })}
                placeholder="Acme, Globex, Initech"
              />
            </Field>
          )}

          <div className="grid grid-cols-2 gap-3">
            <Field label="LinkedIn URL">
              <Input value={form.linkedin_url} onChange={(e) => patch({ linkedin_url: e.target.value })} placeholder="https://linkedin.com/in/…" />
            </Field>
            <Field label="Last contacted">
              <Input type="date" value={form.last_contacted} onChange={(e) => patch({ last_contacted: e.target.value })} />
            </Field>
          </div>
          {form.linkedin_url.trim() && !ln && (
            <p className="text-xs text-rose-600 -mt-2">That doesn't look like a valid http(s) URL — it won't be clickable.</p>
          )}

          <Field label="Notes">
            <Textarea rows={3} value={form.notes} onChange={(e) => patch({ notes: e.target.value })} placeholder="Where you met, roles discussed, follow-ups…" />
          </Field>

          {/* Linked jobs (edit mode only — needs a saved recruiter id) */}
          {isEdit && (
            <div className="space-y-2 pt-1">
              <Label>Linked jobs</Label>
              {(recruiter!.jobs.length === 0) && (
                <p className="text-xs text-muted-foreground">No jobs linked yet.</p>
              )}
              {recruiter!.jobs.map((j) => (
                <div key={j.id} className="flex items-center gap-2 text-sm rounded-md border px-2.5 py-1.5">
                  <Briefcase className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left hover:underline"
                    onClick={() => navigate(`/jobs/${j.id}`)}
                  >
                    <span className="font-medium">{j.title}</span>{" "}
                    <span className="text-muted-foreground">· {j.company}</span>
                  </button>
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-destructive"
                    title="Unlink"
                    onClick={() => unlink.mutate(j.id)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
              <JobLinker recruiterId={recruiter!.id} linkedIds={linkedIds} />
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <Button
              className="flex-1"
              disabled={!form.name.trim() || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
              {isEdit ? "Save changes" : "Add recruiter"}
            </Button>
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
          </div>
          {!isEdit && (
            <p className="text-xs text-muted-foreground">Save first, then reopen to link jobs.</p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <Label>{label}</Label>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

// ─── Recruiter card ───────────────────────────────────────────────────────────

function RecruiterCard({ r, onEdit, onDelete }: { r: Recruiter; onEdit: () => void; onDelete: () => void }) {
  const ln = safeHref(r.linkedin_url);
  return (
    <div className="rounded-lg border bg-card p-4 space-y-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{r.name}</span>
            <Badge variant="outline" className={STATUS_TONE[r.status]}>{statusLabel(r.status)}</Badge>
            {r.type && <Badge variant="secondary" className="text-[10px]">{r.type === "agency" ? "Agency" : "In-house"}</Badge>}
          </div>
          {r.employer && (
            <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
              <Building2 className="h-3 w-3" /> {r.employer}
            </p>
          )}
        </div>
        <div className="flex gap-1 shrink-0">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onEdit} title="Edit">
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive hover:bg-destructive/10" onClick={onDelete} title="Delete">
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Contact row */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {r.email && (
          <a href={`mailto:${r.email}`} className="flex items-center gap-1 hover:text-foreground">
            <Mail className="h-3 w-3" /> {r.email}
          </a>
        )}
        {r.phone && (
          <a href={`tel:${r.phone}`} className="flex items-center gap-1 hover:text-foreground">
            <Phone className="h-3 w-3" /> {r.phone}
          </a>
        )}
        {ln && (
          <a href={ln} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 hover:text-foreground">
            <Linkedin className="h-3 w-3" /> LinkedIn <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      {r.type !== "in_house" && r.companies_represented && r.companies_represented.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {r.companies_represented.map((c) => (
            <Badge key={c} variant="outline" className="text-[10px] font-normal">{c}</Badge>
          ))}
        </div>
      )}

      {r.notes && <p className="text-xs text-muted-foreground line-clamp-2">{r.notes}</p>}

      {r.jobs.length > 0 && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground pt-0.5">
          <Briefcase className="h-3 w-3" />
          {r.jobs.length} linked {r.jobs.length === 1 ? "job" : "jobs"}
        </div>
      )}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function RecruitersPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<RecruiterStatus | "all">("all");
  const [editing, setEditing] = useState<{ recruiter: Recruiter | null; draft: Draft } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Recruiter | null>(null);

  const params: Record<string, string> = {};
  if (search.trim()) params.search = search.trim();
  if (statusFilter !== "all") params.status = statusFilter;

  const { data: recruiters = [], isLoading } = useQuery<Recruiter[]>({
    queryKey: ["recruiters", params],
    queryFn: () => recruitersApi.get("/recruiters", { params }).then((r) => r.data),
  });

  const { data: suggestions = [] } = useQuery<RecruiterSuggestion[]>({
    queryKey: ["recruiter-suggestions"],
    queryFn: () => recruitersApi.get("/recruiters/suggestions").then((r) => r.data),
  });

  const del = useMutation({
    mutationFn: (id: string) => recruitersApi.delete(`/recruiters/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recruiters"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setDeleteTarget(null);
      toast({ title: "Recruiter deleted" });
    },
    onError: () => toast({ title: "Failed to delete", variant: "destructive" }),
  });

  function addFromSuggestion(s: RecruiterSuggestion) {
    setEditing({ recruiter: null, draft: { ...EMPTY_DRAFT, name: s.name, email: s.email ?? "" } });
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Users className="h-5 w-5 text-primary" />
        <h1 className="text-xl font-bold flex-1">Recruiters</h1>
        <Button size="sm" onClick={() => setEditing({ recruiter: null, draft: EMPTY_DRAFT })}>
          <Plus className="h-4 w-4 mr-1" /> Add recruiter
        </Button>
      </div>

      {/* Suggestions from inbox */}
      {suggestions.length > 0 && (
        <div className="rounded-lg border border-blue-200 bg-blue-50/60 dark:bg-blue-950/30 dark:border-blue-900 p-3 space-y-2">
          <div className="flex items-center gap-1.5 text-sm font-medium text-blue-800 dark:text-blue-300">
            <Sparkles className="h-4 w-4" />
            Recruiters from your inbox
          </div>
          <p className="text-xs text-muted-foreground">
            Senders of recruiter emails you haven't tracked yet. Click to add — you can edit before saving.
          </p>
          <div className="flex flex-wrap gap-2">
            {suggestions.slice(0, 12).map((s) => (
              <button
                key={s.email ?? s.name}
                type="button"
                onClick={() => addFromSuggestion(s)}
                className="inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-1 text-xs hover:bg-accent/50"
              >
                <Plus className="h-3 w-3" />
                <span className="font-medium">{s.name}</span>
                {s.email_count > 1 && <span className="text-muted-foreground">×{s.email_count}</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Search + status filter */}
      <div className="flex flex-wrap gap-2 items-center">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search name, employer, or email…"
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as RecruiterStatus | "all")}>
          <SelectTrigger className="w-[160px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUS_OPTIONS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
      ) : recruiters.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <Users className="h-8 w-8 mx-auto mb-2 opacity-40" />
          <p className="text-sm">
            {search || statusFilter !== "all" ? "No recruiters match." : "No recruiters yet — add one or pick from your inbox above."}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {recruiters.map((r) => (
            <RecruiterCard
              key={r.id}
              r={r}
              onEdit={() => setEditing({ recruiter: r, draft: toDraft(r) })}
              onDelete={() => setDeleteTarget(r)}
            />
          ))}
        </div>
      )}

      {/* Add / edit drawer */}
      {editing && (
        <RecruiterSheet
          recruiter={editing.recruiter}
          draft={editing.draft}
          onClose={() => setEditing(null)}
        />
      )}

      {/* Delete confirm */}
      <Dialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete this recruiter?</DialogTitle>
            <DialogDescription>
              Removes {deleteTarget?.name} from your list. Any jobs linked to them stay in your list — they're
              just unlinked.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button variant="destructive" disabled={del.isPending} onClick={() => deleteTarget && del.mutate(deleteTarget.id)}>
              {del.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
