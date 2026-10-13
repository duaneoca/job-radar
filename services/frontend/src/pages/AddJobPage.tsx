import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Copy, Loader2, Plus } from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { Switch } from "../components/ui/switch";
import { jobsApi } from "../lib/api";
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

  // Clear the hash once we've read it so it doesn't linger in history
  useEffect(() => {
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !company.trim() || !url.trim()) {
      toast({ title: "Title, company and URL are required", variant: "destructive" });
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
        salary_min: salaryMin ? parseInt(salaryMin, 10) : null,
        salary_max: salaryMax ? parseInt(salaryMax, 10) : null,
        description: description.trim() || null,
        external_id: externalId || null,
      };
      const res = await jobsApi.post("/jobs/manual" + (separate ? "?separate=true" : ""), payload);
      const review = res.data;
      if (review.already_had === "same_role") {
        // Same role from another listing — not added, not re-scored. Ask rather
        // than assume: it may be a genuinely separate opening.
        setSameRole({ id: review.id, title: review.title, status: review.status, url: review.url });
        return;
      }
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
            {fromLinkedIn ? "Add LinkedIn Job" : "Add Job Manually"}
          </h1>
          {fromLinkedIn && (
            <p className="text-sm text-muted-foreground mt-0.5">
              Review the details captured from LinkedIn, then save.
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
            Job posting URL <span className="text-destructive">*</span>
          </Label>
          <Input
            id="url"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.linkedin.com/jobs/view/…"
            required
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
              type="number"
              min={0}
              step={1000}
              value={salaryMin}
              onChange={(e) => setSalaryMin(e.target.value)}
              placeholder="120000"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="salMax">Salary max (USD)</Label>
            <Input
              id="salMax"
              type="number"
              min={0}
              step={1000}
              value={salaryMax}
              onChange={(e) => setSalaryMax(e.target.value)}
              placeholder="160000"
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
