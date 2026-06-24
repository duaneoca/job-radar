"""
Résumé tailoring — Phase 1: ingest.

Parses the user's plain-text résumé (`Profile.resume_text`) into the canonical
structured JSON (`schemas.ResumeStructured`) and derives "honesty facts" — the
ground-truth durations/titles the tailor step checks the "meet-or-exceed, never
inflate" contract against. The LLM only ever sees text here; no PDF.
"""

import datetime as _dt
import json
import logging
import re

from fastapi import HTTPException

from app import schemas
from app.llm import llm_complete

logger = logging.getLogger(__name__)


def _loads_json_object(raw: str) -> dict:
    """Parse the first JSON object from a model response, tolerating markdown
    fences, leading prose, and TRAILING text. Some models (esp. Haiku on the
    refine prompt) append an explanation after the JSON, which plain json.loads
    rejects with 'Extra data'. We strip fences, jump to the first '{', and use
    raw_decode so anything after the object is ignored."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
        s = s.strip()
    start = s.find("{")
    if start == -1:
        raise json.JSONDecodeError("no JSON object found", s, 0)
    obj, _ = json.JSONDecoder().raw_decode(s[start:])
    return obj

# The structure we ask the model to emit. Lenient — omit unknown fields rather
# than invent. No fabrication: only what's present in the résumé text.
DEFAULT_RESUME_PARSE_PROMPT = """You are a precise résumé parser. Convert the résumé text below into JSON with EXACTLY this shape:

{
  "contact": {"name": str|null, "location": str|null, "email": str|null, "phone": str|null, "links": [str]},
  "summary": str|null,
  "skills": [{"label": str, "items": [str]}],
  "experience": [{
    "company": str, "titles": [str], "start": str|null, "end": str|null,
    "bullets": [str],
    "phases": [{"label": str|null, "start": str|null, "end": str|null, "bullets": [str]}],
    "notable": [str]
  }],
  "education": [{"degree": str|null, "school": str|null}],
  "projects": [{"title": str|null, "bullets": [str]}]
}

Rules:
- Use ONLY information present in the text. Never invent, infer, or embellish facts, dates, titles, or skills.
- Keep bullet wording verbatim where possible (you may drop a leading bullet glyph).
- If a role has sub-periods (e.g. "Building the Platform (2007–2013)"), put them in "phases"; otherwise put bullets directly on the experience and leave "phases" empty.
- "notable" is for a "Notable customers/clients" style line, split into a list.
- Years/dates: copy them as written (e.g. "2007", "2013 – 2026", "present").
- Output ONLY the JSON object — no prose, no markdown fences."""


def parse_resume_text(resume_text: str, api_key: str, model: str) -> schemas.ResumeStructured:
    """Parse résumé text → validated ResumeStructured. Raises HTTPException on
    empty input or malformed model output."""
    if not (resume_text or "").strip():
        raise HTTPException(status_code=400, detail="No résumé text to parse. Add your résumé first.")

    raw = llm_complete(
        system="You convert résumés to structured JSON. Always respond with valid JSON only.",
        messages=[{"role": "user", "content": f"{DEFAULT_RESUME_PARSE_PROMPT}\n\n--- RÉSUMÉ ---\n{resume_text}"}],
        api_key=api_key,
        model=model,
        max_tokens=4096,
    ).strip()

    try:
        data = _loads_json_object(raw)
    except json.JSONDecodeError as e:
        logger.error("Résumé parse returned malformed JSON: %s\nRaw: %s", e, raw[:500])
        raise HTTPException(status_code=502, detail="AI returned malformed JSON parsing your résumé. Try again.")

    try:
        return schemas.ResumeStructured.model_validate(data)
    except Exception as e:  # pydantic ValidationError
        logger.error("Résumé parse failed schema validation: %s", e)
        raise HTTPException(status_code=502, detail="AI returned an unexpected résumé structure. Try again.")


# ── Honesty facts ─────────────────────────────────────────────

_YEAR_RE = re.compile(r"(19|20)\d{2}")
_PRESENT_RE = re.compile(r"present|current|now", re.IGNORECASE)


def _years_in(text):
    return [int(m.group()) for m in _YEAR_RE.finditer(text or "")]


def _end_year(text, this_year: int):
    """Latest year in `text`, treating 'present'/'current' as the current year."""
    if text and _PRESENT_RE.search(text):
        return this_year
    ys = _years_in(text)
    return max(ys) if ys else None


def _start_year(text):
    ys = _years_in(text)
    return min(ys) if ys else None


def derive_honesty_facts(structured: schemas.ResumeStructured) -> dict:
    """Ground truth for the honesty contract: real durations/titles/dates derived
    from the structured résumé. `total_years_experience` is the span from the
    earliest start to the latest end — the ceiling the tailor must never exceed."""
    this_year = _dt.date.today().year
    starts: list[int] = []
    ends: list[int] = []
    employers = []

    for exp in structured.experience:
        # A role's span can live on the experience or be split across phases.
        span_texts = [exp.start, exp.end] + [p.start for p in exp.phases] + [p.end for p in exp.phases]
        s_candidates = [_start_year(t) for t in span_texts if t]
        e_candidates = [_end_year(t, this_year) for t in span_texts if t]
        s = min([x for x in s_candidates if x], default=None)
        e = max([x for x in e_candidates if x], default=None)
        if s:
            starts.append(s)
        if e:
            ends.append(e)
        employers.append({
            "company": exp.company,
            "titles": exp.titles,
            "start": exp.start,
            "end": exp.end,
            "years": (e - s) if (s and e) else None,
        })

    earliest = min(starts) if starts else None
    latest = max(ends) if ends else None
    total = (latest - earliest) if (earliest and latest) else None

    return {
        "total_years_experience": total,
        "earliest_start_year": earliest,
        "latest_end_year": latest,
        "employers": employers,
        "education": [e.model_dump() for e in structured.education],
    }


# ── Tailoring ─────────────────────────────────────────────────

# The locked honesty contract. ALWAYS prepended server-side to the user's editable
# style prompt; never stored in the editable field, so a prompt edit can't remove
# it. Parameterized with the candidate's true ceiling so the model can check
# "meet-or-exceed, never inflate" against ground truth.
HONESTY_CORE = """# RÉSUMÉ TAILORING — HONESTY CONTRACT (ALWAYS ENFORCED — overrides any style guidance below)

You realign an existing résumé to a specific job posting WITHOUT lying. Your edits are SURGICAL — change only what must change to match the posting, and leave everything else exactly as written. Absolute rules:

1. You MAY: rephrase, reorder, and re-emphasize the candidate's REAL skills and experience, and rename a technology to the posting's wording ONLY when it is the SAME technology the candidate already used (e.g. "React.js"→"React").
2. MEET-OR-EXCEED, NEVER INFLATE: you may phrase a qualification to meet or exceed a requirement ONLY when the candidate's true value already clears it. The candidate's true total experience is {total_years} years (earliest {earliest}, latest {latest}). If the posting asks for 8 years, "8+ years" is allowed; if it asks for 30, you must NOT claim 30 — keep the truth.
3. NEVER invent, inflate, or fabricate skills, technologies, employers, job titles, dates, durations, certifications, or accomplishments not present in the source résumé. In particular, you MUST NOT introduce any technology, platform, tool, framework, or product name that does not already appear in the source — even if the posting requires it. The named technologies and platforms in your output must be a SUBSET of those in the source résumé.
4. LEAVE GAPS ALONE: where the posting asks for something the résumé does not show, and the gap cannot be closed by a true synonym for what the candidate already did, leave the gap. Do NOT fill it, imply it, or hint at exposure the candidate does not have. A missing match stays missing.
5. SURGICAL, MINIMAL CHANGES: change only the wording that needs to change to align with the posting; any bullet, skill, or line that already reads well passes through UNCHANGED. Prefer the smallest edit. Edit each bullet INDEPENDENTLY in place — NEVER merge two bullets into one or split one into two. Keep the SAME sections, the SAME jobs in the same order, and the SAME NUMBER of bullets per job/section — do NOT add or remove bullets, jobs, skills groups, or sections. (Trimming for length is a later step, not yours.)
6. Do not change company names, job titles, employers, or dates unless correcting an obvious typo — these are factual anchors.

Return ONLY a JSON object:
{{"tailored": <the full résumé in the SAME schema as the input>, "notes": [{{"before": "<original text>", "after": "<new text>", "type": "vocabulary|emphasis|reorder|factual", "rationale": "<why>", "trigger": "<the VERBATIM sentence or requirement line FROM THE JOB POSTING that inspired this change — quote enough to stand on its own (a full phrase or sentence, not a single word); leave empty only if no specific line in the posting applies>"}}]}}
- "tailored" must match the input schema exactly (contact, summary, skills[], experience[], education[], projects[]).
- "notes" explains the meaningful changes you made (best-effort; the system also computes its own diff). "trigger" must be copied from the job posting text, never invented."""

# Editable style prompt — the default the user can override on the AI Prompts tab.
DEFAULT_RESUME_TAILOR_PROMPT = """Tailoring style:
- Edit surgically: change wording only where it materially improves the match to THIS posting; leave already-strong bullets untouched.
- Mirror the posting's terminology only for skills and tools the candidate actually has.
- Lead each role with the candidate's most relevant real experience for this job.
- Keep the candidate's voice; concise, results-first bullets. Never pad or add scope."""

# Paths whose change touches a factual claim (flagged "review carefully").
_FACTUAL_TOKENS = ("/company", "/titles", "/start", "/end", "/degree", "/school", "/dates")


def _tailor_messages(structured, honesty_facts, job_text, style_prompt, extra=None):
    core = HONESTY_CORE.format(
        total_years=honesty_facts.get("total_years_experience"),
        earliest=honesty_facts.get("earliest_start_year"),
        latest=honesty_facts.get("latest_end_year"),
    )
    user = (
        f"{core}\n\n# STYLE GUIDANCE (editable — never overrides the contract above)\n{style_prompt}\n\n"
        f"# JOB POSTING\n{job_text}\n\n"
        f"# SOURCE RÉSUMÉ (JSON)\n{json.dumps(structured.model_dump(), ensure_ascii=False)}"
    )
    if extra:
        user += f"\n\n# REFINEMENT REQUEST (apply, still under the contract)\n{extra}"
    return user


def tailor_resume(structured, honesty_facts, job_text, style_prompt, api_key, model, *, extra=None):
    """Run the tailor LLM call. Returns (tailored ResumeStructured, model notes list).
    The honesty core is prepended here, server-side."""
    raw = llm_complete(
        system="You tailor résumés to job postings under a strict honesty contract. Respond with valid JSON only.",
        messages=[{"role": "user", "content": _tailor_messages(structured, honesty_facts, job_text, style_prompt, extra)}],
        api_key=api_key,
        model=model,
        max_tokens=8192,
    ).strip()

    try:
        data = _loads_json_object(raw)
    except json.JSONDecodeError as e:
        logger.error("Tailor returned malformed JSON: %s\nRaw: %s", e, raw[:500])
        raise HTTPException(status_code=502, detail="AI returned malformed JSON tailoring your résumé. Try again.")

    try:
        tailored = schemas.ResumeStructured.model_validate(data.get("tailored", data))
    except Exception as e:
        logger.error("Tailored résumé failed schema validation: %s", e)
        raise HTTPException(status_code=502, detail="AI returned an unexpected tailored structure. Try again.")

    notes = data.get("notes") if isinstance(data.get("notes"), list) else []
    return tailored, notes


# ── Deterministic diff (the authoritative consent gate) ───────

def _leaves(structured: schemas.ResumeStructured) -> dict:
    """Flatten a structured résumé to {path: (section, text)} leaves so we can diff
    field- and bullet-level. Index-based paths are stable because the contract
    forbids adding/removing/reordering."""
    out: dict[str, tuple[str, str]] = {}
    if structured.summary:
        out["summary"] = ("summary", structured.summary)
    for i, g in enumerate(structured.skills):
        out[f"skills/{i}/label"] = ("skills", g.label)
        out[f"skills/{i}/items"] = ("skills", " · ".join(g.items))
    for i, e in enumerate(structured.experience):
        out[f"experience/{i}/company"] = ("experience", e.company)
        out[f"experience/{i}/titles"] = ("experience", " → ".join(e.titles))
        if e.start:
            out[f"experience/{i}/start"] = ("experience", e.start)
        if e.end:
            out[f"experience/{i}/end"] = ("experience", e.end)
        for j, b in enumerate(e.bullets):
            out[f"experience/{i}/bullets/{j}"] = ("experience", b)
        for k, p in enumerate(e.phases):
            for j, b in enumerate(p.bullets):
                out[f"experience/{i}/phases/{k}/bullets/{j}"] = ("experience", b)
        for j, n in enumerate(e.notable):
            out[f"experience/{i}/notable/{j}"] = ("experience", n)
    for i, ed in enumerate(structured.education):
        out[f"education/{i}/degree"] = ("education", ed.degree or "")
        out[f"education/{i}/school"] = ("education", ed.school or "")
    for i, pr in enumerate(structured.projects):
        if pr.title:
            out[f"projects/{i}/title"] = ("projects", pr.title)
        for j, b in enumerate(pr.bullets):
            out[f"projects/{i}/bullets/{j}"] = ("projects", b)
    return out


def _classify(path: str, model_type=None) -> str:
    """A change touching a factual anchor is 'factual' regardless of what the model
    claims (safety); otherwise trust the model's label, defaulting to 'wording'."""
    if any(tok in path for tok in _FACTUAL_TOKENS):
        return "factual"
    if model_type in ("vocabulary", "emphasis", "reorder", "factual"):
        return model_type
    return "wording"


def _change_id(path: str) -> str:
    import hashlib
    return hashlib.sha1(path.encode()).hexdigest()[:12]


def _norm_text(s) -> str:
    """Whitespace-collapsed, case-folded text for fuzzy note↔change matching."""
    return " ".join(str(s or "").split()).casefold()


def diff_structured(original: schemas.ResumeStructured, tailored: schemas.ResumeStructured,
                    notes=None) -> list[dict]:
    """Authoritative change list: walk matching leaf paths and record differences.
    Model `notes` enrich type/rationale/trigger; the diff itself is computed, not
    trusted to the model. Notes are matched primarily by the model's `after` text
    (exactly what it wrote = what the diff sees as the new value), with `before` as a
    fallback — far more reliable than before-only matching."""
    note_by_after, note_by_before = {}, {}
    for n in (notes or []):
        if not isinstance(n, dict):
            continue
        if n.get("after"):
            note_by_after.setdefault(_norm_text(n["after"]), n)
        if n.get("before"):
            note_by_before.setdefault(_norm_text(n["before"]), n)

    o, t = _leaves(original), _leaves(tailored)
    changes, matched = [], 0
    for path in sorted(set(o) | set(t)):
        before = o.get(path, (None, None))[1]
        after = t.get(path, (None, None))[1]
        if (before or "") == (after or ""):
            continue
        section = (o.get(path) or t.get(path))[0]
        kind = "modified" if (path in o and path in t) else ("removed" if path in o else "added")
        note = note_by_after.get(_norm_text(after)) or note_by_before.get(_norm_text(before)) or {}
        if note:
            matched += 1
        changes.append({
            "id": _change_id(path),
            "path": path,
            "section": section,
            "before": before,
            "after": after,
            "kind": kind,
            "type": _classify(path, note.get("type")),
            "rationale": note.get("rationale", ""),
            "trigger": note.get("trigger", ""),     # job-posting phrase that motivated it
            "decision": "pending",
        })
    logger.info("Tailor diff: %d changes, %d/%d model notes matched", len(changes), matched, len(notes or []))
    return changes


def build_tailor_state(original, tailored, notes, model, honesty_facts) -> dict:
    """Assemble the per-job tailor record stored on UserJobReview.resume_tailor."""
    import datetime as _d
    changes = diff_structured(original, tailored, notes)
    return {
        "original": original.model_dump(),
        "tailored": tailored.model_dump(),
        "changes": changes,
        "status": "draft",
        "model": model,
        "generated_at": _d.datetime.now(_d.timezone.utc).isoformat(),
        "total_years": honesty_facts.get("total_years_experience"),
        "flagged_count": sum(1 for c in changes if c["type"] == "factual"),
    }
