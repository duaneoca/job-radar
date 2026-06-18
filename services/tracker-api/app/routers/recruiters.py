"""
Recruiter CRM router — track recruiters you've connected with, link them to the
jobs they sourced, and seed entries from inbox recruiter_outreach emails.

Security note (C2): inbox sender strings are agent-derived and therefore
attacker-controlled. Suggestions only ever surface a parsed display name + email
address (both length-capped); everything else is user-entered. Responses are JSON
and rendered by a React client that escapes text by default — but the frontend
must still route linkedin_url through its safeHref guard before using it as a link.
"""

from email.utils import parseaddr
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/recruiters", tags=["recruiters"])

_NAME_MAX = 200
_EMAIL_MAX = 255


def _get_recruiter_or_404(recruiter_id: UUID, user: models.User, db: Session) -> models.Recruiter:
    rec = (
        db.query(models.Recruiter)
        .options(joinedload(models.Recruiter.jobs).joinedload(models.UserJobReview.job))
        .filter(models.Recruiter.id == recruiter_id, models.Recruiter.user_id == user.id)
        .first()
    )
    if not rec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recruiter not found")
    return rec


def _to_out(rec: models.Recruiter) -> schemas.RecruiterOut:
    """Serialize, flattening linked jobs (job title/company live on the Job row).

    Built from the base fields explicitly so the ORM ``jobs`` relationship
    (UserJobReview rows) isn't auto-coerced into RecruiterJobBrief — those fields
    live on the related Job, not the review."""
    jobs = [
        schemas.RecruiterJobBrief(
            id=r.id, title=r.job.title, company=r.job.company, status=r.status
        )
        for r in rec.jobs
    ]
    base = schemas.RecruiterBase.model_validate(rec).model_dump()
    return schemas.RecruiterOut(
        id=rec.id, created_at=rec.created_at, updated_at=rec.updated_at, jobs=jobs, **base
    )


# ── Suggestions from inbox (LITERAL route — must precede /{recruiter_id}) ──────

@router.get("/suggestions", response_model=list[schemas.RecruiterSuggestion])
def recruiter_suggestions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Distinct senders of recruiter_outreach inbox emails that aren't already
    tracked, most-frequent first. The user confirms one to create a recruiter."""
    rows = (
        db.query(models.InboxEmail.sender)
        .filter(
            models.InboxEmail.user_id == current_user.id,
            models.InboxEmail.category == models.EmailCategory.RECRUITER_OUTREACH,
        )
        .all()
    )

    # Emails already tracked — skip those.
    existing = {
        e.lower()
        for (e,) in db.query(models.Recruiter.email)
        .filter(models.Recruiter.user_id == current_user.id, models.Recruiter.email.isnot(None))
        .all()
        if e
    }

    # Group parsed senders by email address.
    by_email: dict[str, dict] = {}
    for (sender,) in rows:
        name, email = parseaddr(sender or "")
        email = (email or "").strip().lower()[:_EMAIL_MAX]
        if not email or "@" not in email or email in existing:
            continue
        name = (name or "").strip()[:_NAME_MAX] or email.split("@")[0]
        slot = by_email.setdefault(email, {"name": name, "email": email, "count": 0})
        slot["count"] += 1
        # Prefer a real display name over the email-local fallback.
        if name and "@" not in slot["name"] and len(name) > len(slot["name"]):
            slot["name"] = name

    suggestions = sorted(by_email.values(), key=lambda s: s["count"], reverse=True)
    return [
        schemas.RecruiterSuggestion(name=s["name"], email=s["email"], email_count=s["count"])
        for s in suggestions
    ]


# ── CRUD ──────────────────────────────────────────────────────

@router.get("", response_model=list[schemas.RecruiterOut])
def list_recruiters(
    search: Optional[str] = Query(None, description="Match name, employer, or email"),
    status_filter: Optional[schemas.RecruiterStatus] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = (
        db.query(models.Recruiter)
        .options(joinedload(models.Recruiter.jobs).joinedload(models.UserJobReview.job))
        .filter(models.Recruiter.user_id == current_user.id)
    )
    if status_filter:
        q = q.filter(models.Recruiter.status == status_filter)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            models.Recruiter.name.ilike(term)
            | models.Recruiter.employer.ilike(term)
            | models.Recruiter.email.ilike(term)
        )
    recruiters = q.order_by(models.Recruiter.name).all()
    return [_to_out(r) for r in recruiters]


@router.post("", response_model=schemas.RecruiterOut, status_code=status.HTTP_201_CREATED)
def create_recruiter(
    payload: schemas.RecruiterCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    data = payload.model_dump()
    if data.get("email"):
        data["email"] = str(data["email"])
    rec = models.Recruiter(user_id=current_user.id, **data)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return _to_out(rec)


@router.get("/{recruiter_id}", response_model=schemas.RecruiterOut)
def get_recruiter(
    recruiter_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _to_out(_get_recruiter_or_404(recruiter_id, current_user, db))


@router.patch("/{recruiter_id}", response_model=schemas.RecruiterOut)
def update_recruiter(
    recruiter_id: UUID,
    payload: schemas.RecruiterUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rec = _get_recruiter_or_404(recruiter_id, current_user, db)
    updates = payload.model_dump(exclude_unset=True)
    if "email" in updates and updates["email"] is not None:
        updates["email"] = str(updates["email"])
    for k, v in updates.items():
        setattr(rec, k, v)
    db.commit()
    db.refresh(rec)
    return _to_out(rec)


@router.delete("/{recruiter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recruiter(
    recruiter_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rec = _get_recruiter_or_404(recruiter_id, current_user, db)
    db.delete(rec)   # FK SET NULL unlinks any jobs; the jobs themselves survive
    db.commit()


# ── Job links ─────────────────────────────────────────────────

@router.post("/{recruiter_id}/jobs", response_model=schemas.RecruiterOut)
def link_job(
    recruiter_id: UUID,
    payload: schemas.RecruiterJobLink,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rec = _get_recruiter_or_404(recruiter_id, current_user, db)
    review = (
        db.query(models.UserJobReview)
        .filter(
            models.UserJobReview.id == payload.review_id,
            models.UserJobReview.user_id == current_user.id,
        )
        .first()
    )
    if not review:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    review.recruiter_id = rec.id
    db.commit()
    db.refresh(rec)
    return _to_out(rec)


@router.delete("/{recruiter_id}/jobs/{review_id}", response_model=schemas.RecruiterOut)
def unlink_job(
    recruiter_id: UUID,
    review_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rec = _get_recruiter_or_404(recruiter_id, current_user, db)
    review = (
        db.query(models.UserJobReview)
        .filter(
            models.UserJobReview.id == review_id,
            models.UserJobReview.user_id == current_user.id,
            models.UserJobReview.recruiter_id == rec.id,
        )
        .first()
    )
    if review:
        review.recruiter_id = None
        db.commit()
    db.refresh(rec)
    return _to_out(rec)
