import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Copy, Loader2, Plus } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { Switch } from "../components/ui/switch";
import { agentApi, jobsApi } from "../lib/api";
import type { InboxPostingDetail } from "../lib/types";
import { toast } from "../hooks/useToast";

interface BookmarkletData {
  title?: string;
  company?: string;
  url?: string;
  location?: string;
  description?: string;
  external_id?: string;
  remote?: boolean;
  source?: string;
  salary_min?: number;
  salary_max?: number;
}

function decodeHash(): BookmarkletData {
  try {
    const hash = window.location.hash.slice(1);
    if (!hash) return {};
    const json = decodeURIComponent(escape(atob(hash)));
    return JSON.parse(json);
  } catch {
    return {};
  }
}

/** "$187,500", "187,500" or "187500" → 187500. Blank or no digits → null. */
function parseSalary(raw: string): number | null {
  const n = parseFloat(raw.replace(/[^0-9.]/g, ""));
  return Number.isFinite(n) ? Math.round(n) : null;
}

export function AddJobPage() {
  const navigate = useNavigate();
  const [saving, setSaving] = useState(false);
  // Set when the import matched a role already in the list from another listing
  // (same company, title and city): offer to open it or add separately.
  const [sameRole, setSameRole] = useState<{ id: string; title: string; status: string; url: string } | null>(null);

  // Pre-fill from bookmarklet hash on first render
  const [prefilled] = useState<BookmarkletData>(() => decodeHash());

  const [title, setTitle] = useState(prefilled.title ?? "");
  const [company, setCompany] = useState(prefilled.company ?? "");
  const [url, setUrl] = useState(prefilled.url ?? "");
  const [location, setLocation] = useState(prefilled.location ?? "");
  const [remote, setRemote] = useState(prefilled.remote ?? false);
  const [salaryMin, setSalaryMin] = useState(prefilled.salary_min ? String(prefilled.salary_min) : "");
  const [salaryMax, setSalaryMax] = useState(prefilled.salary_max ? String(prefilled.salary_max) : "");
  const [description, setDescription] = useState(prefilled.description ?? "");
  const source = prefilled.source ?? "manual";
  const externalId = prefilled.external_id ?? "";

  // Opened from an inbox posting (Add job on a recruiter email): pre-fill from
  // what the agent extracted, and once saved, mark the posting imported.
  const [searchParams] = useSearchParams();
  const fromPosting = searchParams.get("from_posting");
  const [posting, setPosting] = useState<InboxPostingDetail | null>(null);
  useEffect(() => {
    if (!fromPosting) return;
    agentApi.get<InboxPostingDetail>(`/agent/inbox/postings/${fromPosting}`)
      .then(({ data }) => {
        setPosting(data);
        setTitle(data.role);
        setCompany(data.company);
        setUrl(data.link ?? "");
        setLocation(data.location ?? "");
        setRemote(!!data.remote);
        setSalaryMin(data.salary_min ? String(data.salary_min) : "");
        setSalaryMax(data.salary_max ? String(data.salary_max) : "");
        setDescription(data.description ?? "");
      })
      .catch(() => toast({ title: "Couldn't load that inbox posting", variant: "destructive" }));
  }, [fromPosting]);

  async function markImported(reviewId: string) {
    if (!fromPosting) return;
    try {
      await agentApi.post(`/agent/inbox/postings/${fromPosting}/imported`, { review_id: reviewId });
    } catch {
      // The job is saved either way; the inbox just won't show "imported" yet.
    }
  }

  // Clear the hash once we've read it so it doesn't linger in history
  useEffect(() => {
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !company.trim()) {
      toast({ title: "Title and company are required", variant: "destructive" });
      return;
    }
    await save(false);
  }

  async function save(separate: boolean) {
    setSaving(true);
    try {
      const payload = {
        title: title.trim(),
        company: company.trim(),
        url: url.trim(),
        source,
        location: location.trim() || null,
        remote,
        salary_min: parseSalary(salaryMin),
        salary_max: parseSalary(salaryMax),
        description: description.trim() || null,
        external_id: externalId || null,
      };
      const res = await jobsApi.post("/jobs/manual" + (separate ? "?separate=true" : ""), payload);
      const review = res.data;
      if (review.already_had === "same_role") {
        // Same role from another listing — not added, not re-scored. Ask rather
        // than assume: it may be a genuinely separate opening.
        setSameRole({ id: review.id, title: review.title, status: review.status, url: review.url });
        await markImported(review.id);     // the posting IS that job, unless they add separately
        return;
      }
      await markImported(review.id);
      if (res.status === 200) {
        toast({ title: "Already in your list", description: "This job was already saved — not re-added." });
      } else {
        toast({ title: "Job added!", description: "AI review has been queued." });
      }
      navigate(`/jobs/${review.id}`);
    } catch (err: any) {
      toast({
        title: "Failed to add job",
        description: err?.response?.data?.detail ?? "Unknown error",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  }

  const fromLinkedIn = source === "linkedin";

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/jobs")} aria-label="Back to jobs">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-xl font-bold">
            {fromLinkedIn ? "Add LinkedIn Job" : fromPosting ? "Add job from recruiter email" : "Add Job Manually"}
          </h1>
          {fromLinkedIn && (
            <p className="text-sm text-muted-foreground mt-0.5">
              Review the details captured from LinkedIn, then save.
            </p>
          )}
          {posting && (
            <p className="text-sm text-muted-foreground mt-0.5">
              Filled in from “{posting.email_subject}”. Check it over, then save. An hourly rate
              is shown as a yearly salary at 40 hours a week.
            </p>
          )}
        </div>
      </div>

      {sameRole && (
        <div className="rounded-lg border border-amber-400/50 bg-amber-50/60 dark:bg-amber-950/20 p-4 space-y-3">
          <div className="flex items-start gap-2">
            <Copy className="h-4 w-4 text-amber-700 dark:text-amber-400 shrink-0 mt-0.5" />
            <div className="text-sm space-y-1">
              <p className="font-medium">You already have this job</p>
              <p className="text-muted-foreground">
                <span className="text-foreground">{sameRole.title}</span> · {sameRole.status.replace(/_/g, " ")} — same
                company, title and city. It wasn't added again or re-scored.
                {sameRole.url === url.trim() && " Your entry now uses the link you imported."}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => navigate(`/jobs/${sameRole.id}`)}>Open it</Button>
            <Button size="sm" variant="outline" disabled={saving} onClick={() => save(true)}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Plus className="h-4 w-4 mr-1" />}
              Add as a separate job anyway
            </Button>
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Title + Company */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="title">
              Job title <span className="text-destructive">*</span>
            </Label>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Senior Software Engineer"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="company">
              Company <span className="text-destructive">*</span>
            </Label>
            <Input
              id="company"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="Acme Corp"
              required
            />
          </div>
        </div>

        {/* URL */}
        <div className="space-y-1.5">
          <Label htmlFor="url">
            Job posting URL <span className="text-xs text-muted-foreground font-normal">(optional — recruiter emails often have none)</span>
          </Label>
          <Input
            id="url"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.linkedin.com/jobs/view/…"
          />
        </div>

        {/* Location + Remote */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="location">Location</Label>
            <Input
              id="location"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="New York, NY"
            />
          </div>
          <div className="flex items-center gap-3 pt-7">
            <Switch
              id="remote"
              checked={remote}
              onCheckedChange={setRemote}
            />
            <Label htmlFor="remote">Remote position</Label>
          </div>
        </div>

        {/* Salary */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="salMin">Salary min (USD)</Label>
            <Input
              id="salMin"
              // Text, not type="number": no spinner arrows or scroll-wheel nudges,
              // no step rule (step=1000 once refused $187,500), and "187,500" or
              // "$187,500" can be typed or pasted as written. parseSalary reads it.
              type="text"
              inputMode="numeric"
              value={salaryMin}
              onChange={(e) => setSalaryMin(e.target.value)}
              placeholder="120,000"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="salMax">Salary max (USD)</Label>
            <Input
              id="salMax"
              type="text"
              inputMode="numeric"
              value={salaryMax}
              onChange={(e) => setSalaryMax(e.target.value)}
              placeholder="160,000"
            />
          </div>
        </div>

        {/* Description */}
        <div className="space-y-1.5">
          <Label htmlFor="description">
            Job description
            {fromLinkedIn && (
              <span className="ml-2 text-xs text-muted-foreground">(captured from LinkedIn)</span>
            )}
          </Label>
          <Textarea
            id="description"
            rows={12}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Paste the full job description here so the AI can score it against your profile…"
            className="font-mono text-xs leading-relaxed"
          />
        </div>

        {/* Actions */}
        <div className="flex gap-3 pt-2">
          <Button type="button" variant="outline" onClick={() => navigate("/jobs")}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving} className="gap-1.5">
            {saving ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Saving…</>
            ) : (
              <><Plus className="h-4 w-4" /> Add job</>
            )}
          </Button>
        </div>
      </form>
    </div>
  );
}
