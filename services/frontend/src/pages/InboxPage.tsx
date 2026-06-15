import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Inbox as InboxIcon, ChevronRight, ChevronDown, ExternalLink, Check, EyeOff,
  Trash2, AlertTriangle, Loader2, Building2, CircleDot,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "../components/ui/dialog";
import { agentApi } from "../lib/api";
import { cn, safeHref } from "../lib/utils";
import { toast } from "../hooks/useToast";
import type {
  EmailCategory, EmailStatus, InboxEmail, InboxPosting, InboxInteraction, PaginatedInbox,
} from "../lib/types";

const PAGE_SIZE = 25;

const CATEGORY_LABELS: Record<EmailCategory, string> = {
  recruiter_outreach: "Recruiter",
  application_confirmation: "Application",
  job_alert: "Job alert",
  network_notification: "Network",
};

const STATUS_LABELS: Record<EmailStatus, string> = {
  pending: "Pending",
  processed: "Processed",
  needs_review: "Needs review",
  discarded: "Discarded",
};

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function StatusBadge({ status }: { status: EmailStatus }) {
  if (status === "needs_review") {
    return (
      <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30 gap-1">
        <AlertTriangle className="h-3 w-3" /> {STATUS_LABELS[status]}
      </Badge>
    );
  }
  const tone =
    status === "processed" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20"
    : status === "discarded" ? "bg-muted text-muted-foreground"
    : "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/20";
  return <Badge variant="outline" className={tone}>{STATUS_LABELS[status]}</Badge>;
}

// ─── Posting / interaction detail ─────────────────────────────

function PostingRow({ p }: { p: InboxPosting }) {
  const href = safeHref(p.link);   // [C2] only http/https becomes a link
  // The title is the click target: a link opening the source in a new tab when
  // there's a safe URL, otherwise plain (inert) text. {text} is React-escaped.
  const title = (
    <span className="inline-flex items-center gap-1.5">
      <span className="font-medium text-sm">{p.company}</span>
      <span className="text-muted-foreground text-sm">·</span>
      <span className="text-sm">{p.role}</span>
      {href && <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />}
    </span>
  );
  return (
    <div className="flex items-start gap-2 py-2 border-t first:border-t-0">
      <Building2 className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:underline decoration-primary/60 underline-offset-2"
            >
              {title}
            </a>
          ) : title}
          {p.action_required && (
            <Badge variant="outline" className="text-[10px] border-blue-500/30 text-blue-600 dark:text-blue-400">
              action
            </Badge>
          )}
          {p.possible_duplicate && (
            <Badge variant="outline" className="text-[10px] border-amber-500/30 text-amber-700 dark:text-amber-400">
              possible duplicate
            </Badge>
          )}
          {p.import_status === "imported" && (
            <Badge variant="outline" className="text-[10px] border-emerald-500/30 text-emerald-700 dark:text-emerald-400">
              imported
            </Badge>
          )}
        </div>
        {/* Non-http(s) link from the agent — show inert, never as an href */}
        {!href && p.link && (
          <span className="text-xs text-muted-foreground break-all mt-0.5 block">{p.link}</span>
        )}
      </div>
    </div>
  );
}

function InteractionRow({ i }: { i: InboxInteraction }) {
  return (
    <div className="flex items-center gap-2 py-2 border-t text-sm">
      <CircleDot className="h-4 w-4 text-muted-foreground shrink-0" />
      <span>
        Status update:{" "}
        <span className="text-muted-foreground">{i.previous_status ?? "—"}</span>
        {" → "}
        <span className="font-medium">{i.new_status ?? "needs review"}</span>
        {" "}
        <span className="text-muted-foreground">({Math.round(i.match_confidence * 100)}% match)</span>
      </span>
    </div>
  );
}

// ─── Email row (expandable) ───────────────────────────────────

function EmailRow({
  email, expanded, onToggle, onPatch, onDelete, busy,
}: {
  email: InboxEmail;
  expanded: boolean;
  onToggle: () => void;
  onPatch: (status: EmailStatus) => void;
  onDelete: () => void;
  busy: boolean;
}) {
  const needs = email.status === "needs_review";
  return (
    <div
      className={cn(
        "rounded-lg border bg-card",
        needs && "border-amber-500/40 bg-amber-500/[0.04]",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left"
      >
        {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                  : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">{email.subject || "(no subject)"}</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground truncate">
            <span className="truncate">{email.sender}</span>
            <span>·</span>
            <span className="shrink-0">{fmtDateTime(email.received_at)}</span>
          </div>
        </div>
        <Badge variant="secondary" className="shrink-0 hidden sm:inline-flex">
          {CATEGORY_LABELS[email.category] ?? email.category}
        </Badge>
        <span className="text-xs text-muted-foreground shrink-0 w-10 text-right tabular-nums">
          {Math.round(email.confidence * 100)}%
        </span>
        <StatusBadge status={email.status} />
      </button>

      {expanded && (
        <div className="px-4 pb-3 pt-1 border-t">
          {needs && email.escalation_reason && (
            <div className="flex items-start gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-sm mb-2">
              <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-500 shrink-0 mt-0.5" />
              <span><strong>Needs review:</strong> {email.escalation_reason}</span>
            </div>
          )}

          {email.postings.length > 0 && (
            <div className="mb-1">
              {email.postings.map((p) => <PostingRow key={p.id} p={p} />)}
            </div>
          )}
          {email.interactions.map((i) => <InteractionRow key={i.id} i={i} />)}
          {email.postings.length === 0 && email.interactions.length === 0 && (
            <p className="text-sm text-muted-foreground py-2">No postings or status updates extracted.</p>
          )}

          <div className="flex items-center gap-2 mt-3 pt-2 border-t">
            {email.status !== "processed" && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onPatch("processed")}>
                <Check className="h-3.5 w-3.5 mr-1" /> Mark handled
              </Button>
            )}
            {email.status !== "discarded" && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => onPatch("discarded")}>
                <EyeOff className="h-3.5 w-3.5 mr-1" /> Dismiss
              </Button>
            )}
            <div className="flex-1" />
            <Button
              size="sm" variant="ghost" disabled={busy} onClick={onDelete}
              className="text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="h-3.5 w-3.5 mr-1" /> Delete
            </Button>
            {email.langfuse_trace_id && (
              <span className="text-[10px] text-muted-foreground font-mono hidden md:inline" title="Langfuse trace id">
                {email.langfuse_trace_id}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────

export function InboxPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<EmailStatus | "all">("all");
  const [category, setCategory] = useState<EmailCategory | "all">("all");
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<InboxEmail | null>(null);

  const params: Record<string, string | number> = { skip: page * PAGE_SIZE, limit: PAGE_SIZE };
  if (status !== "all") params.status = status;
  if (category !== "all") params.category = category;

  const { data, isLoading } = useQuery<PaginatedInbox>({
    queryKey: ["inbox", status, category, page],
    queryFn: () => agentApi.get("/agent/inbox", { params }).then((r) => r.data),
  });

  // Lightweight needs-review count for the header chip
  const { data: needsReview } = useQuery<PaginatedInbox>({
    queryKey: ["inbox-needs-review"],
    queryFn: () => agentApi.get("/agent/inbox", { params: { status: "needs_review", limit: 1 } }).then((r) => r.data),
  });

  const patchMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: EmailStatus }) =>
      agentApi.patch(`/agent/inbox/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["inbox-needs-review"] });
    },
    onError: (e: any) =>
      toast({ title: "Update failed", description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => agentApi.delete(`/agent/inbox/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["inbox-needs-review"] });
      setDeleteTarget(null);
      toast({ title: "Deleted" });
    },
    onError: (e: any) =>
      toast({ title: "Delete failed", description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const needsCount = needsReview?.total ?? 0;
  const busy = patchMut.isPending || deleteMut.isPending;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div className="flex items-center gap-2">
        <InboxIcon className="h-5 w-5 text-primary" />
        <h1 className="text-xl font-bold">Inbox</h1>
        <span className="text-sm text-muted-foreground">{total}</span>
        {needsCount > 0 && (
          <button
            type="button"
            onClick={() => { setStatus("needs_review"); setPage(0); }}
            className="ml-auto inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-1 text-xs font-medium text-amber-700 dark:text-amber-400 hover:bg-amber-500/25"
          >
            <AlertTriangle className="h-3.5 w-3.5" /> {needsCount} need review
          </button>
        )}
      </div>

      <p className="text-sm text-muted-foreground">
        Emails the agent processed. Open a row to see extracted postings and status updates. Posting
        links open the source site — import via the bookmarklet as usual.
      </p>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <Select value={status} onValueChange={(v) => { setStatus(v as EmailStatus | "all"); setPage(0); }}>
          <SelectTrigger className="w-[160px]"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="needs_review">Needs review</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="processed">Processed</SelectItem>
            <SelectItem value="discarded">Discarded</SelectItem>
          </SelectContent>
        </Select>
        <Select value={category} onValueChange={(v) => { setCategory(v as EmailCategory | "all"); setPage(0); }}>
          <SelectTrigger className="w-[170px]"><SelectValue placeholder="Category" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All categories</SelectItem>
            <SelectItem value="recruiter_outreach">Recruiter</SelectItem>
            <SelectItem value="application_confirmation">Application</SelectItem>
            <SelectItem value="job_alert">Job alert</SelectItem>
            <SelectItem value="network_notification">Network</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <InboxIcon className="h-8 w-8 mx-auto mb-2 opacity-40" />
          <p className="text-sm">No emails {status !== "all" || category !== "all" ? "match these filters" : "yet"}.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((email) => (
            <EmailRow
              key={email.id}
              email={email}
              expanded={expandedId === email.id}
              onToggle={() => setExpandedId(expandedId === email.id ? null : email.id)}
              onPatch={(s) => patchMut.mutate({ id: email.id, status: s })}
              onDelete={() => setDeleteTarget(email)}
              busy={busy}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm">
          <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Prev</Button>
          <span className="text-muted-foreground">Page {page + 1} of {totalPages}</span>
          <Button size="sm" variant="outline" disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      )}

      {/* Delete confirm */}
      <Dialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete this inbox entry?</DialogTitle>
            <DialogDescription>
              This removes the email and its extracted postings from your inbox. It does not affect any
              jobs you've already imported.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={deleteMut.isPending}
              onClick={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
            >
              {deleteMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
