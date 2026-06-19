"""
Recruiter CRM router — track recruiters you've connected with, link them to the
jobs they sourced, and seed entries from inbox recruiter_outreach emails.

Security note (C2): everything sourced from inbox emails is agent-derived and
therefore attacker-controlled — both the parsed sender string AND the agent's
`recruiter_contact` card (signature/body extraction). We sanitize server-side
(length-cap every field, allowlist linkedin_url to http/https, drop markup-y
values) and never auto-create — suggestions are review-and-confirm. The React
client also escapes on render and routes linkedin_url through safeHref.
"""

from email.utils import parseaddr
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/recruiters", tags=["recruiters"])

_NAME_MAX = 200
_EMAIL_MAX = 255
_PHONE_MAX = 50
_FIELD_MAX = 200          # title / employer
_URL_MAX = 500
_COMPANIES_MAX = 20       # cap list length to bound payload


def _cap(v, n: int) -> Optional[str]:
    """Trim a value to a clean, length-capped string, or None."""
    if not isinstance(v, str):
        return None
    s = v.strip()
    return s[:n] if s else None


def _safe_url(v) -> Optional[str]:
    """Allow only absolute http(s) URLs (mirrors the frontend safeHref guard)."""
    s = _cap(v, _URL_MAX)
    if not s:
        return None
    try:
        return s if urlparse(s).scheme in ("http", "https") else None
    except ValueError:
        return None


def _clean_card(card) -> dict:
    """Sanitize the agent's `recruiter_contact` card into our CRM field shape.

    Untrusted input — every field is capped/validated; unknown keys ignored.
    Maps `is_agency` → type and `represents` → companies_represented."""
    if not isinstance(card, dict):
        return {}
    out: dict = {}
    out["name"] = _cap(card.get("name"), _NAME_MAX)
    email = _cap(card.get("email"), _EMAIL_MAX)
    out["email"] = email.lower() if email and "@" in email else None
    out["phone"] = _cap(card.get("phone"), _PHONE_MAX)
    out["title"] = _cap(card.get("title"), _FIELD_MAX)
    out["employer"] = _cap(card.get("employer"), _FIELD_MAX)
    out["linkedin_url"] = _safe_url(card.get("linkedin_url"))

    is_agency = card.get("is_agency")
    out["type"] = "agency" if is_agency is True else "in_house" if is_agency is False else None

    represents = card.get("represents")
    if isinstance(represents, list):
        cleaned = [c for c in (_cap(x, _FIELD_MAX) for x in represents) if c][:_COMPANIES_MAX]
        out["companies_represented"] = cleaned or None
    else:
        out["companies_represented"] = None

    conf = card.get("recruiter_confidence")
    out["recruiter_confidence"] = float(conf) if isinstance(conf, (int, float)) else None
    return {k: v for k, v in out.items() if v is not None}


# Fields that count toward "completeness" when picking the best card for a sender.
_CARD_FIELDS = ("phone", "title", "employer", "linkedin_url", "type", "companies_represented")


def _card_score(card: dict) -> tuple[int, float]:
    """More populated fields wins; confidence breaks ties."""
    filled = sum(1 for f in _CARD_FIELDS if card.get(f))
    return (filled, card.get("recruiter_confidence") or 0.0)


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
    tracked, most-frequent first. Enriched with the agent's `recruiter_contact`
    card (phone/title/employer/linkedin/type/companies) when the agent extracted
    one. The user confirms one to create a recruiter."""
    rows = (
        db.query(models.InboxEmail.sender, models.InboxEmail.raw_extracted_json)
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

    # Group by email address, merging the best card across this sender's emails.
    by_email: dict[str, dict] = {}
    for sender, raw in rows:
        card = _clean_card(raw.get("recruiter_contact")) if isinstance(raw, dict) else {}

        parsed_name, parsed_email = parseaddr(sender or "")
        # Prefer the card's reply-to address, fall back to the parsed sender.
        email = (card.get("email") or parsed_email or "").strip().lower()[:_EMAIL_MAX]
        if not email or "@" not in email or email in existing:
            continue

        name = (card.get("name") or (parsed_name or "").strip() or email.split("@")[0])[:_NAME_MAX]
        slot = by_email.get(email)
        if slot is None:
            slot = {"name": name, "email": email, "count": 0, "card": {}}
            by_email[email] = slot
        slot["count"] += 1
        if name and "@" not in slot["name"] and len(name) > len(slot["name"]):
            slot["name"] = name
        # Keep the more complete card (more fields wins; ties broken by confidence).
        if _card_score(card) > _card_score(slot["card"]):
            slot["card"] = card

    suggestions = sorted(by_email.values(), key=lambda s: s["count"], reverse=True)
    return [
        schemas.RecruiterSuggestion(
            name=s["name"], email=s["email"], email_count=s["count"],
            phone=s["card"].get("phone"),
            title=s["card"].get("title"),
            employer=s["card"].get("employer"),
            linkedin_url=s["card"].get("linkedin_url"),
            type=s["card"].get("type"),
            companies_represented=s["card"].get("companies_represented"),
            recruiter_confidence=s["card"].get("recruiter_confidence"),
        )
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
