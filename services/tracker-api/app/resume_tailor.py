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


def parse_resume_text(resume_text: str, api_key: str, model: str, *,
                      db=None, user_id=None) -> schemas.ResumeStructured:
    """Parse résumé text → validated ResumeStructured. Raises HTTPException on
    empty input or malformed model output.

    `db`/`user_id` are optional only so the signature stays usable from a script;
    every caller in the app passes them, so a dead key found while parsing raises
    the same banner as one found while scoring."""
    if not (resume_text or "").strip():
        raise HTTPException(status_code=400, detail="No résumé text to parse. Add your résumé first.")

    raw = llm_complete(
        system="You convert résumés to structured JSON. Always respond with valid JSON only.",
        messages=[{"role": "user", "content": f"{DEFAULT_RESUME_PARSE_PROMPT}\n\n--- RÉSUMÉ ---\n{resume_text}"}],
        api_key=api_key,
        model=model,
        # A parsed résumé is structural JSON whose size scales with the résumé.
        # Same failure mode as interview prep if the ceiling is too low.
        max_tokens=8192,
        db=db,
        user_id=user_id,
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
                  extra=None, skills_text="", db=None, user_id=None):
    """Run the tailor LLM call. Returns (tailored ResumeStructured, model notes list).
    The honesty core is prepended here, server-side."""
    raw = llm_complete(
        system="You tailor résumés to job postings under a strict honesty contract. Respond with valid JSON only.",
        messages=[{"role": "user", "content": _tailor_messages(structured, honesty_facts, job_text, style_prompt, extra, skills_text)}],
        api_key=api_key,
        model=model,
        max_tokens=8192,
        db=db,
        user_id=user_id,
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


# Object arrays whose ENTRIES can move as a unit — a project promoted to the top,
# a skill group reordered. Identity is the entry's heading: that is how a person
# recognises an entry, and it survives its bullets being reworded.
_ENTRY_ARRAYS = {
    "skills":     ("skills",     lambda e: e.label or ""),
    "experience": ("experience", lambda e: e.company or ""),
    "education":  ("education",  lambda e: " ".join(x for x in (e.degree, e.school) if x)),
    "projects":   ("projects",   lambda e: e.title or ""),
}

# Sub-fields rendered as one joined string for display but stored as a list. The
# change carries the real list in *_value so applying it can never write the
# " · "-joined display form back into the array.
_JOINED_FIELDS = {"items": " · ", "titles": " → "}


def _entry_fields(array: str, e) -> tuple[dict, dict]:
    """(scalar sub-paths, list sub-paths) for ONE entry of an object array."""
    if array == "skills":
        return {"label": e.label, "items": " · ".join(e.items)}, {}
    if array == "experience":
        scalars = {"company": e.company, "titles": " → ".join(e.titles)}
        if e.start:
            scalars["start"] = e.start
        if e.end:
            scalars["end"] = e.end
        lists = {"bullets": list(e.bullets), "notable": list(e.notable)}
        for k, p in enumerate(e.phases):
            lists[f"phases/{k}/bullets"] = list(p.bullets)
        return scalars, lists
    if array == "education":
        return {"degree": e.degree or "", "school": e.school or ""}, {}
    return ({"title": e.title} if e.title else {}), {"bullets": list(e.bullets)}


def _raw_field(array: str, e, sub: str):
    """The stored (unjoined) value behind a scalar sub-path."""
    if sub == "items":
        return list(e.items)
    if sub == "titles":
        return list(e.titles)
    return getattr(e, sub, None)


def _entry_summary(array: str, e) -> str:
    """One-line description of a whole entry, for added/removed cards."""
    section, keyfn = _ENTRY_ARRAYS[array]
    _, lists = _entry_fields(array, e)
    bullets = [b for items in lists.values() for b in items]
    head = keyfn(e) or "(untitled)"
    return f"{head}: {bullets[0]}" if bullets else head
# Minimum similarity for pairing an item that was moved AND reworded. 0.6 is the
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

    Both levels of a résumé are aligned by CONTENT rather than position:
    the ENTRIES of a section (projects, roles, skill groups) and the bullets inside
    each entry. So promoting a project to the top is ONE "reordered" change, not a
    title edit plus a body edit for every slot it shifted through. Every path is
    anchored to the entry's ORIGINAL index, which is what keeps a decision attached
    to its content across a re-order and across refine.
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
        unchanged, so text-matching would steal another entry's rationale."""
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
        """Append, keeping ids unique even when a résumé repeats text verbatim."""
        cid, n = change["id"], 2
        while change["id"] in seen_ids:
            change["id"] = _cid(cid, f"#{n}")
            n += 1
        seen_ids.add(change["id"])
        changes.append(change)

    def emit_reorder(path, section, before_items, after_items, order, removed_i, label):
        note = next((n for n in notes if n.get("type") == "reorder" and id(n) not in used_notes), None)
        if note:
            used_notes.add(id(note))
        add({
            "id": _cid(path, ":order"),
            "path": path,
            "section": section,
            # Numbered text so an older frontend still renders something readable.
            "before": "\n".join(f"{n}. {b}" for n, b in enumerate(before_items, 1)),
            "after": "\n".join(f"{n}. {b}" for n, b in enumerate(after_items, 1)),
            "kind": "reordered",
            "type": "reorder",
            "rationale": (note or {}).get("rationale", "") or label,
            "trigger": (note or {}).get("trigger", ""),
            "decision": "pending",
            "list_path": path,
            "before_items": list(before_items),
            "after_items": list(after_items),
            "order": order,
            "removed_indices": removed_i,
            "orig_path": path,
            "tailored_path": path,
        })

    def diff_list(list_path, section, o_items, t_items):
        """Bullet-level alignment inside one entry."""
        al = _align_list(o_items, t_items)
        if al.is_reorder:
            emit_reorder(list_path, section, o_items, t_items, al.order, al.removed_i,
                         "Bullets re-ordered within this section.")
        for i, j in al.pairs:
            if o_items[i] == t_items[j]:
                continue                      # moved only — the reorder card covers it
            note = take_note(o_items[i], t_items[j])
            add({
                "id": _cid(list_path, str(i), _norm_text(o_items[i])),
                "path": f"{list_path}/{i}", "section": section,
                "before": o_items[i], "after": t_items[j], "kind": "modified",
                "type": _classify(list_path, note.get("type")),
                "rationale": note.get("rationale", ""), "trigger": note.get("trigger", ""),
                "decision": "pending", "list_path": list_path,
                "orig_index": i, "new_index": j,
                "orig_path": f"{list_path}/{i}", "tailored_path": f"{list_path}/{j}",
            })
        for i in al.removed_i:
            note = take_note(o_items[i], None)
            add({
                "id": _cid(list_path, str(i), _norm_text(o_items[i])),
                "path": f"{list_path}/{i}", "section": section,
                "before": o_items[i], "after": None, "kind": "removed",
                "type": _classify(list_path, note.get("type")),
                "rationale": note.get("rationale", ""), "trigger": note.get("trigger", ""),
                "decision": "pending", "list_path": list_path,
                "orig_index": i, "new_index": None,
                "orig_path": f"{list_path}/{i}", "tailored_path": None,
            })
        for j in al.added_j:
            note = take_note(None, t_items[j])
            add({
                "id": _cid(list_path, "+", str(j), _norm_text(t_items[j])),
                "path": f"{list_path}/+{j}", "section": section,
                "before": None, "after": t_items[j], "kind": "added",
                "type": _classify(list_path, note.get("type")),
                "rationale": note.get("rationale", ""), "trigger": note.get("trigger", ""),
                "decision": "pending", "list_path": list_path,
                "orig_index": None, "new_index": j,
                "orig_path": None, "tailored_path": f"{list_path}/{j}",
            })

    # ── summary ──────────────────────────────────────────────
    if (original.summary or "") != (tailored.summary or ""):
        note = take_note(original.summary, tailored.summary)
        add({
            "id": _cid("summary", _norm_text(original.summary)),
            "path": "summary", "section": "summary",
            "before": original.summary, "after": tailored.summary,
            "kind": "modified" if (original.summary and tailored.summary)
                    else ("removed" if original.summary else "added"),
            "type": _classify("summary", note.get("type")),
            "rationale": note.get("rationale", ""), "trigger": note.get("trigger", ""),
            "decision": "pending", "list_path": None,
        })

    # ── entry arrays ─────────────────────────────────────────
    for array, (section, keyfn) in _ENTRY_ARRAYS.items():
        o_ents, t_ents = list(getattr(original, array)), list(getattr(tailored, array))
        if not o_ents and not t_ents:
            continue
        al = _align_list([keyfn(e) for e in o_ents], [keyfn(e) for e in t_ents])

        if al.is_reorder:
            emit_reorder(array, section, [keyfn(e) for e in o_ents], [keyfn(e) for e in t_ents],
                         al.order, al.removed_i, "Entries re-ordered within this section.")

        for i, j in al.pairs:
            so, lo = _entry_fields(array, o_ents[i])
            st, lt = _entry_fields(array, t_ents[j])
            for sub in set(so) | set(st):
                before, after = so.get(sub), st.get(sub)
                if (before or "") == (after or ""):
                    continue
                path = f"{array}/{i}/{sub}"
                note = take_note(before, after)
                change = {
                    "id": _cid(path, _norm_text(before)),
                    "path": path, "section": section,
                    "before": before, "after": after,
                    "kind": "modified" if (sub in so and sub in st)
                            else ("removed" if sub in so else "added"),
                    "type": _classify(path, note.get("type")),
                    "rationale": note.get("rationale", ""), "trigger": note.get("trigger", ""),
                    "decision": "pending", "list_path": None,
                    "entry_index": i, "entry_new_index": j, "entry_field": sub,
                    "orig_path": path, "tailored_path": f"{array}/{j}/{sub}",
                }
                if sub in _JOINED_FIELDS:
                    # Keep the real list alongside the joined display string.
                    change["before_value"] = _raw_field(array, o_ents[i], sub)
                    change["after_value"] = _raw_field(array, t_ents[j], sub)
                add(change)
            for sub in set(lo) | set(lt):
                diff_list(f"{array}/{i}/{sub}", section, lo.get(sub, []), lt.get(sub, []))

        for i in al.removed_i:
            add({
                "id": _cid(array, str(i), ":entry"),
                "path": f"{array}/{i}", "section": section,
                "before": _entry_summary(array, o_ents[i]), "after": None,
                "kind": "removed", "type": _classify(array, None),
                "rationale": "This whole entry was dropped.", "trigger": "",
                "decision": "pending", "list_path": None,
                "entry_index": i, "entry_new_index": None, "entry_whole": True,
                "orig_path": f"{array}/{i}", "tailored_path": None,
            })
        for j in al.added_j:
            add({
                "id": _cid(array, "+", str(j), ":entry"),
                "path": f"{array}/+{j}", "section": section,
                "before": None, "after": _entry_summary(array, t_ents[j]),
                "kind": "added", "type": _classify(array, None),
                "rationale": "This whole entry is new.", "trigger": "",
                "decision": "pending", "list_path": None,
                "entry_index": None, "entry_new_index": j, "entry_whole": True,
                "entry": t_ents[j].model_dump(),
                "orig_path": None, "tailored_path": f"{array}/{j}",
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
    """Pre-alignment behaviour: clone tailored, revert every rejected leaf. Still
    used for tailor states stored before content-aligned diffing shipped."""
    import copy
    eff = copy.deepcopy(state.get("tailored") or {})
    for c in state.get("changes") or []:
        if c.get("decision") == "rejected":
            _set_path(eff, c["path"], _get_path(state.get("original") or {}, c["path"]))
    return eff


def _apply_list(o_items: list, cs: list) -> list:
    """Rebuild one bullet list from the ORIGINAL items plus its changes.

    Rejected → original text/order; pending and accepted → tailored, matching the
    rule every other change type follows.
    """
    reorder = next((c for c in cs if c["kind"] == "reordered"), None)
    by_oi = {c["orig_index"]: c for c in cs
             if c.get("orig_index") is not None and c["kind"] != "reordered"}

    text, alive = {}, []
    for i, item in enumerate(o_items):
        c = by_oi.get(i)
        if c and c["kind"] == "removed":
            if c.get("decision") != "rejected":
                continue                                   # removal stands
            text[i] = item                                 # user kept it
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
    if reorder is None or reorder.get("decision") == "rejected":
        for slot in sorted(added):
            c = added[slot]
            if c.get("decision") != "rejected":
                out.insert(min(slot, len(out)), c["after"])
    return out


def effective_resume(state: dict) -> dict:
    """The résumé the user has actually approved: tailored content with every
    REJECTED change reverted. Pending and accepted keep the tailored value.

    Sections are rebuilt from the original so a rejected re-ordering restores the
    original ORDER while independently accepted rewordings survive — at both the
    entry level (projects, roles) and the bullet level.
    """
    import copy

    changes = state.get("changes") or []
    if not any(c.get("list_path") or c.get("entry_index") is not None
               or c.get("entry_new_index") is not None for c in changes):
        return _legacy_effective(state)

    eff = copy.deepcopy(state.get("tailored") or {})
    orig = state.get("original") or {}

    # Top-level scalars (summary).
    for c in changes:
        if "/" not in c["path"] and c["kind"] != "reordered" and c.get("decision") == "rejected":
            _set_path(eff, c["path"], _get_path(orig, c["path"]))

    for array in _ENTRY_ARRAYS:
        mine = [c for c in changes if c["path"] == array or c["path"].startswith(f"{array}/")]
        if not mine:
            continue
        o_entries = _get_path(orig, array) or []
        reorder = next((c for c in mine if c["kind"] == "reordered" and c["path"] == array), None)
        removed = {c["entry_index"]: c for c in mine
                   if c.get("entry_whole") and c["kind"] == "removed"}
        added = {c["entry_new_index"]: c for c in mine
                 if c.get("entry_whole") and c["kind"] == "added"}

        built, alive = {}, []
        for i, entry in enumerate(o_entries):
            rem = removed.get(i)
            if rem is not None and rem.get("decision") != "rejected":
                continue                                   # entry dropped
            e = copy.deepcopy(entry)
            for c in mine:
                if c.get("entry_index") != i or c.get("entry_whole"):
                    continue
                sub = c["path"][len(f"{array}/{i}/"):] if c["path"].startswith(f"{array}/{i}/") else None
                if not sub:
                    continue
                if c.get("list_path"):
                    continue                               # handled below
                if c.get("decision") == "rejected":
                    continue                               # deepcopy already holds the original
                value = c["after_value"] if "after_value" in c else c["after"]
                _set_path(e, sub, value)
            # Bullet lists inside this entry.
            list_paths = {c["list_path"] for c in mine
                          if c.get("list_path") and c["list_path"].startswith(f"{array}/{i}/")}
            for lp in list_paths:
                sub = lp[len(f"{array}/{i}/"):]
                cs = [c for c in mine if c.get("list_path") == lp]
                _set_path(e, sub, _apply_list(_get_path(entry, sub) or [], cs))
            built[i] = e
            alive.append(i)

        if reorder is not None and reorder.get("decision") != "rejected":
            order = [oi for oi in (reorder.get("order") or []) if oi is None or oi in built]
        else:
            order = sorted(alive)

        out = []
        for slot, oi in enumerate(order):
            if oi is None:
                c = added.get(slot)
                if c and c.get("decision") != "rejected" and c.get("entry"):
                    out.append(copy.deepcopy(c["entry"]))
            else:
                out.append(built[oi])
        if reorder is None or reorder.get("decision") == "rejected":
            for slot in sorted(added):
                c = added[slot]
                if c.get("decision") != "rejected" and c.get("entry"):
                    out.insert(min(slot, len(out)), copy.deepcopy(c["entry"]))
        _set_path(eff, array, out)

    return eff
