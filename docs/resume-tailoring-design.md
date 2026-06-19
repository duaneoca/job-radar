# Résumé Tailoring — Design

**Status:** design, pre-build (validated with prototypes); key decisions resolved (§14).
Awaiting go-ahead to implement.
**Owner:** Duane. **Last updated:** 2026-06-19.

---

## 1. Problem & goal

ATS keyword filters reject résumés whose skills genuinely match a posting but use
**different vocabulary** ("React.js" vs "React", "data pipelines" vs "ETL"). Duane's
résumé is well-polished; the need is to **realign wording, technology names, and the
framing of experience to a specific job posting** — within strict honesty — and export
a **PDF that matches his résumé's look**, with a **diff** of every change for review.

**Hard constraint:** never lie. Rephrase, reorder, and emphasize real skills; never
invent or inflate.

---

## 2. Core principles

1. **The LLM never touches a PDF.** Reading/writing PDF bytes with a model is expensive
   and unreliable. The model only ever operates on **text/structured content** (one call,
   résumé + job description in, tailored content + change list out — same cost class as
   the existing cover-letter generator). PDF is rendered **deterministically** from a
   template. The diff is computed on **text/JSON**. No model in the render or diff path.
2. **Honesty is a locked, server-side layer** (see §3) — it cannot be edited away.
3. **Multi-user via a content/format split.** The per-user path is *content* only; *format*
   comes from a shared template library. No bespoke per-user layout code.

---

## 3. The honesty contract (the heart of the feature)

A single enforceable rule, derived from Duane's own reasoning:

> **Meet-or-exceed, never inflate.** The tool may phrase a qualification to *meet or
> exceed* what a posting asks **only when the true value already clears that bar.** It can
> never claim beyond reality.

Worked examples (26 years of real experience):
- Posting wants **8 years** → "**8+ years**" ✅ (true — he has at least 8).
- Posting wants **30 years** → stays **26** (or "25+"), never "30" ✅.
- Skills: present a skill *at or below* real proficiency to match wording, never above.

**Change classification.** Every change the model proposes is tagged:
- `vocabulary` / `phrasing` and `reorder` / `emphasis` → **safe**.
- Anything touching a **factual claim** (years, titles, employers, dates, certs) →
  **flagged "review carefully"** and shown distinctly in the diff.

**Enforcement & UI.** The honesty contract is a **locked core** prepended **server-side**
to the user's editable style prompt at call time. It is **not** stored in the editable
field, so there is nothing in the UI to accidentally delete — even a blank style prompt
runs with the full guardrail. On the **AI Prompts** tab the contract is shown in a
**read-only callout** (lock icon, *"Always applied — can't be edited, for your
protection"*) **verbatim** above the editable prompt, so the user sees exactly what
constrains the model.

---

## 4. Architecture overview

```
                 ┌─ ingest (once, per user) ─┐
  résumé (.docx/ │  LLM parse → structured    │
   PDF / text) ──┤  résumé JSON  +  honesty   │
                 │  facts (real dates/years)  │
                 └────────────┬───────────────┘
                              │  (stored on Profile)
 job posting ───┐            ▼
                ├─►  LLM TAILOR  (locked honesty core + editable style prompt)
 honesty facts ─┘            │
                             ▼
              tailored JSON + change list (classified)
                             │
                   ┌─────────▼──────────┐
                   │ 3-pane REVIEW page │  accept/reject per change,
                   │ original │ Δ │ new │  stateful refine loop
                   └─────────┬──────────┘
                             │ approved JSON
                  template (React component) + per-user knobs
                             │
                             ▼
                   styled HTML page ──► browser print → PDF
```

One LLM call per tailor (and per refine), text-only. No PDF bytes are ever stored —
the tailored **JSON** is stored per job and rendered on demand.

---

## 5. Ingest (text-only, multi-user-safe)

**Input is the plain-text résumé only** (`Profile.resume_text`) — no `.docx`/PDF parsing.
This eliminates layout misinterpretation; the text is parsed into the **structured JSON
schema** via one LLM call. This is the only per-user content step and it generalizes to
anyone. (A user's `.docx` is used *only* as a visual reference when an admin builds a
matching template — see §7 — never as runtime input.)

**Refresh on edit.** `resume_text` is the source of truth; `resume_structured` is derived.
Editing the text marks the structured JSON **stale**; it is re-parsed lazily (on the next
tailor, or via an explicit "re-sync" button) to avoid spending BYOK tokens on every keystroke
of editing. **Per-job tailored copies are snapshots** — if the base résumé changes we do
**not** mutate them; they are flagged *"based on a previous résumé version — regenerate to
refresh."*

```jsonc
{
  "contact": { "name": "...", "location": "...", "email": "...", "links": ["..."] },
  "summary": "...",
  "skills":  [ { "label": "Languages & Scripting", "items": ["Python","Java","SQL"] } ],
  "experience": [
    { "company": "...", "titles": ["..."], "start": "2007", "end": "2026",
      "phases": [ { "label": "Building the Platform", "start": "2007", "end": "2013",
                    "bullets": ["..."] } ],
      "notable": ["..."] }
  ],
  "education": [ { "degree": "...", "school": "..." } ],
  "projects":  [ { "title": "...", "bullets": ["..."] } ]
}
```

- **Honesty facts** (total years overall and per-skill, true titles/dates) are *derived*
  from the real dates so the tailor step has ground truth to check the contract against.

---

## 6. Tailoring (per job)

- **Inputs:** structured résumé JSON, the job posting text, honesty facts, the locked
  honesty core, and the user's editable style prompt.
- **Outputs:** a tailored copy of the structured JSON **plus a change list**:
  `[{ section, path, before, after, type: vocabulary|emphasis|reorder|factual, rationale }]`.
- **Prompt storage:** mirrors the other prompts — a new nullable `resume_tailor_prompt` on
  `criteria`, defaulted + editable on the **AI Prompts** tab (`value ?? DEFAULT`, blank =
  default), used as `criteria.resume_tailor_prompt or DEFAULT_RESUME_TAILOR_PROMPT`. The
  locked honesty core is prepended server-side and is **not** part of this field.
- **BYOK:** uses the existing `get_llm_provider` → `llm_complete` path, the user's own key.

---

## 7. Rendering & export

### Template library (shared, multi-user)
Templates are **React components** fed the structured JSON. The user **picks** one; we do
**not** auto-replicate each user's original file. Duane's `.docx` can become one library
template, but the system never depends on bespoke per-user layout.

Validated starting library (prototyped against Duane's real content):
- **Classic** — single column, navy accents. Most ATS-safe (linear text). Multi-page-friendly.
- **Modern** — navy sidebar (contact + skills) + main column. More visual variety.
  **One-page design:** a full-bleed colored sidebar can't continue cleanly onto a second
  printed page. **Selection rule: sidebar ⇒ 1 page; single-column ⇒ any length.**

Start with **two** templates; the library can grow. The template page carries a static
note for users who want their own look:
> *"Want a template shaped like your own résumé? Email \<admin address\> and attach it."*
An admin then builds a matching React template (using the attached file purely as a visual
reference) and adds it to the shared library. This is the codified path to bespoke looks
without per-user layout code. (Admin/support address comes from config, e.g. the existing
`ADMIN_EMAIL` / a support alias.)

### Export = print-to-PDF (no server PDF engine)
The styled HTML page **is** the PDF, via the browser's print (`@media print` / `@page`).
No WeasyPrint/LibreOffice in the cluster; rendering is client-side and inherently
multi-user. Validated print rules:
- **`@page { margin }`** supplies margins on **every** page (incl. continuations) — the fix
  for "content runs to the bottom edge / next section starts at the very top."
- **Keep-together:** `break-after: avoid` on headings (no orphaned section titles);
  `break-inside: avoid` on each entry/bullet (no split entries).
- **Autofit:** a **binary search** for the largest `--scale` (font + em-based spacing) that
  fits a target page count, bounded by a floor. Automates the manual "make it a bit
  smaller" pass; deterministic for a given content+template.
- Screen page-card chrome is `@media screen` only; print geometry is purely `@page`.
- `print-color-adjust: exact` for colored backgrounds (Modern sidebar) — and the user must
  enable "Background graphics" in the print dialog.

### Pagination: Paged.js (adopted)
**Decision:** build on **Paged.js** from the start — pure CSS was immediately fiddly on
real résumés, and getting the look right matters. Paged.js is a client-side CSS Paged Media
polyfill that splits content into **real page boxes**, so we get a true paged preview,
reliable break rules, and surgical control. It keeps the no-server-PDF property. The
`@page` margins + keep-together rules + autofit still apply *within* it.

### Manual page breaks (draggable)
Because export is browser-print with **no post-export editing** (§3 of the user's
decisions), the front end must let the user get breaks exactly right. With Paged.js this is
clean:
- Every block (section / entry / bullet) gets a **stable id**.
- A draggable break handle **snaps to the gaps between blocks** — you can't cleanly break
  mid-paragraph, and snapping to a logical boundary *is* the intent. Dropping it sets
  `break-before: page` on that block; Paged.js re-flows and the rest moves to the next page
  (a clean break even if it lands ¾ down a page).
- Manual breaks are stored per tailored résumé as a small list (`force_break_before:
  [blockId, …]`), and are removable.
- **Interaction with autofit:** manual breaks take precedence (they define *where* pages
  split); autofit may still scale the font within the resulting page count. "Auto-fit" and
  "manual breaks" are presented as complementary, not competing.

---

## 8. Per-user template knobs

Users tune the **shared** template via a small set of **bounded knobs** (not by editing
template code), stored as values and applied as CSS variables:

```jsonc
{ "template": "classic",
  "base_font_pt": 10.0,     // ~9–11pt
  "density": "normal",      // line-height + section spacing: compact|normal|roomy
  "margin_in": 0.5,         // 0.4–0.75
  "accent": "#1f3a5f",
  "autofit": "fit-2-pages"  // or "manual" (use base_font as-is); autofit honors min/max
}
```

- **Profile-level default** + optional **per-résumé override** (a specific tailored copy
  may need a slightly smaller font to fit). Same split as structured-résumé (profile) vs
  tailored-copy (per review).
- **Live preview** as knobs change; the same values drive the printed PDF.
- **"Fits on page 1" readout.** The review UI can *tell* the user when a section clears the
  page break, not just let them eyeball it — a small but genuinely useful affordance.

**Validated example** (Classic, Duane's content): at **10.3pt**, "Current Projects" spills
onto page 2 by 62px; dropping to **9.85pt** (~4%) pulls it fully onto page 1. A small,
readable nudge — exactly the per-user tweak this knob enables.

---

## 9. Diff & review

A dedicated full-width **pop-out** route (`/jobs/:id/tailor`); the job-detail page is too
cramped. Three columns:
- **Left:** original résumé (rendered in the template).
- **Right:** tailored résumé (the *same* page that prints — WYSIWYG).
- **Middle:** the change map.

Interactions:
- Per-change **Accept / Reject**. Accepted persist; **rejected revert and are "pinned"**
  (the model is told not to touch that spot again).
- **Stateful refine loop** (reuses the existing `refine_application` pattern): a text field
  ("emphasize cloud architecture; leave the summary alone") runs another pass that respects
  pins + accepted state and produces a fresh diff. Converges instead of thrashing.
- Factual-claim changes are visually flagged (§3).
- State (tailored JSON, per-change decisions, refine history) persists per job.

**Middle column tiers:** MVP = color-coded change chips with Accept/Reject + rationale,
click-to-scroll-and-highlight both panes. V2 = merge-tool connector lines.

---

## 10. Data model & surface (sketch)

- **Profile:** `resume_structured` (JSON, parsed once), `resume_template_settings` (JSON,
  §8 knobs), default `template`.
- **Criteria:** `resume_tailor_prompt` (nullable Text) — editable style prompt (§6).
- **UserJobReview:** `tailored_resume` (JSON), `tailor_changes` (JSON list),
  `tailor_status`, optional per-résumé knob override. (Like `application_answers`.)
- **Endpoints** (mirror `generate.py`): `POST /profile/resume/ingest` (one-time parse),
  `POST /jobs/{review_id}/tailor-resume`, `POST /jobs/{review_id}/tailor-resume/refine`,
  `PATCH` to accept/reject/edit. **No PDF endpoint** — rendering + print are client-side.

---

## 11. Security & data

- **Honesty guardrail** (§3) is the primary safety property; the **diff is the consent
  gate** — nothing exports unapproved.
- **BYOK** LLM via the user's key; **no PDF bytes stored** (tailored JSON only, rendered on
  demand).
- Standard per-user isolation (all new rows keyed by `user_id`, cascade on user delete).

---

## 12. Phasing

1. **Ingest + structured parse + honesty facts** (foundation).
2. **Tailor + diff review** (the value — all text, no PDF yet).
3. **Template render via Paged.js → print-to-PDF** (Classic + Modern; `@page`/keep-together/
   autofit within Paged.js). PDF-only (browser print).
4. **Per-user knobs** + live preview + "fits page 1" readout + **draggable manual breaks**.
5. **(later)** more templates via the admin-request flow; accept/reject connector lines in
   the diff middle column.

The riskiest/most-valuable part (honest tailoring + auditable diff) is usable before the
pixel-level pagination work is finalized.

---

## 13. Validated by prototype (2026-06-19)

Two standalone HTML templates were built from Duane's real résumé content and verified by
rendering + measuring in a real browser:
- **Classic** print path clean after fixing a `box-sizing` width bug (`#doc` overflowed the
  card); contact line wraps; `@page` margins fixed the continuation-page top-edge issue.
- **Modern** fits one page via the autofit **binary search** (largest scale that fits) once
  `min-height` was removed and vertical padding made scale-aware; lands ~0.76 scale for
  this content (the "content lever" — trimming a bullet — is preferable to extreme shrink).
- **Tuning** demo: a live font slider with a "fits on page 1" readout and a page-break
  guide line proved the bounded-knob model and produced the 10.3pt→9.85pt finding above.

---

## 14. Resolved decisions (2026-06-19)

1. **Templates:** start with **two** (Classic + Modern); library grows via the admin
   custom-template request flow (§7).
2. **Pagination:** **adopt Paged.js from the start** — getting the look right matters and
   pure CSS was immediately fiddly on a real résumé.
3. **Export:** **PDF via the browser's print** only (no `.docx`). Trade-off accepted: no
   post-export tweaking, so the **front end must get it right** (autofit, knobs, draggable
   breaks).
4. **Knobs (v1):** font size, density, margin, accent, autofit mode — **plus the draggable
   manual page-break bar** (§7).
5. **Input:** **text-only** (`resume_text`); structured JSON re-parsed on edit (lazy);
   tailored copies are snapshots flagged stale when the base changes (§5).
