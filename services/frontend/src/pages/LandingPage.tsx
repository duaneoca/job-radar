import { Link } from "react-router-dom";
import {
  Radar,
  Target,
  KanbanSquare,
  FileText,
  MousePointerClick,
  KeyRound,
  Check,
  ArrowRight,
  Sun,
  Moon,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { BrowserFrame } from "../components/marketing/BrowserFrame";
import { useDarkMode } from "../hooks/useDarkMode";

/** Screenshots live in /public/marketing, one per theme: `<name>-light.png` and
 *  `<name>-dark.png`, so the page shows the app in the theme you're viewing it in.
 *  Captured from staging by scripts/marketing-screenshots (docs/marketing-screenshots.md). */
const SHOTS = {
  jobs: "jobs-list",
  detail: "job-detail",
  tailor: "resume-tailor",
  capture: "capture",
};
const shotSrc = (name: string, dark: boolean) => `/marketing/${name}-${dark ? "dark" : "light"}.png`;

type Feature = {
  icon: typeof Target;
  eyebrow: string;
  title: string;
  body: string;
  points: string[];
  shot: string;
  shotAlt: string;
};

const FEATURES: Feature[] = [
  {
    icon: Target,
    eyebrow: "Scoring",
    title: "Every posting, scored against you",
    body: "Job Radar reads each posting and rates it on five dimensions — Skills, Experience, Location, Education, and Salary — against your résumé and criteria, so the best-fit roles rise to the top.",
    points: ["5-dimension fit score", "Sort & filter your whole pipeline", "Pros, cons, and a plain-English summary"],
    shot: SHOTS.jobs,
    shotAlt: "Job Radar jobs list with AI fit scores",
  },
  {
    icon: KanbanSquare,
    eyebrow: "Workspace",
    title: "One workspace per job",
    body: "Open any role into a focused workspace — company research, drafted application answers, tailored interview prep, a status timeline, and your notes, all in one place.",
    points: ["Research & application drafts", "Interview prep tailored to the role", "Status timeline from applied → offer",
             "Works from Claude, ChatGPT, Gemini or Grok"],
    shot: SHOTS.detail,
    shotAlt: "Job Radar job detail workspace with tabs",
  },
  {
    icon: FileText,
    eyebrow: "Résumé",
    title: "Tailor your résumé in seconds",
    body: "Get targeted, honest edits that align your résumé to a specific posting — reviewed line-by-line so you stay in control — then export a clean, templated PDF.",
    points: ["Line-by-line diff you accept or reject", "Never invents experience you don't have", "One-click templated PDF export"],
    shot: SHOTS.tailor,
    shotAlt: "Job Radar résumé tailoring diff review",
  },
  {
    icon: MousePointerClick,
    eyebrow: "Capture",
    title: "Capture any job in one click",
    body: "Found a role out in the wild? The bookmarklet grabs it from LinkedIn, Dice, Indeed and the other big boards, or straight from a company's own careers page. Anything else, paste the page and your AI fills in the form. Duplicates are skipped automatically.",
    points: ["📡 One-click bookmarklet", "Boards and company careers pages", "Auto-dedupe — no double entries"],
    shot: SHOTS.capture,
    shotAlt: "Job Radar bookmarklet and Add Job page",
  },
];

function Wordmark() {
  return (
    <span className="flex items-center gap-2 whitespace-nowrap font-bold text-primary">
      <Radar className="h-5 w-5" />
      <span className="text-foreground">Job Radar</span>
    </span>
  );
}

/** Light/dark switch. Shares the app's saved theme, so a visitor who picks one
 *  here keeps it after signing in, and vice versa. Until they choose, the page
 *  follows their system setting. */
function ThemeToggle({ dark, onToggle }: { dark: boolean; onToggle: () => void }) {
  const Icon = dark ? Sun : Moon;
  const label = dark ? "Switch to light mode" : "Switch to dark mode";
  return (
    <Button variant="ghost" size="icon" onClick={onToggle} aria-label={label} title={label}>
      <Icon className="h-4 w-4" />
    </Button>
  );
}

export function LandingPage() {
  const { setMode, dark } = useDarkMode();
  const toggle = () => setMode(dark ? "light" : "dark");
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
          <Wordmark />
          <div className="flex items-center gap-1 sm:gap-2">
            <ThemeToggle dark={dark} onToggle={toggle} />
            {/* The hero repeats Sign in just below; on the narrowest phones the
                header has no room for both it and the theme toggle. */}
            <Button variant="ghost" asChild className="hidden min-[380px]:inline-flex">
              <Link to="/login">Sign in</Link>
            </Button>
            <Button asChild>
              <Link to="/signup">Request access</Link>
            </Button>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -top-40 h-96 bg-gradient-to-b from-primary/20 to-transparent blur-3xl"
        />
        <div className="mx-auto max-w-6xl px-4 pt-16 pb-10 sm:pt-24 text-center">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs font-medium text-muted-foreground">
            <KeyRound className="h-3.5 w-3.5" /> Bring your own AI key
          </span>
          <h1 className="mx-auto mt-6 max-w-3xl text-4xl font-bold tracking-tight sm:text-6xl">
            Your AI-assisted job hunt, on&nbsp;autopilot
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-muted-foreground">
            Job Radar scrapes the boards, scores every posting against your résumé and
            criteria, and helps you research, tailor, and apply — all powered by your own AI key.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Button size="lg" asChild>
              <Link to="/signup">
                Request access <ArrowRight className="ml-1 h-4 w-4" />
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild>
              <Link to="/login">Sign in</Link>
            </Button>
          </div>
          <div className="mx-auto mt-14 max-w-4xl">
            <BrowserFrame src={shotSrc(SHOTS.jobs, dark)} alt="Job Radar dashboard" />
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-6xl space-y-24 px-4 py-20 sm:py-28">
        {FEATURES.map((f, i) => {
          const Icon = f.icon;
          const flip = i % 2 === 1;
          return (
            <div
              key={f.title}
              className="grid items-center gap-10 lg:grid-cols-2 lg:gap-16"
            >
              <div className={flip ? "lg:order-2" : ""}>
                <span className="inline-flex items-center gap-2 text-sm font-semibold text-primary">
                  <Icon className="h-4 w-4" /> {f.eyebrow}
                </span>
                <h2 className="mt-3 text-2xl font-bold tracking-tight sm:text-3xl">{f.title}</h2>
                <p className="mt-4 text-muted-foreground">{f.body}</p>
                <ul className="mt-6 space-y-2.5">
                  {f.points.map((p) => (
                    <li key={p} className="flex items-start gap-2.5 text-sm">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                      <span>{p}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className={flip ? "lg:order-1" : ""}>
                <BrowserFrame src={shotSrc(f.shot, dark)} alt={f.shotAlt} />
              </div>
            </div>
          );
        })}
      </section>

      {/* BYOK strip */}
      <section className="border-y border-border/60 bg-muted/20">
        <div className="mx-auto max-w-4xl px-4 py-16 text-center">
          <KeyRound className="mx-auto h-8 w-8 text-primary" />
          <h2 className="mt-4 text-2xl font-bold tracking-tight sm:text-3xl">
            Your keys, your data, your cost
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-muted-foreground">
            Job Radar runs on your own AI API key — Anthropic, OpenAI, Google, xAI, or Groq. Nothing
            is scored on someone else's dime, and your search stays yours. Add your key in
            Settings and you're off.
          </p>
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-4xl px-4 py-20 text-center">
        <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
          Point your radar at the right roles
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
          Job Radar is invite-based while it's in active development. Request access and we'll get
          you set up.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Button size="lg" asChild>
            <Link to="/signup">
              Request access <ArrowRight className="ml-1 h-4 w-4" />
            </Link>
          </Button>
          <Button size="lg" variant="outline" asChild>
            <Link to="/login">Sign in</Link>
          </Button>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-4 py-8 sm:flex-row">
          <Wordmark />
          <p className="text-sm text-muted-foreground">
            © {new Date().getFullYear()} Job Radar. Your AI-assisted job hunt.
          </p>
        </div>
      </footer>
    </div>
  );
}
