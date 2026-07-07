#!/usr/bin/env python3
"""Reconfigure a demo/showcase account with a coherent, narrow persona.

Built for capturing marketing screenshots. It targets ONE existing user
(default: the staging test account), wipes its jobs/reviews, and installs a
"Nuclear Engineer, Newport News VA" persona (profile + criteria) so a real
scrape+score of that narrow title produces realistic, flattering results at
bounded token cost.

  • Persona résumé matches the title → real AI scores come out high, not low.
  • Scoped strictly to the target user — deletes only its reviews, then any
    jobs that become orphaned (referenced by zero users). Safe on shared DBs.
  • Dry-run by default; pass --confirm to actually write.

It does NOT scrape or score — that's a separate, deliberate step so token spend
stays visible:
    (after this runs) POST /admin/trigger-scrape  as the demo user, or run the
    scraper's scrape_user task, then let ai-reviewer score the narrow result set.

Run against the STAGING DB (needs the tracker-api package + a DATABASE_URL):
    DATABASE_URL=postgresql://…staging… \
      python scripts/seed_demo.py --email testuser@duanesworld.org --confirm

Requires the tracker-api app importable (run from services/tracker-api or set
PYTHONPATH there).
"""
from __future__ import annotations

import argparse
import sys

from app.database import SessionLocal          # noqa: E402  (tracker-api app)
from app import models                          # noqa: E402

DEMO_DISPLAY_NAME = "Alex Rivera"
DEMO_LOCATION = "Newport News, VA"

CRITERIA = dict(
    name="default",
    is_active=True,
    job_titles=["Nuclear Engineer"],
    search_locations=["Newport News, VA"],
    work_style="onsite",
    home_city="Newport News, VA",
    max_commute_miles=30,
    min_salary=95_000,
    target_companies=["Huntington Ingalls Industries", "Newport News Shipbuilding", "Dominion Energy"],
)

RESUME_TEXT = """\
ALEX RIVERA — Nuclear Engineer
Newport News, VA · alex.rivera@example.com

SUMMARY
Nuclear engineer with 8+ years across naval nuclear propulsion and commercial
reactor systems. Experienced supporting aircraft-carrier reactor-plant design,
testing, and safety analysis at Newport News Shipbuilding. Strong in
thermal-hydraulics, reactor safety analysis, radiation protection, and NRC /
Naval Reactors regulatory compliance.

EXPERIENCE
Senior Nuclear Engineer — Newport News Shipbuilding (Huntington Ingalls)   2019–present
 · Led thermal-hydraulic analyses (RELAP5, GOTHIC) for CVN reactor-plant systems,
   resolving a coolant-flow margin issue that unblocked a test milestone.
 · Owned safety-analysis documentation to ASME Section III and NAVSEA 08 standards.
 · Coordinated RADCON reviews and reduced survey turnaround by 20%.

Nuclear Engineer — Dominion Energy (Surry Power Station)                    2016–2019
 · Performed reactor-core and safety-system analysis under 10 CFR 50 / NRC guidance.
 · Supported outage planning and NQA-1 quality assurance for safety-related work.

EDUCATION
M.S. Nuclear Engineering — North Carolina State University
B.S. Nuclear Engineering — Virginia Tech

SKILLS
Reactor systems · Thermal-hydraulics (RELAP5, GOTHIC) · Reactor safety analysis
Radiation protection / RADCON · ASME Section III · 10 CFR / NRC regulatory
Naval Reactors (NAVSEA 08) · Systems engineering · MATLAB · QA (NQA-1)
"""

PROFILE = dict(
    name="default",
    is_active=True,
    full_name=DEMO_DISPLAY_NAME,
    location=DEMO_LOCATION,
    resume_text=RESUME_TEXT,
    summary=(
        "Nuclear engineer (8+ yrs) in naval nuclear propulsion and commercial "
        "reactor systems; reactor-plant design, safety analysis, and RADCON."
    ),
    skills=[
        "Reactor systems", "Thermal-hydraulics", "RELAP5", "GOTHIC",
        "Reactor safety analysis", "Radiation protection / RADCON",
        "ASME Section III", "10 CFR / NRC regulatory", "Naval Reactors (NAVSEA 08)",
        "Systems engineering", "MATLAB", "Quality assurance (NQA-1)",
    ],
    education="M.S. Nuclear Engineering, NC State · B.S. Nuclear Engineering, Virginia Tech",
    desired_salary=120_000,
    commute_preference="Onsite in the Newport News / Hampton Roads area",
    career_stories=[
        {
            "title": "Resolved a coolant-flow margin issue",
            "content": (
                "A reactor-plant test milestone was blocked by a thermal-hydraulic "
                "margin finding. I built a RELAP5 model of the affected loop, "
                "identified a conservative assumption in the original analysis, and "
                "re-ran the case with validated inputs — recovering margin and "
                "clearing the milestone without a hardware change."
            ),
        },
        {
            "title": "Cut RADCON survey turnaround by 20%",
            "content": (
                "Radiation-survey reviews were a bottleneck during outages. I "
                "reworked the review checklist and coordination flow with the RADCON "
                "team, cutting turnaround ~20% while keeping full 10 CFR compliance."
            ),
        },
        {
            "title": "Mentored junior engineers on safety analysis",
            "content": (
                "I ran a monthly working session walking newer engineers through "
                "ASME Section III safety-analysis methods and NQA-1 documentation, "
                "which shortened onboarding and improved first-pass review quality."
            ),
        },
    ],
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", default="testuser@duanesworld.org", help="target user email")
    ap.add_argument("--confirm", action="store_true", help="actually write (default is dry-run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.email == args.email).first()
        if not user:
            print(f"ERROR: no user with email {args.email!r}", file=sys.stderr)
            return 2

        review_count = db.query(models.UserJobReview).filter_by(user_id=user.id).count()
        print(f"Target user: {user.email}  (id={user.id})")
        print(f"  reviews to delete: {review_count}")
        print(f"  display name → {DEMO_DISPLAY_NAME!r}")
        print(f"  criteria → Nuclear Engineer @ Newport News, VA")
        print(f"  profile  → nuclear-engineer persona ({len(PROFILE['skills'])} skills, "
              f"{len(PROFILE['career_stories'])} career stories)")

        if not args.confirm:
            print("\nDRY RUN — nothing written. Re-run with --confirm to apply.")
            return 0

        # 1. Wipe this user's reviews (timeline_events cascade via DB FK), then any
        #    Job now referenced by zero users.
        db.query(models.UserJobReview).filter_by(user_id=user.id).delete(synchronize_session=False)
        db.flush()
        orphan_q = db.query(models.Job).filter(
            ~models.Job.id.in_(db.query(models.UserJobReview.job_id))
        )
        orphans = orphan_q.delete(synchronize_session=False)
        print(f"  deleted {review_count} reviews, {orphans} orphaned jobs")

        # 2. Display name
        user.full_name = DEMO_DISPLAY_NAME

        # 3. Criteria (upsert the active one)
        crit = db.query(models.Criteria).filter_by(user_id=user.id, is_active=True).first()
        if not crit:
            crit = models.Criteria(user_id=user.id)
            db.add(crit)
        for k, v in CRITERIA.items():
            setattr(crit, k, v)

        # 4. Profile (upsert the active one)
        prof = db.query(models.Profile).filter_by(user_id=user.id, is_active=True).first()
        if not prof:
            prof = models.Profile(user_id=user.id)
            db.add(prof)
        for k, v in PROFILE.items():
            setattr(prof, k, v)
        prof.resume_structured_stale = True  # re-parse on next tailor

        db.commit()
        print("\nDone. Next: trigger a scrape for this user (POST /admin/trigger-scrape "
              "or the scraper scrape_user task) and let ai-reviewer score the results.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
