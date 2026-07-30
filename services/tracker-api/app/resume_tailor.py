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
5. SURGICAL, MINIMAL CHANGES: change only the wording that needs to change to align with the posting; any bullet, skill, or line that already reads well passes through UNCHANGED. Prefer the smallest edit. Edit each bullet INDEPENDENTLY in place — NEVER merge two bullets into one or split one into two. Keep the SAME sections, the SAME jobs in the SAME order, and the SAME NUMBER of bullets per job/section — do NOT add or remove bullets, jobs, skills groups, or sections. You MAY reorder bullets WITHIN a single role or section to lead with the most relevant experience; the sections, the jobs, and the skill groups themselves never move. (Trimming for length is a later step, not yours.)
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


def _tailor_messages(structured, honesty_facts, job_text, style_prompt, extra=None, skills_text=""):
    core = HONESTY_CORE.format(
        total_years=honesty_facts.get("total_years_experience"),
        earliest=honesty_facts.get("earliest_start_year"),
        latest=honesty_facts.get("latest_end_year"),
    )
    user = (
        f"{core}\n\n# STYLE GUIDANCE (editable — never overrides the contract above)\n{style_prompt}"
        f"{skills_text}\n\n"
        f"# JOB POSTING\n{job_text}\n\n"
        f"# SOURCE RÉSUMÉ (JSON)\n{json.dumps(structured.model_dump(), ensure_ascii=False)}"
    )
    if extra:
        user += f"\n\n# REFINEMENT REQUEST (apply, still under the contract)\n{extra}"
    return user


def tailor_resume(structured, honesty_facts, job_text, style_prompt, api_key, model, *,
                  extra=None, skills_text=""):
    """Run the tailor LLM call. Returns (tailored ResumeStructured, model notes list).
    The honesty core is prepended here, server-side."""
    raw = llm_complete(
        system="You tailor résumés to job postings under a strict honesty contract. Respond with valid JSON only.",
        messages=[{"role": "user", "content": _tailor_messages(structured, honesty_facts, job_text, style_prompt, extra, skills_text)}],
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

def _scalar_leaves(structured: schemas.ResumeStructured) -> dict:
    """Flatten the SCALAR fields of a résumé to {path: (section, text)}.

    Bullet lists are deliberately excluded — they are aligned by content in
    `_lists`/`_align_list` instead, because the contract lets the model reorder
    bullets within a role and index-based paths cannot express a move (a swap
    would read as two inverse edits).

    `skills/{i}/items` and `experience/{i}/titles` stay joined scalars on
    purpose: a skill group is set-ish, so a reorder card there is noise.
    """
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
    for i, ed in enumerate(structured.education):
        out[f"education/{i}/degree"] = ("education", ed.degree or "")
        out[f"education/{i}/school"] = ("education", ed.school or "")
    for i, pr in enumerate(structured.projects):
        if pr.title:
            out[f"projects/{i}/title"] = ("projects", pr.title)
    return out


def _lists(structured: schemas.ResumeStructured) -> dict:
    """Bullet-style lists as {list_path: (section, [items])} — diffed by content."""
    out: dict[str, tuple[str, list]] = {}
    for i, e in enumerate(structured.experience):
        out[f"experience/{i}/bullets"] = ("experience", list(e.bullets))
        for k, p in enumerate(e.phases):
            out[f"experience/{i}/phases/{k}/bullets"] = ("experience", list(p.bullets))
        out[f"experience/{i}/notable"] = ("experience", list(e.notable))
    for i, pr in enumerate(structured.projects):
        out[f"projects/{i}/bullets"] = ("projects", list(pr.bullets))
    return {k: v for k, v in out.items() if v[1]}


# Minimum similarity for pairing a bullet that was moved AND reworded. 0.6 is the
# same cutoff difflib.get_close_matches uses.
FUZZY_THRESHOLD = 0.60


class _Alignment:
    """Which original list item became which tailored item."""

    def __init__(self, pairs, added_j, removed_i, order):
        self.pairs = pairs            # [(orig_index, tailored_index)] sorted by orig_index
        self.added_j = added_j        # tailored indices with no original
        self.removed_i = removed_i    # original indices with no tailored
        self.order = order            # per tailored slot: orig index, or None if added

    @property
    def is_reorder(self) -> bool:
        """True iff the matched items appear in a different relative order.
        Pure adds/removes never trip this — unmatched indices are excluded."""
        seq = [i for i, _ in sorted(self.pairs, key=lambda p: p[1])]
        return seq != sorted(seq)


def _align_list(orig: list, tail: list) -> _Alignment:
    """Match original items to tailored items in three deterministic passes.

    1. exact (normalized) match, duplicate-preserving
    2. fuzzy match for items that were moved AND reworded
    3. positional pairing of whatever is left — this is what keeps a fully
       rewritten bullet a single 'modified' change instead of remove + add
    """
    from collections import deque
    from difflib import SequenceMatcher

    pairs: list[tuple[int, int]] = []
    free_o, free_t = set(range(len(orig))), set(range(len(tail)))

    # 1. exact
    buckets: dict[str, deque] = {}
    for i, item in enumerate(orig):
        buckets.setdefault(_norm_text(item), deque()).append(i)
    for j in range(len(tail)):
        d = buckets.get(_norm_text(tail[j]))
        if d:
            i = d.popleft()
            pairs.append((i, j))
            free_o.discard(i)
            free_t.discard(j)

    # 2. fuzzy — ties prefer the smallest displacement so the result never
    #    depends on iteration order
    cands = []
    for i in sorted(free_o):
        for j in sorted(free_t):
            ratio = SequenceMatcher(None, _norm_text(orig[i]), _norm_text(tail[j])).ratio()
            if ratio >= FUZZY_THRESHOLD:
                cands.append((ratio, i, j))
    for ratio, i, j in sorted(cands, key=lambda c: (-c[0], abs(c[1] - c[2]), c[1], c[2])):
        if i in free_o and j in free_t:
            pairs.append((i, j))
            free_o.discard(i)
            free_t.discard(j)

    # 3. positional leftovers
    for i, j in zip(sorted(free_o), sorted(free_t)):
        pairs.append((i, j))
        free_o.discard(i)
        free_t.discard(j)

    by_t = {j: i for i, j in pairs}
    return _Alignment(
        pairs=sorted(pairs),
        added_j=sorted(free_t),
        removed_i=sorted(free_o),
        order=[by_t.get(j) for j in range(len(tail))],
    )


def _classify(path: str, model_type=None) -> str:
    """A change touching a factual anchor is 'factual' regardless of what the model
    claims (safety); otherwise trust the model's label, defaulting to 'wording'."""
    if any(tok in path for tok in _FACTUAL_TOKENS):
        return "factual"
    if model_type in ("vocabulary", "emphasis", "reorder", "factual"):
        return model_type
    return "wording"


def _cid(*parts: str) -> str:
    """Change id derived from CONTENT, not position.

    Positional ids (the old sha1(path)) changed whenever a bullet moved, which
    silently dropped the user's accept/reject decisions on refine. Keying on the
    list path plus the original index and text keeps identity stable across a
    reorder — refine always diffs against the untouched original.
    """
    import hashlib
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:12]


def _path_sort_key(path: str):
    """Sort paths with numeric segments compared as numbers, so bullets/2 comes
    before bullets/10, and a list's reorder card sorts just above its items."""
    return tuple((1, int(tok), "") if tok.isdigit() else (0, 0, tok)
                 for tok in path.split("/"))


def _norm_text(s) -> str:
    """Whitespace-collapsed, case-folded text for fuzzy note↔change matching."""
    return " ".join(str(s or "").split()).casefold()


def diff_structured(original: schemas.ResumeStructured, tailored: schemas.ResumeStructured,
                    notes=None) -> list[dict]:
    """Authoritative change list. The diff is computed, never trusted to the model;
    model `notes` only enrich type/rationale/trigger.

    Scalar fields diff by path. Bullet lists diff by CONTENT alignment, so moving a
    bullet produces ONE 'reordered' change for the list rather than a chain of
    inverse edits — and a bullet that was moved *and* reworded still gets its own
    'modified' card for the wording.
    """
    notes = [n for n in (notes or []) if isinstance(n, dict)]
    note_by_after, note_by_before = {}, {}
    for n in notes:
        if n.get("after"):
            note_by_after.setdefault(_norm_text(n["after"]), n)
        if n.get("before"):
            note_by_before.setdefault(_norm_text(n["before"]), n)
    used_notes: set[int] = set()

    def take_note(before, after) -> dict:
        """Bind one model note to one change. Pure moves are skipped: their text is
        unchanged, so text-matching would steal another bullet's rationale."""
        if (before or "") == (after or ""):
            return {}
        for key, table in ((_norm_text(after), note_by_after), (_norm_text(before), note_by_before)):
            n = table.get(key)
            if n is not None and id(n) not in used_notes:
                used_notes.add(id(n))
                return n
        return {}

    changes: list[dict] = []
    seen_ids: set[str] = set()

    def add(change: dict) -> None:
        """Append, keeping ids unique even if a résumé repeats a bullet verbatim."""
        cid, n = change["id"], 2
        while change["id"] in seen_ids:
            change["id"] = _cid(cid, f"#{n}")
            n += 1
        seen_ids.add(change["id"])
        changes.append(change)

    # ── scalar fields ────────────────────────────────────────
    o, t = _scalar_leaves(original), _scalar_leaves(tailored)
    for path in set(o) | set(t):
        before = o.get(path, (None, None))[1]
        after = t.get(path, (None, None))[1]
        if (before or "") == (after or ""):
            continue
        section = (o.get(path) or t.get(path))[0]
        note = take_note(before, after)
        add({
            "id": _cid(path, _norm_text(before)),
            "path": path,
            "section": section,
            "before": before,
            "after": after,
            "kind": "modified" if (path in o and path in t) else ("removed" if path in o else "added"),
            "type": _classify(path, note.get("type")),
            "rationale": note.get("rationale", ""),
            "trigger": note.get("trigger", ""),     # job-posting phrase that motivated it
            "decision": "pending",
            "list_path": None,
        })

    # ── bullet lists ─────────────────────────────────────────
    ol, tl = _lists(original), _lists(tailored)
    for list_path in set(ol) | set(tl):
        section = (ol.get(list_path) or tl.get(list_path))[0]
        o_items = ol.get(list_path, (None, []))[1]
        t_items = tl.get(list_path, (None, []))[1]
        al = _align_list(o_items, t_items)

        if al.is_reorder:
            note = next((n for n in notes
                         if n.get("type") == "reorder" and id(n) not in used_notes), None)
            if note:
                used_notes.add(id(note))
            add({
                "id": _cid(list_path, ":order"),
                "path": list_path,
                "section": section,
                # Numbered text so an older frontend still renders something useful.
                "before": "\n".join(f"{n}. {b}" for n, b in enumerate(o_items, 1)),
                "after": "\n".join(f"{n}. {b}" for n, b in enumerate(t_items, 1)),
                "kind": "reordered",
                "type": "reorder",
                "rationale": (note or {}).get("rationale", "")
                             or "Bullets re-ordered within this section.",
                "trigger": (note or {}).get("trigger", ""),
                "decision": "pending",
                "list_path": list_path,
                "before_items": list(o_items),
                "after_items": list(t_items),
                "order": al.order,
                "removed_indices": al.removed_i,
                "orig_path": list_path,
                "tailored_path": list_path,
            })

        for i, j in al.pairs:
            if o_items[i] == t_items[j]:
                continue                      # moved only — covered by the reorder card
            note = take_note(o_items[i], t_items[j])
            add({
                "id": _cid(list_path, str(i), _norm_text(o_items[i])),
                "path": f"{list_path}/{i}",
                "section": section,
                "before": o_items[i],
                "after": t_items[j],
                "kind": "modified",
                "type": _classify(list_path, note.get("type")),
                "rationale": note.get("rationale", ""),
                "trigger": note.get("trigger", ""),
                "decision": "pending",
                "list_path": list_path,
                "orig_index": i,
                "new_index": j,
                "orig_path": f"{list_path}/{i}",
                "tailored_path": f"{list_path}/{j}",
            })

        for i in al.removed_i:
            note = take_note(o_items[i], None)
            add({
                "id": _cid(list_path, str(i), _norm_text(o_items[i])),
                "path": f"{list_path}/{i}",
                "section": section,
                "before": o_items[i],
                "after": None,
                "kind": "removed",
                "type": _classify(list_path, note.get("type")),
                "rationale": note.get("rationale", ""),
                "trigger": note.get("trigger", ""),
                "decision": "pending",
                "list_path": list_path,
                "orig_index": i,
                "new_index": None,
                "orig_path": f"{list_path}/{i}",
                "tailored_path": None,
            })

        for j in al.added_j:
            note = take_note(None, t_items[j])
            add({
                "id": _cid(list_path, "+", str(j), _norm_text(t_items[j])),
                "path": f"{list_path}/+{j}",
                "section": section,
                "before": None,
                "after": t_items[j],
                "kind": "added",
                "type": _classify(list_path, note.get("type")),
                "rationale": note.get("rationale", ""),
                "trigger": note.get("trigger", ""),
                "decision": "pending",
                "list_path": list_path,
                "orig_index": None,
                "new_index": j,
                "orig_path": None,
                "tailored_path": f"{list_path}/{j}",
            })

    changes.sort(key=lambda c: _path_sort_key(c["path"]))
    logger.info("Tailor diff: %d changes (%d re-ordered), %d/%d model notes matched",
                len(changes), sum(1 for c in changes if c["kind"] == "reordered"),
                len(used_notes), len(notes))
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
        "reorder_count": sum(1 for c in changes if c["kind"] == "reordered"),
    }


# ── Effective résumé (what actually gets printed) ─────────────

def _get_path(obj, path: str):
    cur = obj
    for tok in path.split("/"):
        if cur is None:
            return None
        cur = cur[int(tok)] if tok.isdigit() else cur.get(tok)
    return cur


def _set_path(obj, path: str, value) -> None:
    toks = path.split("/")
    cur = obj
    for tok in toks[:-1]:
        if cur is None:
            return
        cur = cur[int(tok)] if tok.isdigit() else cur.get(tok)
    if cur is None:
        return
    last = toks[-1]
    if last.isdigit():
        cur[int(last)] = value
    else:
        cur[last] = value


def _legacy_effective(state: dict) -> dict:
    """Pre-reorder behaviour: clone tailored, revert every rejected leaf. Still used
    for tailor states stored before list-aware diffing shipped."""
    import copy
    eff = copy.deepcopy(state.get("tailored") or {})
    for c in state.get("changes") or []:
        if c.get("decision") == "rejected":
            _set_path(eff, c["path"], _get_path(state.get("original") or {}, c["path"]))
    return eff


def effective_resume(state: dict) -> dict:
    """The résumé the user has actually approved: tailored content, with every
    REJECTED change reverted. Pending and accepted changes keep the tailored value,
    matching the rule every other change type has always followed.

    Lists are rebuilt from the original so a rejected reorder can restore the
    original ORDER while independently-accepted rewordings survive — something the
    old per-path revert could not express.
    """
    import copy

    changes = state.get("changes") or []
    if not any(c.get("list_path") for c in changes):
        return _legacy_effective(state)

    eff = copy.deepcopy(state.get("tailored") or {})
    orig = state.get("original") or {}

    for c in changes:
        if c.get("list_path") is None and c.get("decision") == "rejected":
            _set_path(eff, c["path"], _get_path(orig, c["path"]))

    by_list: dict[str, list] = {}
    for c in changes:
        if c.get("list_path"):
            by_list.setdefault(c["list_path"], []).append(c)

    for list_path, cs in by_list.items():
        o_items = _get_path(orig, list_path) or []
        reorder = next((c for c in cs if c["kind"] == "reordered"), None)
        by_oi = {c["orig_index"]: c for c in cs
                 if c.get("orig_index") is not None and c["kind"] != "reordered"}

        # Text for each surviving original item.
        text, alive = {}, []
        for i, item in enumerate(o_items):
            c = by_oi.get(i)
            if c and c["kind"] == "removed":
                if c.get("decision") != "rejected":
                    continue                       # removal stands
                text[i] = item                     # user kept it
            elif c and c.get("decision") == "rejected":
                text[i] = item
            elif c:
                text[i] = c["after"]
            else:
                text[i] = item
            alive.append(i)

        if reorder is not None and reorder.get("decision") != "rejected":
            order = [oi for oi in (reorder.get("order") or []) if oi is None or oi in text]
        else:
            order = sorted(alive)

        added = {c["new_index"]: c for c in cs if c["kind"] == "added"}
        out = []
        for slot, oi in enumerate(order):
            if oi is None:
                c = added.get(slot)
                if c and c.get("decision") != "rejected":
                    out.append(c["after"])
            else:
                out.append(text[oi])
        # Kept-but-un-permuted additions (rejected reorder still keeps accepted adds).
        if reorder is None or reorder.get("decision") == "rejected":
            for slot in sorted(added):
                c = added[slot]
                if c.get("decision") != "rejected":
                    out.insert(min(slot, len(out)), c["after"])
        _set_path(eff, list_path, out)

    return eff
