import { Link } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";

const REPO = "https://github.com/duaneoca/job-radar-agent";
const FULL_RUNBOOK = `${REPO}/blob/main/docs/DEPLOYMENT.md`;

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-md border bg-muted/50 px-3 py-2 text-xs font-mono leading-relaxed">
      {children}
    </pre>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="font-medium flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-semibold">{n}</span>
        {title}
      </h3>
      <div className="space-y-2 text-sm text-muted-foreground pl-7">{children}</div>
    </div>
  );
}

export function LocalAgentSetupPage() {
  return (
    <div className="max-w-2xl space-y-6">
      <Link to="/settings?tab=agent" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to Email Agent settings
      </Link>

      <div className="space-y-1">
        <h1 className="text-xl font-bold">Run the agent locally (Proton)</h1>
        <p className="text-sm text-muted-foreground">
          Proton Mail Bridge only listens on <code>127.0.0.1</code>, so the agent runs on your own Mac
          (via pipx + launchd) rather than in our cloud. This is the quickstart — the{" "}
          <a href={FULL_RUNBOOK} target="_blank" rel="noreferrer" className="underline inline-flex items-center gap-0.5">
            full runbook <ExternalLink className="h-3 w-3" />
          </a>{" "}
          has Linux, troubleshooting, and operating details.
        </p>
      </div>

      <div className="space-y-6">
        <Step n={1} title="Prerequisites">
          <ul className="list-disc pl-4 space-y-1">
            <li><strong>Proton Mail Bridge</strong> installed, running, and logged in. Note its IMAP host/port (default <code>127.0.0.1:1143</code>) and the <strong>Bridge-specific password</strong> (not your Proton login).</li>
            <li>In Proton, create your funnel folder + sub-folders (e.g. <code>Hire Duane</code> + <code>Interaction / Postings / Social / Unprocessed</code>). The agent never creates folders.</li>
            <li>An <strong>LLM API key</strong> (Anthropic / OpenAI / Google / Groq — set it on the API Keys tab).</li>
            <li><code>pipx</code>: <code>brew install pipx && pipx ensurepath</code> (restart your shell).</li>
          </ul>
        </Step>

        <Step n={2} title="Install the agent">
          <Code>{`pipx install "git+${REPO}"
job-radar-agent version`}</Code>
        </Step>

        <Step n={3} title="Get your agent key">
          <p>On the <Link to="/settings?tab=agent" className="underline">Email Agent</Link> tab, under <strong>Proton → Agent key</strong>, generate a key (starts with <code>jr_…</code>) and copy it — it's shown only once. It maps to your account; the agent sends it as <code>X-Agent-Key</code>.</p>
        </Step>

        <Step n={4} title="Configure">
          <p>Create the config dir and <code>.env</code> from the template, then fill it in:</p>
          <Code>{`HOME_DIR="$HOME/Library/Application Support/JobRadarAgent"
mkdir -p "$HOME_DIR/data"
curl -fsSL ${REPO.replace("github.com", "raw.githubusercontent.com")}/main/.env.example -o "$HOME_DIR/.env"
chmod 600 "$HOME_DIR/.env" && $EDITOR "$HOME_DIR/.env"`}</Code>
          <Code>{`EMAIL_PROVIDER=proton
EMAIL_ROOT_FOLDER=Folders/Hire Duane      # Proton namespaces under Folders/
PROTON_IMAP_HOST=127.0.0.1
PROTON_IMAP_PORT=1143
PROTON_IMAP_USER=you@proton.me
PROTON_IMAP_PASSWORD=<bridge-specific-password>

LLM_PROVIDER=anthropic
LLM_MODEL=claude-haiku-4-5                 # bare id, dashes not dots
LLM_API_KEY=<your provider key>

JOBRADAR_API_URL=https://job-radar.net/api
AGENT_API_KEY=<from step 3>`}</Code>
          <p>Not sure of the model id? <code>job-radar-agent models</code> lists the exact ids your key accepts.</p>
        </Step>

        <Step n={5} title="Preflight, then run">
          <p><code>doctor</code> checks Bridge login, folders, a live LLM ping, and Job Radar connectivity. Fix any ✗ before scheduling.</p>
          <Code>{`AGENT_HOME="$HOME/Library/Application Support/JobRadarAgent" job-radar-agent doctor
AGENT_HOME="$HOME/Library/Application Support/JobRadarAgent" MAX_EMAILS_PER_RUN=5 job-radar-agent run --once`}</Code>
        </Step>

        <Step n={6} title="Schedule it (every 15 min)">
          <Code>{`cp deploy/local/com.jobradar.emailagent.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobradar.emailagent.plist`}</Code>
          <p>Update later with <code>pipx upgrade job-radar-agent</code> — your <code>.env</code> and schedule are untouched.</p>
        </Step>
      </div>

      <div className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
        Your mailbox credentials stay in that local <code>.env</code> and never touch Job Radar — the
        only thing the agent sends us is the agent key (to write results back). Full details, Linux
        setup, and troubleshooting in the{" "}
        <a href={FULL_RUNBOOK} target="_blank" rel="noreferrer" className="underline">runbook</a>.
      </div>
    </div>
  );
}
