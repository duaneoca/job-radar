# Design: BYOK Adzuna + Per-User Scraping & Source-Health Fixes

**Status:** Draft for review
**Author:** Claude (pairing with Duane)
**Date:** 2026-06-12

---

## 1. Context & problems

Today the scraper pulls a **union of all approved users' criteria**, scrapes a
**shared `jobs` pool**, and **fans every job out to every user** as a
`UserJobReview`. Relevance is delegated entirely to the per-user AI score + sort.

This surfaced four concrete problems (discovered debugging staging's 7,318-job pile-up):

1. **Shared Adzuna rate ceiling.** Adzuna's free tier is **25/min, 250/day,
   1,000/week, 2,500/month** (verified from their ToS — the old `~1000/day` code
   comment is wrong). One shared key across all users caps the *entire system* at
   ~10 keyword×location combos at the current 2-hour / 2-page cadence. Staging is
   ~3× over and being throttled.
2. **Cross-user job bleed.** Because fan-out hits every user, you receive a review
   row for jobs that only matched *someone else's* criteria (e.g. `testuser`'s
   generic "Software Engineer" searches land in Duane's list, scored low but present).
   Not a data leak — per-user review data is isolated — but it bloats every list
   and doesn't scale to multiple real users.
3. **Wasted AI tokens.** Every fanned-out job is AI-ranked against every user's
   resume using *their* LLM key. You pay to score jobs that were never meant for you.
4. **Two of three sources are degraded:**
   - **Remotive → 0 jobs.** It only runs on a `remote`/`anywhere` location pass,
     but the scraper iterates each user's concrete `search_locations`; no user has
     "Remote" listed, so the remote pass never fires. Dead since launch.
   - **The Muse → fixed feed.** Pulls 3 hardcoded categories (Software Engineering,
     Data Science, Data and Analytics) and ignores user keywords. Returns the same
     ~400 software/data jobs to everyone regardless of what they're looking for.

## 2. Direction (the BYOK pivot)

Move Adzuna to **bring-your-own-key, per user**, consistent with the existing LLM
BYOK model. Each user supplies their own Adzuna `app_id` + `app_key`; we scrape
*their* criteria with *their* key and attribute the results to *only them*.

This fixes 1–3 at the root rather than patching symptoms:

- **Rate limit dissolves** — each user gets their own 250/day. A single user at
  3 titles × 2 locations × 2 pages × 12 runs ≈ 144 calls/day sits under their own
  cap, with room to broaden.
- **Bleed disappears by construction** — "my jobs" = "the jobs my scrape produced."
- **AI spend drops** — only your matched jobs get ranked.

## 3. Goals / non-goals

**Goals**
- Per-user Adzuna scraping with BYOK credentials.
- Per-user job scoping: a review exists for a user only if that user's scrape
  produced it.
- Keep the shared `jobs` table as a **dedup/storage cache** (no duplicate rows
  when two users surface the same posting).
- Fix Remotive (make the remote pass actually run).
- Decide The Muse's fate under a per-user model.
- No change required to downstream: jobs list, AI scoring, timeline, and the new
  soft-expire task all already key off `user_job_reviews.user_id`.

**Non-goals**
- Re-introducing HTML scraping of LinkedIn/Indeed/etc. (still Cloudflare-blocked).
- Paid Adzuna tiers.
- Changing the AI scoring pipeline itself.

## 4. Design

### 4.1 Data model

- **Keep `jobs` shared**, deduped by `(source, external_id)`. It's a storage cache,
  not user-owned.
- **Reviews become strictly per-scrape.** When a user's scrape finds a job, create
  a `UserJobReview` for **that user only** — never fan out to others.
- `UserJobReview`, scoring, expiry, and the list view are unchanged; they already
  filter by `user_id`.

### 4.2 Adzuna credential storage

Adzuna needs **two** values (`app_id` + `app_key`), unlike single-string LLM keys.

- **Option A (least churn):** add an `adzuna` value to the `LLMProvider` enum and
  store a JSON blob `{"app_id": "...", "app_key": "..."}` in the existing encrypted
  `api_key` column. Reuses the whole encrypt/decrypt + key-management UI.
- **Option B (cleaner):** a dedicated `adzuna_credentials` table (or two columns)
  with its own encryption.

**DECIDED → Option A.** Reuse `user_api_keys` (add an `adzuna` provider, store a
JSON `{app_id, app_key}` blob in the encrypted `api_key` column). Surface it on the
existing **API Keys settings page** alongside the LLM/Tavily keys — same add/edit/
delete UX, same Fernet encryption, no new table.

### 4.3 Scraper → tracker-api: per-user config endpoint

The scraper needs each active user's criteria + **decrypted** Adzuna creds. Keep
all crypto in tracker-api (don't hand `ENCRYPTION_KEY` to the scraper).

- New internal endpoint **`GET /scraper/user-configs`** (`include_in_schema=False`,
  no external auth, in-cluster only — same posture as `/agent/config`). Returns:
  ```json
  [
    {
      "user_id": "...",
      "job_titles": ["Forward Deployed Engineer", ...],
      "search_locations": ["Oakland, CA", ...],
      "work_style": "any",
      "adzuna": { "app_id": "...", "app_key": "..." }   // or null if not provided
    }
  ]
  ```
- Replaces the current `GET /criteria/scraper/union` for the scrape path (union can
  be retired once the public-source story below is settled).
- **Security:** decrypted secrets over in-cluster HTTP. Acceptable given the
  existing `/agent/config` precedent, but should be covered by the same
  NetworkPolicy hardening tracked for the agent endpoints (JR-5).

### 4.4 Scraper refactor

`scrape_all` changes from "one union scrape" to "loop over users":

```
configs = GET /scraper/user-configs
for cfg in configs:
    locations = cfg.search_locations + ["Remote"]      # always add a remote pass (see 4.6)
    # Adzuna — only if the user supplied a key
    if cfg.adzuna:
        for loc in locations:
            jobs = adzuna.scrape(cfg.job_titles, loc, creds=cfg.adzuna)
            post_jobs(jobs, user_id=cfg.user_id)
    # Public sources — no key, still scoped to this user's criteria
    for source in (the_muse?, remotive):
        for loc in locations:
            jobs = source.scrape(cfg.job_titles, loc)
            post_jobs(jobs, user_id=cfg.user_id)
```

- **Per-user failure isolation:** one user's bad/expired key or a source error must
  not abort other users' scrapes (wrap each user in try/except, continue).
- **`AdzunaScraper.scrape` gains a `creds` parameter** (app_id/app_key) instead of
  reading global `settings`. Falls back to the global env key only if we keep a
  system fallback (see 4.5).

### 4.5 No-key fallback policy

**DECIDED → public sources only.** No Adzuna key ⇒ no Adzuna scrape (no shared
system key). This keeps the BYOK model honest and the rate math trivial. A user
with no Adzuna key still gets The Muse + Remotive, scoped to their criteria.

Pair this with explicit onboarding warnings so a key-less user understands *why*
their results are thin — see §4.8.

### 4.6 `POST /jobs` attribution

Currently `POST /jobs` always fans out to all users. Change so the scraper attributes
each job to a single user:

- Add an optional `user_id` (or a dedicated internal route `POST /jobs/for-user`).
  When present: dedup the `Job` by `(source, external_id)`, then create a review for
  **that user only** and enqueue `review_job(job_id, user_id)`.
- The existing fan-out path can be retired once nothing uses it.

### 4.7 Public sources under a per-user model

Adzuna is 94% of volume and maps cleanly. The public sources need an explicit call:

- **Remotive (fix & keep):** the only fix needed is the remote pass (4.6 always adds
  `"Remote"`). Optionally gate on `work_style != "onsite"`. Cheap, remote-only, and
  genuinely useful for a remote search. Keyword filter already exists (note its
  `_STOPWORDS` drops "engineer"/"developer", which may over-filter — revisit).
- **The Muse (decision needed):** it ignores keywords and only knows broad
  categories. Options:
  - **(i) Map criteria → Muse category** (e.g. detect "engineer"/"data"/"product"/
    "design" in `job_titles` and request matching categories). Personalizes it.
  - **(ii) Keep as a shared software/data feed**, fanned out only to users whose
    criteria look software/data-ish (a coarse relevance gate).
  - **(iii) Drop it.** It's ~6% of volume and the least personalized source.
  - **DECIDED → (i): best-effort map of the user's `job_titles` to The Muse's broad
    categories.** Build a token→category map over The Muse's fixed category list
    (Software Engineering, Data Science, Data and Analytics, Design, Product, Sales,
    Marketing, Accounting/Finance, etc.). For each user, scan their `job_titles` for
    matching tokens and request only the matched categories; if nothing matches, fall
    back to no Muse results (rather than the current software/data default) so a
    non-tech user doesn't get irrelevant jobs. Keyword tokens that map nowhere are
    simply ignored.

### 4.8 Onboarding & missing-key warnings

Because keys are optional but the product is near-useless without them, surface the
gap proactively.

- **Login-time prompt.** After login, check which keys the user has and show a
  **dismissible banner/modal** if any are missing. Tiered messaging:
  - **Required to get results:** an **Adzuna** key (job source) **and** an **AI**
    key (any of Anthropic / OpenAI / Google / Groq — for scoring).
  - **Recommended:** a **Tavily** key (company research enrichment).
- Non-blocking (keys are optional), but persistent: keep a small indicator in the
  nav / Settings until resolved, so the prompt isn't a one-time thing easily missed.
- **"Where to get keys" page.** A help page with direct links + 1-line how-to per
  provider:
  - Adzuna → <https://developer.adzuna.com/> (register an app → `app_id` + `app_key`)
  - Anthropic → <https://console.anthropic.com/>
  - OpenAI → <https://platform.openai.com/api-keys>
  - Tavily → <https://tavily.com/>
- **Update the Help section** with a "Getting started: API keys" tab covering what
  each key does, that it's BYOK (their keys, their quotas/cost), and the links above.
- Detection uses the existing keys API (`GET /keys`); add `adzuna` to what it reports
  (hint only, never the secret).

### 4.9 Scrape triggers & cadence

- **Background cadence → every 6 hours** (down from 2h). Per-user budgets make this
  safe, and 6-hourly is plenty fresh for a job hunt while staying well under each
  user's 250/day.
- **Scrape-on-criteria-change.** When a user creates/updates their active criteria
  (`POST`/`PATCH /criteria`), enqueue an **immediate per-user scrape** for that user
  only: `scrape_user(user_id)`. This requires a per-user scrape Celery task (which
  the per-user refactor introduces anyway).
  - **Debounce:** coalesce rapid successive saves (e.g. ignore if a scrape for that
    user was enqueued in the last N minutes) so editing criteria doesn't fire a
    burst of scrapes / API calls.
  - Only fires for users with the relevant keys (no Adzuna key ⇒ criteria-change
    scrape still runs public sources).

## 5. What does NOT change

- `UserJobReview`, the jobs list/detail UI, AI scoring, timeline.
- The **soft-expire + cleanup tasks** (just shipped) — they key off review
  `user_id` + status and work identically in the new model.
- The shared `jobs` table schema (still deduped by `source` + `external_id`).

## 6. Migration & rollout

1. Ship Adzuna creds storage + Settings UI (no behavior change yet).
2. Ship `/scraper/user-configs` endpoint alongside the existing union endpoint.
3. Switch the scraper to per-user; keep a feature flag / env toggle to fall back to
   the union scrape during validation.
4. Remotive remote-pass fix can ship independently and immediately (it helps the
   current model too).
5. Existing shared/fanned-out reviews: leave them — they age out via the soft-expire
   task. No destructive migration needed. (Optionally offer an admin "prune reviews
   whose job title doesn't match my criteria" one-off for staging.)
6. **`testuser` needs no explicit retirement.** Once per-user scraping is live and
   the union/fan-out path is removed, a user with no Adzuna key produces no Adzuna
   scrape and only their own scoped public-source results — so `testuser` can no
   longer pollute anyone's list. It only matters during the transition window while
   the union path is still toggled on.

## 7. Decisions — RESOLVED

1. **Credential storage:** ✅ Option A — `adzuna` provider in `user_api_keys`
   (`{app_id, app_key}` JSON), surfaced on the existing API Keys settings page.
2. **No-key fallback:** ✅ Public sources only; no shared system key. Paired with
   onboarding warnings (§4.8).
3. **The Muse:** ✅ Best-effort map of `job_titles` → The Muse's broad categories
   (§4.7); no match ⇒ no Muse results.
4. **Adzuna at signup:** ✅ Optional, not required — degraded (public-only) results
   until added, with explicit warnings + a "where to get keys" help page (§4.8).
5. **Scrape cadence:** ✅ 6-hourly background, **plus** an immediate per-user scrape
   on criteria change, debounced (§4.9).

## 8. Phased implementation plan

- **Phase 0 (independent, ship now):** Fix Remotive's remote pass; correct the
  `~1000/day` Adzuna comment. Low risk, helps the current model today.
- **Phase 1:** `user_api_keys` `adzuna` provider (`{app_id, app_key}`) + API Keys
  settings page UI to add/edit/delete it.
- **Phase 2:** Onboarding & Help — missing-key login banner/modal (Adzuna + AI
  required, Tavily recommended), "where to get keys" links, Help "Getting started:
  API keys" tab. Can ship alongside Phase 1.
- **Phase 3:** `/scraper/user-configs` internal endpoint (criteria + decrypted creds).
- **Phase 4:** Per-user scraper loop + `AdzunaScraper` `creds` param + per-user
  `POST /jobs` attribution + `scrape_user(user_id)` task, behind a toggle. Drop
  cadence to 6h.
- **Phase 5:** Scrape-on-criteria-change trigger (debounced) wired to
  `POST`/`PATCH /criteria`.
- **Phase 6:** The Muse category mapping; retire the union endpoint + global fan-out
  (removes the cross-user bleed for good).
- **Phase 7:** Cleanup — optional one-off staging review prune, docs, remove the
  feature toggle once validated.
