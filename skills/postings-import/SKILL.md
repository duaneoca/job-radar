---
name: postings-import
description: >-
  Import job postings from your email inbox into Job Radar. Reads UNSEEN messages
  in the "Job Postings" IMAP folder (Proton Bridge), harvests posting links, opens
  each in Chrome using your logged-in session, and imports via the Job Radar
  bookmarklet — skipping anything already tracked. Run manually on the unlocked
  Mac mini. Use when the user says "process my job postings folder", "import my
  job emails", or similar.
---

# Job Radar — Postings Import

Pull job postings out of your inbox and into Job Radar, cheaply. A Sieve filter
has already sorted posting emails into a `Job Postings` folder, so this skill does
**no email classification** — it harvests links and lets the existing bookmarklet
do the capture. It is **manual and on-demand** (the Chrome step needs your
unlocked, logged-in browser).

## Cost discipline (why this exists)

The old cloud agent ran an LLM over every email every 15 minutes — expensive and
slow. This skill spends **zero inference on the routine path**: the folder is
pre-sorted, links are harvested deterministically, dedup is decided by an HTTP
status code, and extraction reuses the tuned bookmarklet. You ("Claude") only
*think* on the exception branch (see step 5). Do not "read and summarize" emails;
do not categorize; just run the loop.

## Prerequisites (check, don't assume)

- **Proton Bridge** is running locally and `./.env` is filled in (copy from
  `env.example`). Source it: `set -a; . ./.env; set +a`
- **Chrome** is open with the Claude extension connected, and you are **logged
  into `https://job-radar.net`** in that browser (the bookmarklet POSTs with your
  session).
- You are also **logged into the job sites** (LinkedIn, etc.) in that same Chrome,
  so postings render past login/Cloudflare walls.
- Supported destinations are exactly: **LinkedIn, Dice, BuiltIn, Monster,
  ZipRecruiter, Indeed.** Anything else is logged and skipped (step 5).

## The loop

### 0. Get the live bookmarklet (once per run)

The bookmarklet is the single source of truth for per-site extraction — read it
straight from the running app, don't hardcode a copy.

1. In the Chrome extension, open a tab to `https://job-radar.net/settings` and go
   to the **Bookmarklet** tab.
2. Read the bookmarklet JS out of the DOM with `javascript_tool`:
   ```js
   [...document.querySelectorAll('a')]
     .map(a => a.getAttribute('href'))
     .find(h => h && h.startsWith('javascript:'))
   ```
3. Strip the leading `javascript:` — keep the rest as `BMARK` (the IIFE body).
   `appOrigin` is baked in as `https://job-radar.net`, so it imports to the right
   place regardless of which job site you eval it on.

### 1. List unseen postings (deterministic)

```bash
python3 imap_fetch.py list
```

Returns JSON: `[{uid, subject, sender, date, links: [...]}]`. Listing uses
`BODY.PEEK` and a read-only SELECT, so it **never** marks anything seen. Junk
(unsubscribe/settings/social) is already filtered; `links` are candidate postings.

### 2. Per email → per link: open + capture (Claude drives, no inference)

For each email, for each `link`:

1. **Navigate** the Chrome tab to `link`. Your session resolves the tracking
   redirect to the real posting URL.
2. **Clear network log**, then **eval `BMARK`** in that tab (`javascript_tool`).
   The bookmarklet extracts the posting and `window.open(...)`s
   `https://job-radar.net/jobs/add#<data>`, which POSTs to `/jobs/manual`.
3. **Read the result deterministically** — inspect the `/jobs/add` tab's network
   for the import call:
   ```
   read_network_requests(tab, urlPattern="/jobs/manual")
   ```
   - HTTP **201** → imported ✅
   - HTTP **200** → already tracked, skipped ⏭ (server dedups on `external_id`)
   - **No `/jobs/manual` request at all** → the bookmarklet bailed (unsupported
     site or a non-`/jobs/view/` page). This is the exception branch → step 5.
4. Close the `/jobs/add` tab and continue.

Track per-email outcomes. A LinkedIn digest with 10 links is normal; most will be
200s (already tracked) — that's the dedup working, not an error.

### 3. Mark the email done (the only mutating IMAP call)

Once **every** link in an email is resolved (imported, skipped-as-dup, or logged
as unsupported):

```bash
python3 imap_fetch.py mark-seen <uid>
```

If anything errored mid-email, **do not** mark it — leave it UNSEEN so the next
run retries, and tell the user.

### 4. Summarize

Report: N emails processed, X imported, Y already-tracked, Z unsupported/skipped.
List the unsupported destinations (sender → URL) — that's the data that tells us
which sites to teach the bookmarklet next.

### 5. Exception branch (the only place you infer)

When a link produces **no `/jobs/manual` request**, or an email's links are
ambiguous/none, **do not silently write anything.** Stop and show the user what
you found (sender, the URL, where it resolved) and ask how to proceed. Never
hand-craft a `/jobs/manual` POST to force an import past the bookmarklet's guards.

## State machine (summary)

```
UNSEEN  ──peek+harvest──►  per-link: navigate → eval BMARK → read 200/201/none
   ▲                                                    │
   └── any error ── leave UNSEEN, report ───────────────┤
                                                        ▼
                              all links resolved ──► mark \Seen
```

## Notes

- **Dedup is the server's job**, not yours. `POST /jobs/manual` returns 200 if you
  already have the job (matched on `external_id`+`source`, fallback `url`), 201 if
  newly created. You only read the code.
- **Scope creep guard:** if you find yourself wanting to parse an email's body or
  classify it, stop — the Sieve filter and the destination bookmarklet already
  cover that. Your job is plumbing, not judgment, except in step 5.
