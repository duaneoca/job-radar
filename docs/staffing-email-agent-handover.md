# Staffing email agent — handover to job-radar

Written 2026-11-25. Hands the job-radar side of the staffing email agent to whoever builds it here.
The agent side has finished step 1 (the backlog eval) and is waiting on this repo's endpoints.

**What the feature does:** staffing-vendor emails that the agent's sorter files as
`recruiter_outreach` become jobs in Job Radar. Vendor emails for the same req are attached to the
existing job, and clear misses are filtered out. For strong matches, the agent leaves a threaded
reply draft with the resume attached. The user reviews it and sends it. Nothing is ever sent
automatically.

## Read first

The companion repo is `../job-radar-agent`. Until its PRs #22 and #23 merge, the current text is on
its branch `feat/staffing-eval`.

| Document | What it's for |
|---|---|
| `../job-radar-agent/INTEGRATION_SPEC.md` **v0.8**, §2.4 and §3.9 | **The contract.** If this handover and the spec disagree, the spec wins. Fix whichever is wrong in the same PR. |
| `../job-radar-agent/docs/staffing-email-agent.md` | Design and rationale, plus the decisions of 2026-11-13 and 2026-11-25. |
| `../job-radar-agent/docs/agent-integration.md` › "Staffing email agent work items" | This repo's work list, items 1–7. This handover adds file pointers and gotchas to it. |
| `../job-radar-agent/agent/staffing.py` | The agent's field rules, filters and allowed-state reduction. The allowed-state reduction is part of the contract: both repos must implement it the same way. |

## Decisions already made (don't reopen them)

- **Cloud and local ship together.** The feature is for Gmail and IMAP users too, not just Proton.
  A step is done when it works for the cloud agent.
- **Settings come from Job Radar.** `GET /agent/settings` serves local and cloud agents alike, and
  every behavior is configurable in the UI. Secrets and operator cost guards are never in it.
- **Jobs are created only through one shared import function,** driven by the inbox write's
  `staffing.outcome`. The agent never touches the DB `[D2]`.
- **Outcomes:** `imported` creates the job; `duplicate` attaches the email as a source of the
  matched job; `filtered` stores the inbox item only. A repeated `(user_id, message_id)` creates
  nothing.
- **Closed jobs absorb duplicates.** A match against a `dismissed`, `rejected` or `expired` job is
  a `duplicate` of it: no new job, no draft. So `GET /agent/email-jobs` returns closed jobs too,
  with their `status`, and draft candidates leave them out.
- **Inbox buttons:**
  - Filtered items show **"Import anyway"**.
  - Duplicate items show **"Not a duplicate"**: detach the email from the matched job, then import
    it as its own job.
  - Both use `POST /agent/inbox/postings/{id}/import`.
- **"W2 or C2C" is stored as plain `contract`.** The employment-type filter passes it when
  `contract_c2c` or `contract_w2` is allowed. The agent applies the filter; you store the value.
- **`work_mode` (`onsite | hybrid | remote`) replaces the `remote` boolean,** on both `Job` and
  `InboxPosting`, with a data migration.
- **One draft per job.** The draft goes to the first vendor's email, and the draft stamp blocks a
  second draft. `applied_via` records the vendor or `direct` and can be edited in the UI.

## What I found in this repo that shapes the work

1. **There is no server-side import function yet.** Today "Add Job" happens in the browser, through
   `POST /jobs/manual` (`create_manual_job`, `services/tracker-api/app/routers/jobs.py:678`), and
   then `POST /agent/inbox/postings/{id}/imported` (`routers/agent.py:1664`) records the import.
   To get one shared import function:
   - **Extract the core of `create_manual_job` into a function that doesn't commit.** The inbox
     write must create the inbox rows and the job in **one transaction**. `create_manual_job`
     commits internally and sends the Celery review task (`app.tasks.review_job`, queue `review`).
     Send that task only after the outer commit.
   - **Same-role check.** `create_manual_job` returns an existing job for a same company + title +
     city twin (`_manual_twin`, line 578) unless `separate=true`. Decide how agent imports treat
     it. **Recommendation:** agent imports behave like `separate=true` and keep only the same-link
     check. The agent has already deduplicated on the job description, and vendor "company" values
     like "Acme (agency)" make the twin check both miss and over-merge. "Not a duplicate" must also
     import separately, or it would land straight back on the job the user just said it isn't.
   - The function is used by:
     - `create_manual_job`;
     - the inbox write (`POST /agent/inbox`, `routers/agent.py:1049`);
     - `POST /agent/inbox/postings/{id}/import`;
     - a new MCP tool, `import_posting`.
2. **`JobSource` (`models.py:49`) has no `EMAIL`.** Add it. Jobs created this way carry it.
3. **`remote` is a boolean in two models:** `Job.remote` (`models.py:217`) and
   `InboxPosting.remote` (`models.py:771`). Code that reads it:
   - `routers/{jobs,criteria,agent,generate}.py`, `posting_extract.py`, `schemas.py`;
   - `ai-reviewer` (`main.py`, `reviewer.py`);
   - every scraper in `services/scraper/app/scrapers/`;
   - the frontend (`JobsPage`, `InboxPage`, `ProfilePage`, `SettingsPage`).

   Migration: `true ⇒ remote`, `false ⇒ null`. `false` never meant onsite.
4. **Writing skills have no `email_draft` scope.** `SKILL_SCOPES` is at `schemas.py:313`. Add it;
   `/agent/settings` returns the enabled skills that include it.
5. **Statuses.** `JobStatus` (`models.py:35`) includes `referral_requested`. The closed set for
   dedup and drafts is exactly `dismissed`, `rejected` and `expired`.
6. **Where settings live today.** `email_policy` and retention are columns on `EmailCredential`
   (`models.py:~837`), served only in the cloud config (`_email_policy`, `routers/agent.py:126`).
   Sorter settings exist only in the agent's `.env`. `/agent/settings` needs per-user storage for
   `sorter`, `email_policy`, `retention` and `staffing` that **local** users can reach too. They
   may have no `EmailCredential` row at all, since a local agent keeps its mail login in `.env`.
   **Recommendation:** a new per-user `agent_settings` row, with the columns already on
   `EmailCredential` read as the fallback until they're migrated.
7. **The inbox write already does dedup Phase B** (partial unique index
   `uq_inbox_postings_user_dedup_key`). Staffing postings keep sending `dedup_key`. For in-scope
   mail the outcome is decided by the job-description comparison, so a re-sent email arrives as
   `duplicate`, not as a skipped insert.
8. **Migrations.** Alembic is at `0039_agent_run_late`. Use one migration per item, so each PR is
   reviewable on its own.

## Ship order (the agent treats a missing endpoint as "feature off")

Stage A (job creation) can turn on only after items 1–5. Stage B (reply drafts) needs items 6–7.
Each item is one PR.

| # | What | Spec | Done when |
|---|---|---|---|
| 1 | Per-user settings storage + `GET /agent/settings` (dual auth: `X-Agent-Key`, or `X-Internal-Token` + `X-User-Id`). `criteria` is copied from the active `Criteria` row; `writing_skills` from enabled skills scoped `email_draft`. Server-side validation: bare hostnames, closed enums, days 1–365, threshold 0–10. | §2.4 | Tested: the response never carries a key, password or token, and never an operator guard (spend ceiling, emails per run, retention cap, poll interval). A user with no agent settings gets `{}` or defaults, not a 500. |
| 2 | Schema: `work_mode` replaces `remote` (migration); `employment_type` on jobs and postings; on inbox postings `jd_text` (≤ 20,000), `outcome`, `outcome_reason` (≤ 300), `outcome_review_id`, `apply_directly`; `JobSource.EMAIL`; job sources (the emails attached to a job: message id, sender, received at); on the review: `applied_via` and the draft stamp. | §1.2, §3.9 | Scrapers, the scorer prompt, filters and UI read `work_mode`; no `remote` reader is left. |
| 3 | The shared import function (finding 1). The inbox write accepts the new posting fields and the `staffing` block and, **in the same transaction**: `imported` ⇒ import; `duplicate` ⇒ attach as a source of `duplicate_of_review_id` (ownership-checked `[H1]`); `filtered` ⇒ store only. The response adds `review_ids[]` aligned with `posting_ids[]`. `POST /agent/inbox/postings/{id}/import` does "Import anyway" / "Not a duplicate" (detach first). MCP tool `import_posting`. | §3.1, §3.9 step 4 | Tested: a repeated `(user_id, message_id)` creates nothing and returns the existing rows; a foreign `duplicate_of_review_id` is rejected; every posting field is length-capped and escaped `[C2]`; the AI review is queued after commit. |
| 4 | `GET /agent/email-jobs?since_days=N`: email-sourced jobs, **closed ones included**, each with `status`. | §2.1 | Returns `[{review_id, company, role, location, work_mode, jd_text, created_at, applied_via, status}]`. |
| 5 | Inbox UI: outcome + reason per posting; dim filtered and duplicate items (link to the matched job); the two buttons; an "apply directly" badge. On the job: its email sources and "How I applied" (vendor / directly), editable. Profile › **Email Agent** tab (next to `AIPromptsTab`, `ProfilePage.tsx`) with the staffing settings, plus the moved sorter, email policy and retention settings; work mode, locations, min salary and excluded companies are shown read-only with a link to Criteria. | §2.2 | Everything the agent reads is editable here. |
| 6 | `GET /agent/draft-candidates` (server filter: source `email`, AI review done, `ai_score ≥ draft_score_threshold`, no stamp, `applied_via` null, not `apply_directly`, **not closed**; includes `profile_summary` = `Profile.summary` and `ai_pros`) and `POST /agent/reviews/{review_id}/draft` (idempotent stamp; sets `applied_via` to the vendor). | §3.9 Stage B | `source` is the job's FIRST email. |
| 7 | Resume PDF on the Profile page next to the text resume, labeled "Used by Job Radar Agent for reply drafts", hidden unless the agent and auto-draft are on; one file per user, PDF magic bytes checked, about 5 MB cap, no text extraction. `GET /agent/resume` (dual auth). `/agent/settings` carries `{sha256, filename, uploaded_at}`. | §3.9 | The agent downloads only when the checksum changes. |

## Cloud items (outside the code, but they block the feature)

- `TYPESAFE_API_KEY` is **not in either hosted environment**
  (`k8s/base/email-agent/secret.yaml.example`, checked 2026-11-13). The cloud sorter can't run
  without it.
- Confirm on production that dedup Phase B (#201) is live. The agent's cloud sorter was gated on
  it, and that gate gets lifted once it's confirmed.

## Security obligations on this side (spec §5)

- `[D2]` Jobs are created only through the shared import function: idempotent, ownership-checked.
- `[H1]` Identity comes from the key. Never trust a `user_id` from the body. Validate
  `outcome_review_id`, `duplicate_of_review_id` and `review_id` belong to the caller.
- `[C2]` All agent-derived fields (role, company, location, `jd_text`, reason) are length-capped,
  escaped on render, and links are scheme-allowlisted.
- `[H6a]` `/agent/settings` is reachable from outside the cluster, so it must never carry a
  secret. Credentials stay in `/agent/config` (in-cluster only).

## Checking it against the agent

- `python scripts/eval_staffing.py --criteria criteria.json` in `../job-radar-agent` is
  read-only. It shows what the agent will send for the current Interaction mail: outcomes, cleaned
  fields, duplicate pairs. The run of 2026-11-25 over 150 emails gave 48 imported, 11 duplicate
  and 29 filtered.
- The agent's Stage A code isn't written yet. It will go into `agent/sort_stage.py`, reusing
  `agent/staffing.py`, once items 1–4 ship.
