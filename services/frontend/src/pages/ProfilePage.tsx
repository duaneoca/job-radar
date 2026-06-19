import { useState, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, Trash2, Loader2, Search, Lock } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Textarea } from "../components/ui/textarea";
import { Separator } from "../components/ui/separator";
import { Badge } from "../components/ui/badge";
import { profileApi, criteriaApi, connectionsApi } from "../lib/api";
import { toast } from "../hooks/useToast";
import type { Profile, Criteria, LinkedInConnection, WorkStyle, ApplicationTemplate, CareerStory } from "../lib/types";

// ─── Shared constants ─────────────────────────────────────────────────────────

const WORK_STYLE_OPTIONS: { value: WorkStyle; label: string; description: string }[] = [
  { value: "remote",  label: "Remote",     description: "Only remote positions" },
  { value: "hybrid",  label: "Hybrid OK",  description: "Remote or hybrid" },
  { value: "onsite",  label: "Onsite OK",  description: "Any work arrangement" },
  { value: "any",     label: "Any",        description: "No preference" },
];

const DEFAULT_INTERVIEW_PREP_PROMPT =
  `You are an experienced hiring manager preparing to interview a candidate for the role below.\n\nGenerate 12–15 interview questions you would realistically ask, covering four categories:\n- Behavioral (past experience stories — "Tell me about a time…")\n- Technical (skills, tools, and role-specific knowledge)\n- Situational (hypothetical scenarios — "What would you do if…")\n- Culture/Motivation (fit, values, goals — "Why this company?", "Where do you see yourself in 5 years?")\n\nFor each question write a coaching note that:\n1. Names the best career story or experience from the candidate's background to draw on (use story titles if provided)\n2. Specifies what outcome or angle to emphasize for this specific role\n3. Notes any direct connection to language or requirements in the job description`;

const DEFAULT_SCORING_PROMPT =
`# Job Fit Reviewer

## Role
You are an expert job fit evaluator. Your job is to assess how well a job posting matches a candidate's profile and search criteria. You are objective, thorough, and honest — a low score is more useful than a falsely optimistic one.

## Evaluation Dimensions

### 1. Skills Match
Skills matches can be fuzzy. There are skills that have different names, but are transferrable. For the skills listed in the job posting, count the number of skills that match or are transferrable. Take the percentage of the matches, and add 20% up to 100%, then provide a ranking from 1-10 based on that percentage.

### 2. Experience Match
Apply the same matching for experience as the skills matching.

### 3. Location
Employers may expect full time in office, hybrid, or remote. Evaluate compatibility between employer and applicant. No overlap = 1, remote-to-remote = 10. Hybrid/onsite scored 2–9 based on commute distance.

### 4. Education
10 = degree match, 8 = related degree, 5 = level matched, 3 = one level below requested, 1 = none of the above.

### 5. Salary
10 = well above desired salary, 8–9 = somewhat above, 5–7 = desired salary within range, 2–4 = tight fit, 1 = below desired salary, 5 = no salary listed.

## Scoring
Overall score = evenly weighted average of the five ranks, rounded to one decimal.`;

// The locked honesty contract, shown read-only above the editable style prompt so
// the user sees exactly what's always enforced (the real enforcement is server-side).
const HONESTY_CONTRACT_DISPLAY = `Always enforced — cannot be edited (here for your protection):
• Meet-or-exceed, never inflate — a qualification may be phrased to meet/exceed a posting only when your true value already clears it (e.g. "8+ years" if you have at least 8); never claim beyond reality.
• Never invent or fabricate skills, technologies, employers, titles, dates, durations, or certifications not in your résumé.
• Keep factual anchors — company names, job titles, employers, and dates are not changed.
• Preserve structure — rephrase in place; don't add or remove bullets, jobs, or sections.
Changes that touch a factual claim are flagged for your review in the diff.`;

const DEFAULT_RESUME_TAILOR_PROMPT = `Tailoring style:
- Mirror the posting's terminology and priorities; lead each role with the experience most relevant to THIS job.
- Prefer the posting's exact technology names where they're a true synonym for what you used.
- Keep your voice; concise, results-first bullets. Don't pad.`;

const DEFAULT_RESEARCH_PROMPT =
  `Summarize this company based on the job posting:\n1. What they do and their market position\n2. Culture and work environment signals from the posting\n3. Growth stage / stability signals\n4. Why this role could be a good fit given the candidate's background`;

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

// ─── Resume tab ───────────────────────────────────────────────────────────────

function ResumeTab() {
  const qc = useQueryClient();
  const { data: profile } = useQuery<Profile>({
    queryKey: ["profile"],
    queryFn: () => profileApi.get("/profile").then((r) => r.data),
  });

  const [resumeText, setResumeText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const text = resumeText ?? profile?.resume_text ?? "";

  async function save() {
    setSaving(true);
    try {
      await profileApi.put("/profile", { resume_text: text });
      qc.invalidateQueries({ queryKey: ["profile"] });
      setResumeText(null);
      toast({ title: "Resume saved" });
    } catch {
      toast({ title: "Failed to save resume", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setResumeText((ev.target?.result as string) ?? "");
    reader.readAsText(file);
  }

  return (
    <div className="max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-medium">Resume</h3>
          <p className="text-sm text-muted-foreground">Paste your resume text or upload a markdown/text file. Used by AI to score job matches.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
          <Upload className="h-4 w-4 mr-1" />
          Upload file
        </Button>
        <input ref={fileRef} type="file" accept=".txt,.md,.markdown" className="hidden" onChange={handleFile} />
      </div>
      <Textarea
        rows={20}
        className="font-mono text-xs"
        placeholder="Paste your resume here (plain text or Markdown)…"
        value={text}
        onChange={(e) => setResumeText(e.target.value)}
      />
      <Button onClick={save} disabled={saving || text === (profile?.resume_text ?? "")}>
        {saving ? "Saving…" : "Save resume"}
      </Button>
    </div>
  );
}

// ─── Career Stories tab ───────────────────────────────────────────────────────

function CareerStoriesTab() {
  const qc = useQueryClient();
  const { data: profile } = useQuery<Profile>({
    queryKey: ["profile"],
    queryFn: () => profileApi.get("/profile").then((r) => r.data),
  });

  const [stories, setStories] = useState<CareerStory[] | null>(null);
  const [editingStory, setEditingStory] = useState<number | "new" | null>(null);
  const [storyDraft, setStoryDraft] = useState<CareerStory>({ title: "", content: "" });
  const [savingStories, setSavingStories] = useState(false);
  const displayStories = stories ?? profile?.career_stories ?? [];

  async function saveStories(updated: CareerStory[]) {
    setSavingStories(true);
    try {
      await profileApi.put("/profile", { career_stories: updated });
      qc.invalidateQueries({ queryKey: ["profile"] });
      setStories(updated);
      toast({ title: "Career stories saved" });
    } catch {
      toast({ title: "Failed to save stories", variant: "destructive" });
    } finally {
      setSavingStories(false);
    }
  }

  function startEdit(idx: number) {
    setStoryDraft({ ...displayStories[idx] });
    setEditingStory(idx);
  }

  function cancelEdit() {
    setEditingStory(null);
    setStoryDraft({ title: "", content: "" });
  }

  async function commitEdit() {
    if (!storyDraft.title.trim()) return;
    const updated = editingStory === "new"
      ? [...displayStories, storyDraft]
      : displayStories.map((s, i) => (i === editingStory ? storyDraft : s));
    await saveStories(updated);
    cancelEdit();
  }

  async function deleteStory(idx: number) {
    await saveStories(displayStories.filter((_, i) => i !== idx));
  }

  function StoryForm() {
    return (
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <input
          className="w-full text-sm font-medium bg-transparent border-b pb-1 outline-none focus:border-primary"
          placeholder="Story title (e.g. 'Led infrastructure migration at Acme')"
          value={storyDraft.title}
          onChange={(e) => setStoryDraft((d) => ({ ...d, title: e.target.value }))}
          autoFocus
        />
        <Textarea
          rows={8}
          className="text-sm"
          placeholder="The story — situation, what you did, and the outcome. The AI will reference this by title and use the content to suggest specific angles to emphasise in interviews."
          value={storyDraft.content}
          onChange={(e) => setStoryDraft((d) => ({ ...d, content: e.target.value }))}
        />
        <div className="flex gap-2 justify-end">
          <Button size="sm" variant="ghost" onClick={cancelEdit}>Cancel</Button>
          <Button size="sm" onClick={commitEdit} disabled={savingStories || !storyDraft.title.trim()}>
            {savingStories ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
            Save story
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <h3 className="font-medium">Career stories</h3>
        <p className="text-sm text-muted-foreground mt-0.5">
          Your reusable STAR-method stories and key wins. The AI references these by name when generating interview coaching notes — so a good title matters.
        </p>
      </div>

      {displayStories.map((story, idx) => (
        <div key={idx} className="rounded-lg border bg-card">
          {editingStory === idx ? (
            <div className="p-4 space-y-3">
              <input
                className="w-full text-sm font-medium bg-transparent border-b pb-1 outline-none focus:border-primary"
                placeholder="Story title"
                value={storyDraft.title}
                onChange={(e) => setStoryDraft((d) => ({ ...d, title: e.target.value }))}
                autoFocus
              />
              <Textarea
                rows={8}
                className="text-sm"
                placeholder="The story — situation, what you did, and the outcome…"
                value={storyDraft.content}
                onChange={(e) => setStoryDraft((d) => ({ ...d, content: e.target.value }))}
              />
              <div className="flex gap-2 justify-end">
                <Button size="sm" variant="ghost" onClick={cancelEdit}>Cancel</Button>
                <Button size="sm" onClick={commitEdit} disabled={savingStories || !storyDraft.title.trim()}>
                  {savingStories ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
                  Save story
                </Button>
              </div>
            </div>
          ) : (
            <div className="p-4">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium">{story.title}</p>
                <div className="flex gap-1 shrink-0">
                  <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => startEdit(idx)}>Edit</Button>
                  <Button
                    variant="ghost" size="sm"
                    className="h-7 px-2 text-destructive hover:bg-destructive/10"
                    onClick={() => deleteStory(idx)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
              {story.content && (
                <p className="text-xs text-muted-foreground mt-1.5 line-clamp-3 leading-relaxed">{story.content}</p>
              )}
            </div>
          )}
        </div>
      ))}

      {editingStory === "new" ? <StoryForm /> : (
        <Button variant="outline" size="sm" onClick={() => { setStoryDraft({ title: "", content: "" }); setEditingStory("new"); }} disabled={editingStory !== null}>
          + Add career story
        </Button>
      )}
    </div>
  );
}

// ─── Job Criteria tab ─────────────────────────────────────────────────────────

function CriteriaTab() {
  const qc = useQueryClient();
  const { data: criteria } = useQuery<Criteria>({
    queryKey: ["criteria"],
    queryFn: () => criteriaApi.get("/criteria").then((r) => r.data),
  });

  const [form, setForm] = useState<Partial<Criteria> | null>(null);
  const [saving, setSaving] = useState(false);
  const c = form ?? criteria ?? {};

  function set(key: keyof Criteria, value: any) {
    setForm((f) => ({ ...(f ?? criteria ?? {}), [key]: value }));
  }

  function handleListChange(key: keyof Criteria, raw: string) {
    set(key, raw.split("\n"));
  }

  async function save() {
    setSaving(true);
    try {
      const listKeys: (keyof Criteria)[] = ["job_titles", "search_locations", "excluded_companies", "target_companies"];
      const payload = { ...c };
      for (const k of listKeys) {
        if (Array.isArray(payload[k])) {
          (payload as any)[k] = (payload[k] as string[]).map((s) => s.trim()).filter(Boolean);
        }
      }
      await criteriaApi.put("/criteria", payload);
      qc.invalidateQueries({ queryKey: ["criteria"] });
      setForm(null);
      toast({ title: "Criteria saved" });
    } catch {
      toast({ title: "Failed to save criteria", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <div className="space-y-1.5">
        <Label>Job titles</Label>
        <p className="text-xs text-muted-foreground">One per line — used by scrapers to find matching postings.</p>
        <Textarea
          rows={4}
          value={(c.job_titles ?? []).join("\n")}
          onChange={(e) => handleListChange("job_titles", e.target.value)}
          placeholder={"Software Engineer\nProduct Manager\nData Scientist"}
        />
      </div>

      <Separator />

      <div className="space-y-2">
        <Label>Work arrangement</Label>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {WORK_STYLE_OPTIONS.map(({ value, label, description }) => (
            <button
              key={value}
              type="button"
              onClick={() => set("work_style", value)}
              className={`flex flex-col items-center gap-0.5 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                (c.work_style ?? "any") === value
                  ? "border-primary bg-primary/5 text-foreground font-medium"
                  : "text-muted-foreground hover:border-muted-foreground/50 hover:text-foreground"
              }`}
            >
              <span>{label}</span>
              <span className="text-xs opacity-70 text-center leading-tight">{description}</span>
            </button>
          ))}
        </div>
      </div>

      <Separator />

      <div className="space-y-4">
        <div>
          <Label>Your home city</Label>
          <p className="text-xs text-muted-foreground mt-0.5">Used to estimate commute distance for onsite/hybrid roles.</p>
          <Input
            className="mt-1.5"
            value={c.home_city ?? ""}
            onChange={(e) => set("home_city", e.target.value || null)}
            placeholder="Austin, TX"
          />
        </div>
        <div>
          <Label>Max commute</Label>
          <p className="text-xs text-muted-foreground mt-0.5">Jobs beyond this distance get penalized in scoring. Leave blank for no limit.</p>
          <div className="flex items-center gap-2 mt-1.5">
            <Input
              type="number" min={0} className="w-24"
              value={c.max_commute_miles ?? ""}
              onChange={(e) => set("max_commute_miles", e.target.value ? Number(e.target.value) : null)}
              placeholder="25"
            />
            <span className="text-sm text-muted-foreground">miles</span>
          </div>
        </div>
        <div>
          <Label>Search regions</Label>
          <p className="text-xs text-muted-foreground mt-0.5">Area keywords fed to scrapers (one per line).</p>
          <Textarea
            className="mt-1.5" rows={3}
            value={(c.search_locations ?? []).join("\n")}
            onChange={(e) => handleListChange("search_locations", e.target.value)}
            placeholder={"New York, NY\nAustin, TX\nChicago, IL"}
          />
        </div>
      </div>

      <Separator />

      <div className="space-y-1.5">
        <Label>Minimum salary</Label>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">$</span>
          <Input
            type="number" className="w-36"
            value={c.min_salary ?? ""}
            onChange={(e) => set("min_salary", e.target.value ? Number(e.target.value) : null)}
            placeholder="150,000"
          />
          <span className="text-sm text-muted-foreground">per year</span>
        </div>
      </div>

      <Separator />

      <div className="grid sm:grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>Target companies</Label>
          <p className="text-xs text-muted-foreground">Score boost — AI prioritizes these.</p>
          <Textarea
            rows={3}
            value={(c.target_companies ?? []).join("\n")}
            onChange={(e) => handleListChange("target_companies", e.target.value)}
            placeholder={"Acme Corp\nGlobex\nInitech"}
          />
        </div>
        <div className="space-y-1.5">
          <Label>Excluded companies</Label>
          <p className="text-xs text-muted-foreground">Never surface jobs from these.</p>
          <Textarea
            rows={3}
            value={(c.excluded_companies ?? []).join("\n")}
            onChange={(e) => handleListChange("excluded_companies", e.target.value)}
          />
        </div>
      </div>

      <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save criteria"}</Button>
    </div>
  );
}

// ─── AI Prompts tab ───────────────────────────────────────────────────────────

function AIPromptsTab() {
  const qc = useQueryClient();
  const { data: criteria } = useQuery<Criteria>({
    queryKey: ["criteria"],
    queryFn: () => criteriaApi.get("/criteria").then((r) => r.data),
  });

  const [form, setForm] = useState<Partial<Criteria> | null>(null);
  const [saving, setSaving] = useState(false);
  const c = form ?? criteria ?? {};

  function set(key: keyof Criteria, value: any) {
    setForm((f) => ({ ...(f ?? criteria ?? {}), [key]: value }));
  }

  async function save() {
    setSaving(true);
    try {
      await criteriaApi.put("/criteria", { ...c });
      qc.invalidateQueries({ queryKey: ["criteria"] });
      setForm(null);
      toast({ title: "AI prompts saved" });
    } catch {
      toast({ title: "Failed to save", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <div className="space-y-1.5">
        <Label>AI scoring prompt</Label>
        <p className="text-xs text-muted-foreground">
          The full rubric used to score jobs. Edit to change how dimensions are weighted or interpreted.
          Saving a blank value resets to the default.
        </p>
        <Textarea
          rows={16}
          value={c.scoring_prompt ?? DEFAULT_SCORING_PROMPT}
          onChange={(e) => set("scoring_prompt", e.target.value || null)}
          className="font-mono text-xs"
        />
      </div>

      <Separator />

      <div className="space-y-1.5">
        <Label>Voice guidelines</Label>
        <p className="text-xs text-muted-foreground">
          Describe your writing style, tone, and personal preferences. Injected into every application prompt so generated text sounds like you.
        </p>
        <Textarea
          rows={5}
          value={c.voice_guidelines ?? ""}
          onChange={(e) => set("voice_guidelines", e.target.value || null)}
          placeholder={`I write in a direct, confident tone without being boastful. I avoid buzzwords like "passionate" and "synergy". I prefer short sentences. I focus on outcomes over activities.`}
        />
      </div>

      <Separator />

      <div className="space-y-1.5">
        <Label>Company research prompt</Label>
        <p className="text-xs text-muted-foreground">Instructions for the AI when you click "Generate" in the Research tab on any job.</p>
        <Textarea
          rows={5}
          value={c.research_prompt ?? DEFAULT_RESEARCH_PROMPT}
          onChange={(e) => set("research_prompt", e.target.value || null)}
        />
      </div>

      <Separator />

      <div className="space-y-3">
        <div>
          <Label>Application templates</Label>
          <p className="text-xs text-muted-foreground mt-0.5">
            Each template appears as a section in the Application tab on every job. Voice guidelines are automatically included with each prompt.
          </p>
        </div>
        {(c.application_templates ?? DEFAULT_APP_TEMPLATES).map((tmpl, idx) => (
          <div key={idx} className="rounded-lg border p-3 space-y-2 bg-muted/20">
            <div className="flex items-center gap-2">
              <Input
                className="flex-1 h-8 text-sm font-medium"
                value={tmpl.label}
                onChange={(e) => {
                  const updated = [...(c.application_templates ?? DEFAULT_APP_TEMPLATES)];
                  updated[idx] = { ...updated[idx], label: e.target.value };
                  set("application_templates", updated);
                }}
                placeholder="Question label"
              />
              <Button
                variant="ghost" size="sm"
                className="h-8 px-2 text-destructive hover:bg-destructive/10"
                onClick={() => {
                  const updated = (c.application_templates ?? DEFAULT_APP_TEMPLATES).filter((_, i) => i !== idx);
                  set("application_templates", updated);
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
            <Textarea
              rows={3} className="text-sm"
              value={tmpl.prompt}
              onChange={(e) => {
                const updated = [...(c.application_templates ?? DEFAULT_APP_TEMPLATES)];
                updated[idx] = { ...updated[idx], prompt: e.target.value };
                set("application_templates", updated);
              }}
              placeholder="Instructions for the AI when generating this answer…"
            />
          </div>
        ))}
        <Button
          variant="outline" size="sm"
          onClick={() => {
            const current = c.application_templates ?? DEFAULT_APP_TEMPLATES;
            set("application_templates", [...current, { label: "", prompt: "" }]);
          }}
        >
          + Add template
        </Button>
      </div>

      <Separator />

      <div className="space-y-1.5">
        <Label>Interview prep prompt</Label>
        <p className="text-xs text-muted-foreground">Instructions for the AI when generating interview questions and coaching notes.</p>
        <Textarea
          rows={8}
          value={c.interview_prep_prompt ?? DEFAULT_INTERVIEW_PREP_PROMPT}
          onChange={(e) => set("interview_prep_prompt", e.target.value || null)}
        />
      </div>

      <Separator />

      <div className="space-y-1.5">
        <Label>Résumé tailoring</Label>
        <p className="text-xs text-muted-foreground">
          How the AI realigns your résumé to a specific job posting. The honesty rules below
          are always applied and can't be edited — your style guidance layers on top.
        </p>
        <div className="rounded-md border border-amber-300/60 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900 p-3 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-amber-800 dark:text-amber-300 mb-1">
            <Lock className="h-3.5 w-3.5" /> Always enforced — not editable
          </div>
          <pre className="whitespace-pre-wrap font-sans text-amber-900/90 dark:text-amber-200/80 leading-relaxed">{HONESTY_CONTRACT_DISPLAY}</pre>
        </div>
        <Textarea
          rows={6}
          value={c.resume_tailor_prompt ?? DEFAULT_RESUME_TAILOR_PROMPT}
          onChange={(e) => set("resume_tailor_prompt", e.target.value || null)}
          placeholder={DEFAULT_RESUME_TAILOR_PROMPT}
        />
      </div>

      <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save AI prompts"}</Button>
    </div>
  );
}

// ─── Connections tab ──────────────────────────────────────────────────────────

function ConnectionsTab() {
  const qc = useQueryClient();
  const { data: connections = [] } = useQuery<LinkedInConnection[]>({
    queryKey: ["connections"],
    queryFn: () => connectionsApi.get("/connections").then((r) => r.data),
  });

  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [search, setSearch] = useState("");

  // Client-side search across name / company / position (the full list is already
  // loaded, so this is instant — no extra round trips).
  const query = search.trim().toLowerCase();
  const filtered = query
    ? connections.filter((c) =>
        [c.first_name, c.last_name, c.company, c.position]
          .filter(Boolean)
          .some((v) => v!.toLowerCase().includes(query)),
      )
    : connections;
  // Un-searched: keep the list short (first 20). Searching: show all matches.
  const visible = query ? filtered : filtered.slice(0, 20);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await connectionsApi.post("/connections/import?replace=true", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      qc.invalidateQueries({ queryKey: ["connections"] });
      toast({ title: `${res.data.imported} contacts imported` });
    } catch (err: any) {
      toast({ title: "Import failed", description: err?.response?.data?.detail, variant: "destructive" });
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function clearAll() {
    if (!confirm("Remove all contacts?")) return;
    try {
      await connectionsApi.delete("/connections");
      qc.invalidateQueries({ queryKey: ["connections"] });
      toast({ title: "Contacts cleared" });
    } catch {
      toast({ title: "Failed to clear", variant: "destructive" });
    }
  }

  return (
    <div className="max-w-lg space-y-6">
      <div className="space-y-2">
        <h3 className="font-medium">Import LinkedIn connections</h3>
        <p className="text-sm text-muted-foreground">
          Export your first-degree connections from LinkedIn (Settings → Data Privacy → Get a copy of your data → Connections)
          and upload the <code className="text-xs bg-muted px-1 rounded">Connections.csv</code> file below.
        </p>
        <p className="text-sm text-muted-foreground">Uploading a new file replaces your previously imported contacts.</p>
      </div>

      <div className="rounded-lg border bg-muted/30 px-4 py-3 flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">
            {connections.length > 0 ? `${connections.length} contacts` : "No contacts imported yet"}
          </p>
          {connections.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Last uploaded {new Date(connections[0].created_at).toLocaleDateString(undefined, {
                year: "numeric", month: "short", day: "numeric",
              })}
            </p>
          )}
        </div>
        {connections.length > 0 && (
          <Button variant="outline" size="sm" onClick={clearAll} className="text-destructive hover:bg-destructive/10">
            <Trash2 className="h-4 w-4 mr-1" />
            Clear all
          </Button>
        )}
      </div>

      <div className="flex gap-2">
        <Button onClick={() => fileRef.current?.click()} disabled={uploading}>
          {uploading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Upload className="h-4 w-4 mr-1" />}
          {connections.length > 0 ? "Replace contacts" : "Upload CSV"}
        </Button>
        <input ref={fileRef} type="file" accept=".csv" className="hidden" onChange={handleUpload} />
      </div>

      {connections.length > 0 && (
        <div className="space-y-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search by name, company, or position…"
              className="pl-8"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <p className="text-sm text-muted-foreground">
            {query
              ? `${filtered.length} ${filtered.length === 1 ? "match" : "matches"}`
              : connections.length > 20
                ? "Showing first 20 — search to find anyone"
                : `${connections.length} contacts`}
          </p>

          {visible.length === 0 ? (
            <p className="text-sm text-muted-foreground py-3">No contacts match "{search.trim()}".</p>
          ) : (
            <div className="rounded-lg border overflow-hidden">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-muted/50 border-b">
                    <th className="text-left px-3 py-2">Name</th>
                    <th className="text-left px-3 py-2 hidden sm:table-cell">Company</th>
                    <th className="text-left px-3 py-2 hidden md:table-cell">Position</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((c) => (
                    <tr key={c.id} className="border-b last:border-0">
                      <td className="px-3 py-1.5">{c.first_name} {c.last_name}</td>
                      <td className="px-3 py-1.5 hidden sm:table-cell text-muted-foreground">{c.company}</td>
                      <td className="px-3 py-1.5 hidden md:table-cell text-muted-foreground">{c.position}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!query && connections.length > 20 && (
                <p className="text-xs text-muted-foreground px-3 py-2">+{connections.length - 20} more — use search above</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Profile page ─────────────────────────────────────────────────────────────

export function ProfilePage() {
  const [searchParams] = useSearchParams();
  const defaultTab = searchParams.get("tab") ?? "resume";

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Profile</h1>
      <Tabs defaultValue={defaultTab}>
        <TabsList className="flex-wrap h-auto gap-1">
          <TabsTrigger value="resume">Resume</TabsTrigger>
          <TabsTrigger value="stories">Career Stories</TabsTrigger>
          <TabsTrigger value="criteria">Job Criteria</TabsTrigger>
          <TabsTrigger value="prompts">AI Prompts</TabsTrigger>
          <TabsTrigger value="connections">Connections</TabsTrigger>
        </TabsList>
        <TabsContent value="resume"      className="mt-6"><ResumeTab /></TabsContent>
        <TabsContent value="stories"     className="mt-6"><CareerStoriesTab /></TabsContent>
        <TabsContent value="criteria"    className="mt-6"><CriteriaTab /></TabsContent>
        <TabsContent value="prompts"     className="mt-6"><AIPromptsTab /></TabsContent>
        <TabsContent value="connections" className="mt-6"><ConnectionsTab /></TabsContent>
      </Tabs>
    </div>
  );
}
