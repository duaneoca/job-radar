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

    # Strip markdown code fences if the model added them.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

    try:
        data = json.loads(raw)
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
