"""One-off: merge a user's duplicate job reviews.

    python -m app.merge_duplicates --email you@example.com            # preview
    python -m app.merge_duplicates --email you@example.com --apply    # delete

Two kinds of copy, both from before ingest learned to recognise them:
  * JSearch exact-link copies — JSearch re-issued its own id for the same
    posting most days, so every scrape added it again;
  * aggregator re-listings — same company, title and city (`jobs.match_key`).

For each group ONE review is kept and only untouched aggregator copies are
deleted. "Touched" means anything a person did: a status past new/reviewed/
expired (dismissed included — that is a verdict), notes, contact notes, a
recruiter, research, application answers, interview prep or a tailored résumé.
  * nobody touched any → keep the most recently scraped (freshest data);
  * one was touched → keep that one;
  * two or more were touched → leave the whole group alone and list it.
Company-board and manual jobs are never deleted, only kept. A Job row left with
no reviews from anyone is removed too. Deleting a review cascades its timeline.
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.dedup import AGGREGATOR_SOURCES

_UNTOUCHED_STATUSES = {"new", "reviewed", "expired"}
_WORK_FIELDS = ("notes", "contact_notes", "recruiter_id", "research_summary",
                "application_answers", "interview_questions", "resume_tailor")


@dataclass
class Row:
    review_id: object
    job_id: object
    status: str
    source: str
    url: str
    match_key: str | None
    scraped: datetime | None
    title: str = ""
    company: str = ""
    work: dict = field(default_factory=dict)

    @property
    def touched(self) -> bool:
        return self.status not in _UNTOUCHED_STATUSES or any(self.work.get(f) for f in _WORK_FIELDS)

    @property
    def deletable(self) -> bool:
        return self.source in AGGREGATOR_SOURCES and not self.touched


@dataclass
class Plan:
    delete: list = field(default_factory=list)                 # Row
    kept: dict = field(default_factory=dict)                   # review_id → Row kept for a group
    skipped: list = field(default_factory=list)                # (kind, label, [Row]) left alone
    by_kind: dict = field(default_factory=lambda: defaultdict(int))


def _choose(group: list) -> Row | None:
    """The row to keep, or None to leave the group alone."""
    touched = [r for r in group if r.touched]
    if len(touched) > 1:
        return None
    if touched:
        return touched[0]
    # Prefer a company-board or manual job (the better link), then the freshest.
    return max(group, key=lambda r: (r.source not in AGGREGATOR_SOURCES,
                                     r.scraped or datetime.min))


def plan(rows: list) -> Plan:
    p = Plan()
    gone: set = set()
    reported: set = set()

    def merge(kind: str, groups: dict) -> None:
        for label, members in groups.items():
            members = [r for r in members if r.review_id not in gone]
            if len(members) < 2:
                continue
            keep = _choose(members)
            if keep is None:
                # The same copies often share a link AND a key; report them once.
                ids = frozenset(r.review_id for r in members)
                if ids not in reported:
                    reported.add(ids)
                    p.skipped.append((kind, label, members))
                continue
            p.kept[keep.review_id] = keep
            for r in members:
                if r is not keep and r.deletable:
                    p.delete.append(r)
                    p.by_kind[kind] += 1
                    gone.add(r.review_id)

    by_url = defaultdict(list)
    for r in rows:
        if r.source == "jsearch" and r.url:
            by_url[r.url].append(r)
    merge("same link (JSearch)", by_url)

    by_key = defaultdict(list)
    for r in rows:
        if r.match_key and r.review_id not in gone:
            by_key[r.match_key].append(r)
    merge("same company+title+city", by_key)
    return p


def _load(db, email: str) -> list:
    from app import models
    user = db.query(models.User).filter(models.User.email.ilike(email)).one()
    q = (db.query(models.UserJobReview, models.Job)
         .join(models.Job, models.Job.id == models.UserJobReview.job_id)
         .filter(models.UserJobReview.user_id == user.id))
    out = []
    for rev, job in q:
        out.append(Row(
            review_id=rev.id, job_id=job.id,
            status=getattr(rev.status, "value", rev.status), source=job.source, url=job.url,
            match_key=job.match_key, scraped=job.date_scraped,
            title=job.title, company=job.company,
            work={f: getattr(rev, f, None) for f in _WORK_FIELDS},
        ))
    return out


def run(db, email: str, apply: bool) -> Plan:
    from app import models
    rows = _load(db, email)
    p = plan(rows)
    print(f"{len(rows)} reviews for {email}; {len(p.delete)} untouched copies to remove "
          f"({', '.join(f'{k}: {n}' for k, n in p.by_kind.items()) or 'none'}).")
    print(f"{len(p.skipped)} group(s) left alone because more than one copy has your work on it.")
    for kind, label, members in p.skipped[:20]:
        print(f"  [{kind}] {members[0].company} — {members[0].title}: "
              + ", ".join(f"{m.status}" for m in members))
    if not apply:
        print("Preview only — nothing changed. Re-run with --apply to delete.")
        return p

    ids = [r.review_id for r in p.delete]
    jobs = {r.job_id for r in p.delete}
    for chunk in range(0, len(ids), 500):
        (db.query(models.UserJobReview)
         .filter(models.UserJobReview.id.in_(ids[chunk:chunk + 500]))
         .delete(synchronize_session=False))
    db.flush()
    # A shared Job that no user reviews any more goes too (the nightly cleanup
    # would, but not until tonight).
    orphans = [j for j in jobs
               if not db.query(models.UserJobReview.id).filter(models.UserJobReview.job_id == j).first()]
    if orphans:
        db.query(models.Job).filter(models.Job.id.in_(orphans)).delete(synchronize_session=False)
    db.commit()
    print(f"Deleted {len(ids)} reviews and {len(orphans)} orphaned jobs.")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--email", required=True)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: preview)")
    args = ap.parse_args(argv)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        run(db, args.email, args.apply)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
