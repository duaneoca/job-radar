import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Separator } from "../components/ui/separator";
import { useSearchParams } from "react-router-dom";

// ─── Section heading helpers ──────────────────────────────────────────────────

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="text-base font-semibold mt-6 mb-1 first:mt-0">{children}</h2>;
}

function H3({ children }: { children: React.ReactNode }) {
  return <h3 className="text-sm font-medium mt-4 mb-1">{children}</h3>;
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground leading-relaxed">{children}</p>;
}

function Li({ children }: { children: React.ReactNode }) {
  return <li className="text-sm text-muted-foreground leading-relaxed">{children}</li>;
}

function Callout({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-sm text-foreground leading-relaxed">
      {children}
    </div>
  );
}

// ─── Tabs ─────────────────────────────────────────────────────────────────────

function GettingStartedTab() {
  return (
    <div className="max-w-2xl space-y-4">
      <H2>Step 1 — Set up your profile</H2>
      <P>Go to <strong>Profile → Resume</strong> and paste in your resume text. This is the
        foundation for all AI features — scoring, cover letters, research summaries, and
        interview prep all draw from it.</P>
      <P>While you're there, add a few <strong>Career Stories</strong> (Profile → Career Stories).
        These are specific examples from your work history that the AI references when
        generating coaching notes and application answers.</P>

      <Separator />

      <H2>Step 2 — Set your criteria</H2>
      <P>Go to <strong>Profile → Job Criteria</strong> and fill in:</P>
      <ul className="list-disc list-inside space-y-1 ml-2">
        <Li><strong>Job titles</strong> — what roles you're targeting (one per line). The scraper uses these as search keywords.</Li>
        <Li><strong>Work arrangement</strong> — remote, hybrid, onsite, or any.</Li>
        <Li><strong>Search regions</strong> — locations fed to job boards.</Li>
        <Li><strong>Minimum salary</strong> — jobs below this get a low salary score.</Li>
        <Li><strong>Target / excluded companies</strong> — boost or hide specific employers.</Li>
      </ul>

      <Separator />

      <H2>Step 3 — Add your API keys</H2>
      <P>Go to <strong>Settings → API Keys</strong> and add, at a minimum, an{" "}
        <strong>Adzuna</strong> key (the job source) and an <strong>AI</strong> key
        (for scoring &amp; generation). A <strong>Tavily</strong> key is recommended for
        company research. Everything runs on your own keys — Job Radar never stores them in
        plaintext. See the <strong>API keys</strong> tab for what each one does and where to
        get it.</P>

      <Separator />

      <H2>Step 4 — Get jobs</H2>
      <P>Once your Adzuna key is in, jobs are scraped automatically and appear in your Jobs
        list shortly after. You can also add jobs manually from any job site using the
        bookmarklet in <strong>Settings → Bookmarklet</strong>.</P>

      <Separator />

      <H2>Step 5 — Work your pipeline</H2>
      <P>Jobs arrive scored and sorted. Open any job to see the AI breakdown, generate
        application materials, and track your status as you move through the process.</P>
    </div>
  );
}

function JobPipelineTab() {
  return (
    <div className="max-w-2xl space-y-4">
      <H2>How jobs get in</H2>
      <P>Once you've added your Adzuna key and set your criteria, Job Radar scrapes for you
        automatically every 6 hours — and right away whenever you save your criteria. It
        searches against <em>your</em> job titles and locations, and the jobs it finds are
        yours alone (not shared with other users). Sources:</P>
      <ul className="list-disc list-inside space-y-1 ml-2">
        <Li><strong>Adzuna</strong> — the main job board; searched with your own Adzuna key. No key, no Adzuna results.</Li>
        <Li><strong>The Muse</strong> — matched to the categories your job titles fall into</Li>
        <Li><strong>Remotive</strong> — remote-only roles</Li>
        <Li><strong>Company boards</strong> — every company in your <em>Target companies</em> list
          is watched directly on its Greenhouse, Ashby, or Lever job board (free, no key).
          Only roles matching your job titles are pulled in, so watching big companies
          doesn't flood your list or your AI scoring budget.</Li>
      </ul>
      <P>Each job is scored against your resume and criteria using your AI key, then sorted by
        match. You can also capture jobs directly from job sites using the bookmarklet
        (see below).</P>

      <Separator />

      <H2>Bookmarklet — adding jobs manually</H2>
      <P>The bookmarklet lets you add a job to Job Radar in one click while browsing
        any supported job site. No copy-pasting required.</P>

      <H3>Setup (one time)</H3>
      <ol className="list-decimal list-inside space-y-1 ml-2">
        <Li>Go to <strong>Settings → Bookmarklet</strong></Li>
        <Li>Drag the <strong>"Add to Job Radar"</strong> button to your browser's bookmarks bar</Li>
        <Li>If you don't see the bookmarks bar, enable it in your browser settings</Li>
      </ol>

      <H3>Using it</H3>
      <ol className="list-decimal list-inside space-y-1 ml-2">
        <Li><strong>Keep Job Radar open</strong> in another tab or window — the bookmarklet opens a new tab in Job Radar to complete the import</Li>
        <Li>Navigate to a job posting on a supported site</Li>
        <Li>Click <strong>"Add to Job Radar"</strong> in your bookmarks bar</Li>
        <Li>A new Job Radar tab opens with the job details pre-filled — review and save</Li>
        <Li>The job is added to your list and queued for AI scoring automatically</Li>
      </ol>

      <H3>Supported sites</H3>
      <ul className="list-disc list-inside space-y-1 ml-2">
        <Li><strong>LinkedIn</strong> — fully supported</Li>
        <Li><strong>Dice</strong> — fully supported</Li>
        <Li><strong>BuiltIn</strong> — fully supported</Li>
        <Li><strong>Monster</strong> — fully supported</Li>
        <Li><strong>ZipRecruiter</strong> — fully supported</Li>
        <Li><strong>Indeed</strong> — fully supported</Li>
        <Li><strong>Ashby</strong> (jobs.ashbyhq.com) — fully supported</Li>
        <Li><strong>Greenhouse</strong> (job&#8288;-&#8288;boards.greenhouse.io) — fully supported</Li>
      </ul>

      <Separator />

      <H2>Deduplication</H2>
      <P>Jobs are deduplicated by source + external ID. The scraper won't create
        duplicate listings — it only adds a job when it hasn't been seen before.</P>

      <Separator />

      <H2>AI review</H2>
      <P>Each new job is automatically queued for AI scoring. The reviewer runs per-user —
        the same job gets scored separately against each person's profile and criteria.
        Scores appear on the job card once the review completes (usually within seconds).</P>

      <Separator />

      <H2>Job statuses</H2>
      <ul className="list-disc list-inside space-y-1 ml-2">
        <Li><strong>New</strong> — just arrived, not yet actioned</Li>
        <Li><strong>Reviewed</strong> — you've looked at it</Li>
        <Li><strong>Applied</strong> — application submitted</Li>
        <Li><strong>Interviewing</strong> — active interview process</Li>
        <Li><strong>Offer</strong> — offer received</Li>
        <Li><strong>Rejected</strong> — closed out</Li>
        <Li><strong>Dismissed</strong> — not interested, hidden from default view</Li>
      </ul>

      <Separator />

      <H2>Filtering and sorting</H2>
      <P>The Jobs list defaults to showing New jobs sorted by AI score descending — highest
        matches first. Use the filters to switch status, sort by date, or search by title
        or company.</P>
    </div>
  );
}

function ScoringTab() {
  return (
    <div className="max-w-2xl space-y-4">
      <P>Each job gets an overall score from 1–10, built from five equal-weight dimensions.
        The overall score is the simple average of the five ranks. You can customise the
        entire scoring rubric in <strong>Profile → AI Prompts → AI scoring prompt</strong>.</P>

      <Separator />

      <H2>The five dimensions</H2>

      <H3>1. Skills match</H3>
      <P>Counts how many skills in the job posting match or are transferable to yours.
        Takes the match percentage, adds 20% (employers rarely expect all listed skills),
        caps at 100%, then maps to 1–10.</P>

      <H3>2. Experience match</H3>
      <P>Same fuzzy matching logic applied to years and types of experience described
        in the posting versus your background.</P>

      <H3>3. Location</H3>
      <P>Compares the job's work arrangement (remote / hybrid / onsite) with your preference
        and commute tolerance. Remote-to-remote = 10. Incompatible arrangement = 1.
        Hybrid and onsite roles are scored 2–9 based on estimated commute distance.</P>

      <H3>4. Education</H3>
      <P>10 = exact degree match · 8 = related degree · 5 = level matched ·
        3 = one level below requested · 1 = no match.</P>

      <H3>5. Salary</H3>
      <P>10 = well above your desired salary · 8–9 = somewhat above · 5–7 = within range ·
        2–4 = tight fit · 1 = below your minimum · 5 = no salary listed (neutral).</P>

      <Separator />

      <H2>Recommended flag</H2>
      <P>Jobs with an overall score ≥ 6.0 are flagged as <strong>Recommended</strong>.
        You can adjust this threshold by editing the scoring prompt.</P>

      <Separator />

      <H2>Pros and cons</H2>
      <P>Along with the numeric score, the AI writes 2–4 specific strengths and 1–3 honest
        gaps grounded in the actual job description and your profile — not generic filler.</P>

      <Separator />

      <H2>Customising the scoring</H2>
      <P>The full rubric is editable in <strong>Profile → AI Prompts → AI scoring prompt</strong>.
        The default text is pre-loaded so you can see exactly how it works and tweak from
        there. Good reasons to edit: adjust the salary bands, change how skills fuzziness
        is applied, add a sixth dimension, or emphasise certain factors more heavily.
        The JSON output format is always appended automatically and can't be changed —
        everything else is yours to modify. Saving a blank value resets to the default.</P>
    </div>
  );
}

function ApplicationToolsTab() {
  return (
    <div className="max-w-2xl space-y-4">
      <P>Open any job and use the tabs to generate AI-assisted content. All generation
        uses your own AI API key (Anthropic, OpenAI, Google, or Groq) and draws from your
        resume, career stories, and voice guidelines.</P>

      <Separator />

      <H2>Research</H2>
      <P>The Research tab generates a company summary based on the job posting. It covers
        what the company does, culture signals, growth stage, and why this role could be
        a good fit for your background.</P>
      <P>You can customise the research prompt in <strong>Profile → AI Prompts →
        Company research prompt</strong>.</P>

      <Separator />

      <H2>Application</H2>
      <P>The Application tab shows one section per template you've defined in
        <strong> Profile → AI Prompts → Application templates</strong>. Defaults include
        a cover letter, "Why do you want to work here?", and an "About me" summary.</P>
      <P>Each section generates independently. You can refine any answer with the
        chat box below it — ask the AI to make it shorter, more formal, or to emphasise
        a different aspect of your background.</P>
      <P>Your <strong>voice guidelines</strong> are automatically injected into every
        application prompt so generated text sounds like you.</P>

      <Separator />

      <H2>Interview prep</H2>
      <P>The Interview Prep tab generates 12–15 questions a hiring manager would
        realistically ask, across four categories: Behavioral, Technical, Situational,
        and Culture/Motivation.</P>
      <P>Each question includes a coaching note that names the best career story from
        your profile to draw on and the angle to emphasise for this specific role. Add
        your own notes to each card as you prepare.</P>
      <P>You can edit, delete, or add questions manually, and save your changes.</P>

      <Separator />

      <H2>Tailor résumé</H2>
      <P>The Tailor résumé tab rewrites your résumé to line up with a specific posting.
        The AI proposes targeted edits — reworded bullets, re-ordered skills, emphasis
        shifts — and shows them as a <strong>line-by-line diff you accept or reject</strong>,
        so you stay in control of every change.</P>
      <P>It's deliberately <strong>honest</strong>: a locked guideline stops it from
        inventing experience, tools, or titles you don't have. If there's a genuine gap
        between you and the role, it leaves the gap rather than papering over it.</P>
      <P>When you're happy, export a clean, templated <strong>PDF</strong> — pick a template,
        adjust font/density/margins, and print or download. Your tailored version is saved
        with the job, so you can come back to it later.</P>

      <Separator />

      <H2>Timeline</H2>
      <P>The Timeline tab keeps a running log of everything that happens with a job.
        Status changes (New → Applied → Interviewing etc.) and AI review scores are
        logged automatically. You can also add your own freeform notes — phone screen
        scheduled, recruiter name, follow-up sent — anything worth remembering.
        Events are shown newest-first.</P>
    </div>
  );
}

function PromptsTab() {
  return (
    <div className="max-w-2xl space-y-4">
      <P>All AI behaviour in Job Radar is driven by prompts you control. Go to
        <strong> Profile → AI Prompts</strong> to edit them.</P>

      <Separator />

      <H2>AI scoring prompt</H2>
      <P>This is the full rubric the AI uses when scoring a job. It defines the five
        dimensions and how each is evaluated. The default is pre-loaded so you can see
        exactly how it works. The output format (the JSON structure the code parses) is
        always appended automatically — everything else is yours to change.</P>
      <P>Good reasons to edit it: change how skills fuzziness is calculated, adjust the
        salary scoring bands, add a sixth dimension, or weight certain factors higher
        by adding explicit instructions. Saving a blank value resets to the default.</P>

      <Separator />

      <H2>Voice guidelines</H2>
      <P>Describe your writing style, tone, and preferences. This block is injected into
        every application prompt automatically. Example:</P>
      <div className="rounded-md bg-muted px-4 py-3 text-xs font-mono text-muted-foreground whitespace-pre-wrap">
        {`I write in a direct, confident tone. I avoid buzzwords like "passionate" and "synergy". I prefer short sentences and focus on outcomes over activities. I don't use em dashes.`}
      </div>

      <Separator />

      <H2>Company research prompt</H2>
      <P>Instructions for the AI when you click Generate in the Research tab. The job
        posting and your resume are always included as context — this prompt tells the AI
        what to focus on and how to structure the output.</P>

      <Separator />

      <H2>Application templates</H2>
      <P>Each template becomes a section in the Application tab for every job. The label
        is the section heading. The prompt tells the AI what to write — it's automatically
        combined with your voice guidelines and the job + resume context.</P>
      <P>You can add, remove, and reorder templates. Changes apply to all future
        generation; previously generated answers are preserved.</P>

      <Separator />

      <H2>Interview prep prompt</H2>
      <P>Controls what the AI generates when you click Generate prep. The default asks
        for 12–15 questions across four categories with career-story-linked coaching
        notes. Edit it to change the number of questions, add a fifth category, or
        shift the focus toward technical depth.</P>
    </div>
  );
}

function InboxRecruitersTab() {
  return (
    <div className="max-w-2xl space-y-4">
      <P>Beyond scraping and scoring, Job Radar can watch your inbox, keep a recruiter
        contact list, and ping you on Slack — so nothing falls through the cracks.</P>

      <Separator />

      <H2>Inbox &amp; the email agent</H2>
      <P>Connect a mailbox and the email agent reads your job-related email, sorts each
        message into a category (application confirmation, recruiter outreach, interview
        request, rejection, and so on), and matches it back to the job in your pipeline —
        logging the update on that job's timeline automatically.</P>
      <P>Anything it isn't sure about is escalated to <strong>needs review</strong>: the
        amber badge on the <strong>Inbox</strong> tab shows how many items are waiting for
        your call. It never quietly changes a status it isn't confident about.</P>
      <H3>Connecting a mailbox</H3>
      <P>Go to <strong>Settings → Email Agent</strong>. There are two ways to run it:</P>
      <ul className="list-disc list-inside space-y-1 ml-2">
        <Li><strong>Cloud (Gmail)</strong> — connect Gmail with one click via Google sign-in;
          Job Radar processes it for you on a schedule.</Li>
        <Li><strong>Local self-host</strong> — run the agent on your own machine against any
          IMAP mailbox; your mail credentials never leave your computer. See{" "}
          <strong>Settings → Email Agent → local setup</strong> for the walkthrough.</Li>
      </ul>
      <Callout>
        Your decrypted mail credentials never leave the cluster (cloud) or your machine
        (local). The agent only writes back status updates and suggestions — you stay in
        control.
      </Callout>

      <Separator />

      <H2>Recruiters</H2>
      <P>The <strong>Recruiters</strong> tab is a lightweight CRM for the people reaching out
        to you. Track each recruiter's name, employer, whether they're agency or in-house,
        the companies they represent, and your status with them — and link a recruiter to
        the jobs they sent you.</P>
      <P>When the email agent spots recruiter outreach, it offers <strong>suggestions</strong>
        pre-filled from the message. Nothing is added automatically — you review and confirm
        each contact before it's saved.</P>

      <Separator />

      <H2>Slack notifications</H2>
      <P>Connect Slack (<strong>Settings → Slack</strong>, "Add to Slack") to get a nudge in
        your workspace when the agent needs a decision or flags something worth your
        attention — handy when you're not sitting in Job Radar all day. The connection is
        per-user and scoped to your own workspace.</P>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

function ApiKeysTab() {
  return (
    <div className="max-w-2xl space-y-4">
      <Callout>
        Job Radar is <strong>bring-your-own-key</strong>. You provide API keys for the
        services it uses — they run on <em>your</em> accounts and quotas, and Job Radar
        encrypts them at rest (only the last 4 characters are ever shown). Add them under{" "}
        <strong>Settings → API Keys</strong>.
      </Callout>

      <H2>Required to find &amp; score jobs</H2>

      <H3>Adzuna — job source</H3>
      <P>Adzuna is the main job board Job Radar searches against your criteria. It uses a
        two-part credential: an <strong>App ID</strong> and an <strong>App Key</strong>.
        Without it you'll only get jobs from the public sources (The Muse, Remotive).</P>
      <P>Register a free app at{" "}
        <a href="https://developer.adzuna.com/" target="_blank" rel="noreferrer" className="underline">developer.adzuna.com</a>{" "}
        — sign up, create an application, and copy the App ID + App Key into Settings.</P>

      <H3>An AI key — scoring &amp; generation</H3>
      <P>All AI features (job scoring, cover letters, research, interview prep) run on your
        own LLM key. Any <strong>one</strong> of these works:</P>
      <ul className="list-disc list-inside space-y-1 ml-2">
        <Li><strong>Anthropic</strong> (Claude) —{" "}
          <a href="https://console.anthropic.com/" target="_blank" rel="noreferrer" className="underline">console.anthropic.com</a></Li>
        <Li><strong>OpenAI</strong> (GPT) —{" "}
          <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer" className="underline">platform.openai.com/api-keys</a></Li>
        <Li><strong>Google</strong> (Gemini) —{" "}
          <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer" className="underline">aistudio.google.com</a></Li>
        <Li><strong>Groq</strong> —{" "}
          <a href="https://console.groq.com/keys" target="_blank" rel="noreferrer" className="underline">console.groq.com/keys</a></Li>
      </ul>

      <Separator />

      <H2>Recommended</H2>
      <H3>Tavily — company research</H3>
      <P>Tavily gives the AI up-to-date web info about companies and roles, improving research
        summaries and interview prep. Optional, but worth it. Get a free key at{" "}
        <a href="https://tavily.com/" target="_blank" rel="noreferrer" className="underline">tavily.com</a>.</P>
    </div>
  );
}

export function HelpPage() {
  const [searchParams] = useSearchParams();
  const defaultTab = searchParams.get("tab") ?? "start";

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Help</h1>
      <Callout>
        Job Radar is a personal AI-assisted job hunting tool. It scrapes job boards, scores
        each posting against your profile and criteria, helps you research, tailor your
        résumé, and apply, tracks recruiters, and can watch your inbox — all driven by your
        own AI API key.
      </Callout>
      <Tabs defaultValue={defaultTab}>
        <TabsList className="flex-wrap h-auto gap-1">
          <TabsTrigger value="start">Getting started</TabsTrigger>
          <TabsTrigger value="keys">API keys</TabsTrigger>
          <TabsTrigger value="pipeline">Job pipeline</TabsTrigger>
          <TabsTrigger value="scoring">Scoring</TabsTrigger>
          <TabsTrigger value="tools">Application tools</TabsTrigger>
          <TabsTrigger value="inbox">Inbox &amp; recruiters</TabsTrigger>
          <TabsTrigger value="prompts">Prompts</TabsTrigger>
        </TabsList>
        <TabsContent value="start"    className="mt-6"><GettingStartedTab /></TabsContent>
        <TabsContent value="keys"     className="mt-6"><ApiKeysTab /></TabsContent>
        <TabsContent value="pipeline" className="mt-6"><JobPipelineTab /></TabsContent>
        <TabsContent value="scoring"  className="mt-6"><ScoringTab /></TabsContent>
        <TabsContent value="tools"    className="mt-6"><ApplicationToolsTab /></TabsContent>
        <TabsContent value="inbox"    className="mt-6"><InboxRecruitersTab /></TabsContent>
        <TabsContent value="prompts"  className="mt-6"><PromptsTab /></TabsContent>
      </Tabs>
    </div>
  );
}
