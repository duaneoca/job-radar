## Output Format
Return ONLY a valid JSON object — no preamble, no explanation, no markdown fences. Start your response with `{` and end with `}`. The JSON must match this exact structure:

{
  "score": 7.0,
  "skills_rank": 8,
  "experience_rank": 7,
  "location_rank": 10,
  "education_rank": 5,
  "salary_rank": 6,
  "summary": "Your [specific skill/background] aligns well with this role's focus on [X]. [One honest gap or concern].",
  "pros": ["strength 1", "strength 2", "strength 3"],
  "cons": ["weakness 1", "weakness 2"],
  "recommended": true
}

Field rules:
- `score` — float, the evenly weighted average of the five ranks rounded to one decimal
- `*_rank` — integer 1–10 for each dimension per the rubric above
- `summary` — 1-2 sentences, plain English, no jargon; addressed to the candidate in second person (e.g. "Your experience in X..."); describe their fit from their own perspective, not the employer's
- `pros` — 2-4 specific strengths, grounded in the job description and profile
- `cons` — 1-3 honest gaps or concerns
- `recommended` — true if score >= 6.0, false otherwise
