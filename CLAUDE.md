# Job Radar — CLAUDE.md

AI-assisted job hunting tool. Scrapes job boards, scores postings against the user's resume and criteria using their own API keys (BYOK), and helps with applications.

**Production:** https://job-radar.net  
**Staging:** https://staging.job-radar.net (auto-deploys on every push to `main`)  
**Current version:** v1.3.2

---

## Repo layout

```
services/
  tracker-api/     FastAPI backend — SQLAlchemy, Alembic, Celery producer
  frontend/        React + Vite + shadcn/ui, served by nginx
  ai-reviewer/     Celery worker — scores jobs via LiteLLM
  scraper/         Celery worker + Beat scheduler — per-user scrape every 6 hours
  mcp-writer/      FastMCP service wrapping the email-agent /agent/* endpoints
k8s/
  base/            Kustomize base manifests for all services
  overlays/
    production/    Patches: host = job-radar.net
    staging/       Patches: host = staging.job-radar.net
```

---

## Deployment

**Staging** — push to `main`, CI/CD deploys automatically.

**Production** — tag and push:
```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

**Versioning (semver — always confirm with user before tagging):**
- `vX.0.0` — major: breaking changes, big redesigns
- `vX.Y.0` — minor: new features (backward compatible)
- `vX.Y.Z` — patch: bug fixes only

**Infrastructure:**
- Single k3s node on AWS EC2
- Namespaces: `jobradar-production`, `jobradar-staging`
- Cloudflare proxies both hostnames (handles TLS — no cert-manager needed)
- GHCR for Docker images; GitHub Actions for CI/CD
- SES sending from `noreply@job-radar.net` (domain identity, not email address identity)
- IAM role on EC2 — no hardcoded AWS keys

**Adzuna is BYOK** — each user stores their own `app_id`/`app_key` (Settings → API Keys,
encrypted in `user_api_keys`). No shared/global Adzuna key; the old manual
`scraper-secrets` is retired.

---

## Database

PostgreSQL in-cluster with PVC. Alembic migrations in `services/tracker-api/alembic/versions/`.

**Schema overview:**
- `jobs` — shared pool; scraped once, visible to all users
- `user_job_reviews` — per-user AI scores, status, notes; FK → `jobs` with `ondelete=CASCADE`
- `timeline_events` — FK → `user_job_reviews` with `ondelete=CASCADE`
- `criteria`, `profiles`, `user_api_keys`, `linkedin_connections`, `email_credentials`, `slack_connections` — all cascade on user delete

**Cascade rule:** deleting a `User` cascades to all their rows. Deleting a `UserJobReview` cascades to `TimelineEvent`. The shared `jobs` row is only deleted when zero reviews reference it (handled in code, not DB FK).

**When adding a migration:**
```bash
cd services/tracker-api
alembic revision --autogenerate -m "description"
alembic upgrade head   # apply locally
```
Migrations run automatically on pod startup in k8s.

---

## Services

### tracker-api (FastAPI)
Base URL in-cluster: `http://tracker-api/`

Key routers:
- `POST /jobs` — scraper writes raw jobs (no auth)
- `GET/PATCH/DELETE /jobs/{review_id}` — user's job review (`review_id` = `UserJobReview.id`, NOT `Job.id`)
- `POST /jobs/manual` — bookmarklet / manual import (auth required)
- `POST /jobs/{job_id}/ai-review` — ai-reviewer posts scores (no auth, internal)
- `POST /admin/internal/cleanup` — nightly cleanup called by scraper Beat (no auth, `include_in_schema=False`)
- `POST /admin/trigger-scrape` / `trigger-evaluate` / `cleanup-jobs` — admin UI triggers

**Route ordering gotcha:** literal routes must come before `{param}` routes. FastAPI matches first-wins. E.g., `/jobs/manual` and `/jobs/enqueue-review` must be registered before `/{review_id}`. Same pattern caused a past bug with `/internal/{user_id}/llm` vs `/{provider}`.

**Internal no-auth endpoints** use `include_in_schema=False`. Do not add user-facing auth to these; they are only called by other services inside the cluster.

**Email agent auth — the invariant:** every **per-user operational** endpoint accepts
**either** `X-Agent-Key` (local self-host → user from key) **or** `X-Internal-Token` +
`X-User-Id` (cloud CronJob, per-user) — see `get_agent_writer`. That set is
`GET /agent/reviews` + `POST /agent/{inbox, interactions, runs, hitl/register, hitl/pending,
hitl/consume}`. Enumeration/bootstrap (`/agent/cloud/*`) is the cloud-internal surface
(`X-Internal-Token` only). `/agent/config` stays key-only (the cloud path uses
`/agent/cloud/config/{user_id}` instead). `/agent/config` + `/agent/cloud/*` return DECRYPTED
per-user secrets and
are **in-cluster only**: blocked at nginx (`/agent/config` exact, `^~ /api/agent/cloud/`) and
behind the tracker-api NetworkPolicy. `/agent/cloud/users` (no secrets, enumerate) is split
from `/agent/cloud/config/{user_id}` (one user's creds) on purpose — runner holds one user at
a time (H6). The shared `AGENT_INTERNAL_TOKEN` must match in `tracker-api-secrets` and
`email-agent-secrets`.

### frontend (React + Vite)
- shadcn/ui components in `src/components/ui/`
- nginx proxies `/api/` → tracker-api
- Bookmarklet built inline in `src/pages/SettingsPage.tsx` — `buildBookmarklet()` function returns a `javascript:` URL. All JS inside is minified (newlines stripped at runtime). Escape backslashes twice in template literals (`\\s` → `\s` in output).

### ai-reviewer (Celery worker)
- Prompts: `services/ai-reviewer/app/prompts/review_prompt.md` + `output_format.md`
- Scores on 5 dimensions (Skills, Experience, Location, Education, Salary), each 1–10, averaged to overall score
- Summary must be written in second person to the candidate ("Your background in X…"), not from a hiring manager perspective
- Uses LiteLLM — priority order: Anthropic → OpenAI → Google → Groq

### scraper (Celery + Beat)
Beat schedule (UTC):
- Every 6 hours — `scrape_all`: per-user scrape. Fetches `/criteria/scraper/user-configs`
  (each user's criteria + their decrypted Adzuna creds), scrapes each user with their
  own key, and POSTs jobs to `/jobs?user_id=` (attributed to that user, no fan-out).
- 2:45 AM — `expire_jobs`; 3 AM — `cleanup_jobs`.
- Also triggered on demand: `scrape_user(user_id)` fires when a user saves criteria (debounced).

Sources: **Adzuna** (BYOK per-user key; skipped for users without one), **The Muse** (public,
category mapped from the user's job titles), **Remotive** (public, remote-only). HTML scraping
was abandoned — Cloudflare blocks datacenter IPs on LinkedIn/Indeed/Glassdoor.

### email-agent (CronJob — cloud multi-user)
`k8s/base/email-agent/` — a `*/15 * * * *` CronJob running
`ghcr.io/duaneoca/job-radar-agent:latest` (image built by the **separate
`job-radar-agent` repo**, NOT this repo's CI — do not add it to the `kubectl set image`
loop). One run enumerates enabled users via `/agent/cloud/users`, fetches one config at a
time, processes, and writes back with `AGENT_INTERNAL_TOKEN` + `user_id`. The **local**
self-host agent (Proton Bridge on Duane's machine) is separate: REST + its own `.env`, no
CronJob, mailbox creds never touch Job Radar. Cloud is Gmail-only until the agent ships a
cloud-IMAP provider.

**Local vs cloud config/credential model** (the "why is config in two places?" question,
incl. how a self-host/Proton user sets up Slack via `.env` vs the cloud OAuth flow):
fully documented in [`docs/agent-topologies-and-credentials.md`](docs/agent-topologies-and-credentials.md).
The rule: decrypted secrets never leave the cluster (H6a), so external/local agents
self-configure from `.env`; only in-cluster (cloud) agents fetch decrypted config.

---

## Data retention

- `terminal_ttl_days = 14` (config) — dismissed, rejected, expired reviews deleted after 14 days
- Orphaned `Job` rows (no remaining reviews from any user) hard-deleted in same pass
- Applied / interviewing / offer statuses are never touched by cleanup
- Manual trigger: Admin → System → "Clean up old jobs"

---

## Bookmarklet

Supported sites: LinkedIn, Dice, BuiltIn, Monster, ZipRecruiter, Indeed.

**LinkedIn specifics:**
- URL guard: aborts with a helpful message if URL does not contain `/jobs/view/` (search/list pages give bad data)
- Salary search order: (1) insight/salary/compensation elements, (2) top-card spans, (3) description text fallback
- Salary regex handles: `$180,000–$225,000`, `$180K - $280K`, `$180K—$280K` (hyphen, en-dash U+2013, em-dash U+2014)

All six sites use the same K-aware salary regex:
```
\$(\d+(?:,\d+)?(?:\.\d+)?)(K?)\s*[-–—]\s*\$(\d+(?:,\d+)?(?:\.\d+)?)(K?)
```
Parse with `Math.round(parseFloat(match[1].replace(/,/g,'')) * (match[2].toUpperCase() === 'K' ? 1000 : 1))`.

---

## Key conventions

- **Review ID vs Job ID:** `UserJobReview.id` (`job.id` in frontend) is used for all PATCH/DELETE on reviews. `UserJobReview.job_id` (`job.job_id` in frontend) is the shared `Job` pool FK — never use this as the API path parameter.
- **Celery tasks** are sent from tracker-api using a producer-only `Celery(broker=...)` instance (no workers run in tracker-api).
- **API keys** are stored encrypted; LiteLLM receives the plaintext key per-request from the worker.
- **SQLAlchemy cascade:** always set both `ondelete="CASCADE"` on the FK column AND `cascade="all, delete-orphan"` on the relationship, otherwise bulk `.delete(synchronize_session=False)` won't cascade.
- **Admin bootstrap:** if `ADMIN_EMAIL` env var is set and no users exist, the first startup creates an admin account with `ADMIN_PASSWORD` and forces a password change.

---

## Pending / backlog ideas

- More The Muse category mappings (`_CATEGORY_TRIGGERS` in `the_muse.py`)
- Tavily enrichment could be extended beyond the research endpoint
- Optional defense-in-depth: app-level `X-Internal-Token` on internal endpoints
  (external surface is already blocked at nginx; see internal-endpoint memory)

Done recently: soft-expire (`job_ttl_days`) shipped; `email-monitor` retired and
replaced by `mcp-writer` (FastMCP); Adzuna moved to per-user BYOK.
