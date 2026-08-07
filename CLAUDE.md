# Job Radar — CLAUDE.md

AI-assisted job hunting tool. Scrapes job boards, scores postings against the user's resume and criteria using their own API keys (BYOK), and helps with applications.

**Production:** https://job-radar.net  
**Staging:** https://staging.job-radar.net (auto-deploys on every push to `main`)  
**Current version:** v1.12.2

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

**Release notes are written before the tag, not generated from it.**
`services/frontend/src/lib/releases.ts` holds the user-facing notes; the Help →
What's new tab renders them and `ReleaseBanner` announces the top entry. The
GitHub Release keeps `generate_release_notes: true` — that's the developer
changelog and a different audience. Never send PR titles to users; "fix(ai-
reviewer): stop the OOM loop" tells them only that something they relied on was
broken in ways they hadn't noticed.

Order: work merges → draft the notes → agree the wording with the user → merge
that as its own small PR → tag. Adding no entry means no announcement, which is
the intended behaviour for a quiet hotfix and a safe failure if it's forgotten.
Say when something was broken, not only what is new.

**Versioning (semver — always confirm with user before tagging):**
- `vX.0.0` — major: breaking changes, big redesigns
- `vX.Y.0` — minor: new features (backward compatible)
- `vX.Y.Z` — patch: bug fixes only

**Infrastructure:**
- Single k3s node on AWS EC2
- Namespaces: `jobradar-production`, `jobradar-staging`
- Cloudflare proxies both hostnames (handles TLS — no cert-manager needed)
- GHCR for Docker images; GitHub Actions for CI/CD
  - Staging tags images with the commit SHA, production with the release tag.
  - `prune-images.yml` (weekly) deletes untagged versions and staging SHA builds
    older than 8 weeks, keeping the 10 most recent. **Release (`v*`) images are
    never deleted** — a production rollback redeploys an earlier `vX.Y.Z`, and the
    node's kubelet garbage-collects its local cache, so GHCR is what makes rollback
    possible. Images on the node need no management (kubelet GCs at 85% disk).
- SES sending from `noreply@job-radar.net` (domain identity, not email address identity)
- **Backups are two halves.** The nightly `pg_dump` CronJob (production overlay,
  02:00 UTC → S3, verified with `pg_restore --list` before upload) covers the
  data. It does NOT cover the Secrets, and a dump without them is largely
  useless: every `user_api_keys.encrypted_key`, Gmail refresh token and Slack
  token in it is Fernet-encrypted with `ENCRYPTION_KEY`, which is the one value
  in the cluster that cannot be reissued. Keep an off-cluster copy and check it
  hasn't drifted:
  ```bash
  scripts/check-secret-backup.py export -o ~/jobradar-secrets.json   # store in a password manager
  scripts/check-secret-backup.py check  ~/jobradar-secrets.json      # names + verdicts only, no values
  ```
  `check` exits non-zero on drift. Run it after adding or rotating any secret —
  a backup taken before v1.4.x lacks `AGENT_INTERNAL_TOKEN` and the OAuth pairs,
  and would restore a system that starts but can't run the email agent.
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
- `criteria`, `profiles`, `user_api_keys`, `linkedin_connections`, `recruiters`, `email_credentials`, `slack_connections` — all cascade on user delete
- `recruiters` — per-user recruiter CRM. `user_job_reviews.recruiter_id` FK → `recruiters` with `ondelete=SET NULL` (deleting a recruiter unlinks its jobs, never deletes them). Seedable from inbox `recruiter_outreach` senders via `GET /recruiters/suggestions` — enriched (phone/title/employer/linkedin/type/companies) from the agent's `recruiter_contact` card in `inbox_emails.raw_extracted_json` when present. All agent-derived fields are untrusted (C2): sanitized server-side (`_clean_card` — length-caps, http(s)-only linkedin), review-and-confirm, never auto-created.

**Cascade rule:** deleting a `User` cascades to all their rows. Deleting a `UserJobReview` cascades to `TimelineEvent`. Deleting a `Recruiter` only nulls `user_job_reviews.recruiter_id`. The shared `jobs` row is only deleted when zero reviews reference it (handled in code, not DB FK).

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
- nginx proxies `/api/` → tracker-api. **`proxy_read_timeout` is 95s**, not the
  60s default: AI generation is slow, and interview prep with a large writing
  skill measured ~73s — nginx was returning 504 *thirteen seconds before* the
  backend finished and saved a good answer, so the user saw "Generation failed"
  for work that had succeeded. 95s is deliberately just under Cloudflare's 100s
  cap, which nothing in our config can raise; anything genuinely slower than that
  has to become a background job rather than a longer request.
- Bookmarklet built inline in `src/pages/SettingsPage.tsx` — `buildBookmarklet()` function returns a `javascript:` URL. All JS inside is minified (newlines stripped at runtime). Escape backslashes twice in template literals (`\\s` → `\s` in output).

### ai-reviewer (Celery worker)
- Prompts: `services/ai-reviewer/app/prompts/review_prompt.md` + `output_format.md`
- Scores on 5 dimensions (Skills, Experience, Location, Education, Salary), each 1–10, averaged to overall score
- Summary must be written in second person to the candidate ("Your background in X…"), not from a hiring manager perspective
- Uses LiteLLM — priority order: Anthropic → OpenAI → Google → Groq
  (`models.LLM_PROVIDERS`), preferring a key that has a model.

**There is NO default model — this is an invariant, not an oversight.** Picking a
model on the user's behalf spends *their* money on something they never chose, and
any model hardcoded here eventually gets retired (a dead Gemini default once made
every review for a user silently score nothing). `model_for_key()` returns `None`
when `preferred_model` is unset; `get_llm_provider()` 400s; `PUT /keys/active`
refuses a model-less key; `GET /keys/internal/{u}/llm` returns **409** (404 still
means "no key at all"). Do not reintroduce a `PROVIDER_MODELS`-style fallback.

**Permanent key failures are recorded, transient ones are not.**
`user_api_keys.last_error_kind` holds `invalid_model` / `invalid_key`
(`models.KEY_ERROR_*`); ai-reviewer classifies via `app/llm_errors.py` and posts to
`POST /keys/internal/{user_id}/llm/status` (internal token; null `kind` clears).
`classify_llm_error` checks exception **types** for the transient set *before* it
sniffs message text — a 429 body containing "invalid" must never be read as a dead
model, or a throttled user is told to change a setting that was correct. A permanent
verdict also stops the retry; a transient one retries — and if it survives all
`max_retries`, it is reported as `rate_limited` and the task returns instead of
raising. That last part matters: letting the exception escape makes Celery log it
at ERROR, which would put one user's quota ceiling in the operator's digest.
`classify_llm_error` never returns the transient kinds; only retry exhaustion
does, because a lone 429 mid-burst is normal. Which one is decided by
`transient_kind()` **while the original exception is still in hand**:
`rate_limited` for a real throttle, `PROVIDER_UNAVAILABLE` for a timeout,
connection failure or 5xx — and that is the fallback, because "not responding"
is vague but never wrong, whereas "you are being rate-limited" is a specific
claim about the user's account. Neither blocks future jobs or
`PUT /keys/active` (`models.KEY_ERRORS_TRANSIENT`) — the key works — and any
success clears them. Re-saving the key or changing
the model clears the record. **Every** `llm_complete` call site
passes `db`/`user_id` — the five in `generate.py` plus `resume_tailor.py`'s two,
which take them through from their four routers — so scoring, research,
application answers, interview prep, résumé parsing and tailoring all reach the
same banner. The args stay optional only so the functions remain callable from a
script; if you add a call site, wire them.

Foreground calls record transient kinds too. `llm_complete` already sets
`num_retries=2`, so a throttle that surfaces has outlasted its retries — the same
bar the worker applies. `_PROVIDER_DOWN` (timeout / connection / 5xx) is handled
explicitly and logged at WARNING; before that it fell through to the catch-all and
logged at **ERROR**, which put a user's provider outage in the operator's digest.
The catch-all still logs ERROR and records nothing, because an unexpected
exception there is our bug, not the provider's.

**A model that won't answer in JSON is the user's problem, not a bug.**
`KEY_ERROR_UNUSABLE_OUTPUT` is recorded only after `UNUSABLE_OUTPUT_STREAK` (3)
*consecutive* unparseable responses — one rambling answer proves nothing, and a
permanent accusation from a single sample is how banners lose trust. Any success,
any other failure kind, or changing the key/model resets the count. It joins
`KEY_ERRORS_BLOCKING`: the worker skips scoring outright rather than spending the
user's quota to relearn what is already on their screen. Every blocking kind is
cleared by a user action or a success, so it cannot wedge. Reviewer asks for
`response_format={"type":"json_object"}` only for providers with NATIVE json
mode (`_NATIVE_JSON_MODE_PREFIXES` — OpenAI, Gemini, Groq). **Not** via
`litellm.get_supported_openai_params()`, which reports Anthropic as supported
but implements it as a forced tool call with an empty schema: Claude then
invents its own keys and every review fails the KeyError path. Unknown models
fall through to prompt-only, which works everywhere. And
`extract_json_object()` takes the LAST brace-balanced object (models show their
working, sometimes including an example object, before answering).

**Log level follows who can fix it.** A failure the user is shown (dead model,
rejected key) is logged at `WARNING` — it is not an operator fault and must not
reach an error digest. `ERROR` is reserved for unexpected exceptions, failed
post-backs, and unparseable model output.

**Writing skills** (`criteria.writing_skills`, JSON `[{id,name,content,enabled,scopes}]`):
user-loadable blocks of style rules injected into the prompts named in each skill's
`scopes` (`application | research | interview_prep | resume | scoring`). Built by
`skills_block()` in `routers/generate.py`; ai-reviewer keeps its own small copy since
the services share no package. **Always rendered below** the locked honesty contract /
rubric / output format so a skill can only shape wording — and never injected into
extract-changes (meta-prompting) or the résumé parser (extraction).

### scraper (Celery + Beat)
Beat schedule (UTC):
- Every 6 hours — `scrape_all`: per-user scrape. Fetches `/criteria/scraper/user-configs`
  (each user's criteria + their decrypted Adzuna creds), scrapes each user with their
  own key, and POSTs jobs to `/jobs?user_id=` (attributed to that user, no fan-out).
- 2:45 AM — `expire_jobs`; 3 AM — `cleanup_jobs`.
- Also triggered on demand: `scrape_user(user_id)` fires when a user saves criteria (debounced)
  — but only when a *search* field actually changed (`_SCRAPE_FIELDS` in `routers/criteria.py`).
  The AI Prompts tab PUTs the whole criteria object, so prompt/skill edits must not scrape.

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

## Observability

**One log format, everywhere.** `logging_config.configure_logging(level)` is called
at import in tracker-api, ai-reviewer and scraper (each has its own copy — the
services share no package). `LOG_LEVEL` sets the level; the **format is fixed**,
because `log_digest.LOG_LINE` parses it:

```
2026-08-01 04:29:02 WARNING app.reviewer: LLM call failed for job …
```

Both Celery apps set `worker_hijack_root_logger = False`. Without it Celery
re-installs its own root handler on worker startup and the workers silently revert
to Celery's format — the digest then matches nothing and reports a permanently
healthy system. `tests/test_log_digest.py` pins the parser against
`configure_logging`'s actual output for the same reason; the two drifting apart
produces no crash and no error, just silence.

**Hourly error digest** (`app/log_digest.py`, `:05` past the hour, **production
overlay only**) — runs on the tracker-api image via a `command:` override, like the
backup job. No new image, no new dependency, and **not in CI's `kubectl set image`
loop** (that loop hardcodes five *deployment* names under `set -euo pipefail`), so
it uses `:latest` + `imagePullPolicy: Always` like email-agent. `:latest` is pushed
only by the production release workflow.

- **ERROR/CRITICAL only.** WARNING is where user-fixable problems live and must
  stay out — see the log-level rule under ai-reviewer.
- Grouped by `service|logger|normalised-message`, so one bug across 1,115 jobs is
  one line with a count, not 1,115 paragraphs.
- `FALLBACK_LINE` catches `ERROR:`/`FATAL:`/`PANIC:` from services we don't write
  (postgres, redis, nginx), which never match our format.
- Lists **all** pods in the namespace — no `labelSelector`. `project=jobradar` is
  on the Deployment objects but **not** their pod templates, so selecting on it
  matches zero pods.
- **Nothing found → nothing sent.** Failure to read the cluster sends a `FAILED`
  email and exits 1; one unreadable container is named in the footer, never dropped.
- **Repeat mail while an error persists is intentional** — fix the bug or fix what
  is being logged at ERROR. No cooldown, no suppression, no state.
- Logs rotate, so coverage is best-effort; the footer says so rather than implying
  completeness.

**First RBAC in the repo** — `log-digest-rbac.yaml`: a dedicated ServiceAccount plus
a namespaced **Role** (not ClusterRole) granting only `pods: list` and `pods/log:
get`. It cannot read secrets and cannot see `jobradar-staging`.

## Data retention

- **Two soft-expiry rules** (`_do_expire`), both flipping NEW/REVIEWED → EXPIRED:
  1. `job_ttl_days = 30` — the review sat unactioned here that long.
  2. `posting_max_age_days = 21` — the **posting's own `date_posted`** is that old,
     no matter when we scraped it. Rule 1 counts from `updated_at`, so a listing
     already weeks old when scraped could show for ~50 days after going up; this
     is what fills the list with dead "no longer available" links.
     `_POSTING_AGE_EXEMPT_SOURCES` skips `manual` (deliberate capture) and
     `ashby`/`greenhouse`/`lever` (we re-read those boards every scrape, so a
     posting still returned IS still open — evergreen reqs can sit for years).
- **Company boards get a real signal instead of a proxy.** `POST /jobs/board-sync`
  (internal) receives every `external_id` still on each board the scraper
  successfully read; a posting missing `BOARD_MISS_THRESHOLD` (2) consecutive
  scrapes is expired. Safety: only boards that returned a valid payload are sent
  (a 429 or dead slug is omitted, never mistaken for "empty"); ids are collected
  BEFORE the title prefilter, so editing `job_titles` can't expire past finds;
  reappearing resets the counter. Expiry is global for the job and touches only
  NEW/REVIEWED.
- Expiry cannot be confirmed by fetching an AGGREGATOR link: they return 403 to
  anything that isn't a real browser, and the block page is identical for live
  and dead ads. Posting age is the proxy there. (A browser *does* see
  "Unfortunately, this job is no longer available" — but only a browser.)
- Never treat absence from an aggregator search as evidence: Adzuna is queried
  `sort_by=date` capped at 100/keyword, so live-but-older postings fall out by
  design. `models.BOARD_SOURCES` is the single source of truth for which sources
  return every open role.
- `terminal_ttl_days = 14` (config) — dismissed, rejected, expired reviews deleted after 14 days
- Orphaned `Job` rows (no remaining reviews from any user) hard-deleted in same pass
- Applied / interviewing / offer / referral_requested are never touched by either pass
- Manual trigger: Admin → System → "Clean up old jobs"

---

## Bookmarklet

Supported sites: LinkedIn, Dice, BuiltIn, Monster, ZipRecruiter, Indeed, Ashby
(`jobs.ashbyhq.com`), Greenhouse (`job-boards.greenhouse.io`). Ashby/Greenhouse are
ATS platforms (many companies) rather than job boards; extraction reads the static
job page (`h1` title; company from the URL path or `document.title`). Company-branded
Greenhouse embeds (e.g. `careers.<company>.com`) and legacy `boards.greenhouse.io`
(which 302s to a generic careers page) are **not** matched. `source` is a
`VARCHAR(50)` column validated against the `JobSource` enum in code — adding a source
needs the enum value in `models.py` + frontend `types.ts`/`SOURCE_LABELS`, **no DB
migration**.

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
- **Docs stay in lockstep with features:** any user-facing change ships matching updates to the in-app Help (`services/frontend/src/pages/HelpPage.tsx`) and, for headline features, the public landing page (`services/frontend/src/pages/LandingPage.tsx` + `index.html` meta). Don't let the docs drift behind the product.

---

## Pending / backlog ideas

- More The Muse category mappings (`_CATEGORY_TRIGGERS` in `the_muse.py`)
- Tavily enrichment could be extended beyond the research endpoint
- Optional defense-in-depth: app-level `X-Internal-Token` on internal endpoints
  (external surface is already blocked at nginx; see internal-endpoint memory)

Done recently: soft-expire (`job_ttl_days`) shipped; `email-monitor` retired and
replaced by `mcp-writer` (FastMCP); Adzuna moved to per-user BYOK.
