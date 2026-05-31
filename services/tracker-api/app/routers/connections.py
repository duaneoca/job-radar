"""
LinkedIn connections router — import CSV, list, match against a company.
"""

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/connections", tags=["connections"])

# LinkedIn export column names vary slightly by account region/version.
# We try a handful of known variants.
_FIRST = ("First Name", "first_name", "FirstName")
_LAST  = ("Last Name",  "last_name",  "LastName")
_EMAIL = ("Email Address", "email", "Email")
_COMPANY  = ("Company",  "company")
_POSITION = ("Position", "position", "Title")
_CONNECTED = ("Connected On", "connected_on", "ConnectedOn")


def _pick(row: dict, *keys: str) -> Optional[str]:
    for k in keys:
        if k in row and row[k]:
            return row[k].strip() or None
    return None


@router.post("/import", status_code=status.HTTP_200_OK)
async def import_connections(
    file: UploadFile = File(...),
    replace: bool = Query(False, description="Delete existing connections before import"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Upload a LinkedIn Connections.csv export.
    Set replace=true to wipe the existing list first.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")   # strip BOM if present
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    # LinkedIn exports start with a "Notes: …" preamble line before the real header.
    # Skip lines until we find one containing recognisable column names.
    lines = text.splitlines()
    header_idx = 0
    for i, line in enumerate(lines):
        if "First Name" in line or "first_name" in line or "FirstName" in line:
            header_idx = i
            break
    text = "\n".join(lines[header_idx:])

    reader = csv.DictReader(io.StringIO(text))

    if replace:
        db.query(models.LinkedInConnection).filter(
            models.LinkedInConnection.user_id == current_user.id
        ).delete()

    inserted = 0
    skipped = 0
    for row in reader:
        first = _pick(row, *_FIRST)
        last  = _pick(row, *_LAST)
        company = _pick(row, *_COMPANY)

        if not (first or last or company):
            skipped += 1
            continue

        conn = models.LinkedInConnection(
            user_id=current_user.id,
            first_name=first,
            last_name=last,
            email=_pick(row, *_EMAIL),
            company=company,
            position=_pick(row, *_POSITION),
            connected_on=_pick(row, *_CONNECTED),
        )
        db.add(conn)
        inserted += 1

    db.commit()
    return {"imported": inserted, "skipped": skipped}


@router.get("", response_model=list[schemas.LinkedInConnectionOut])
def list_connections(
    company: Optional[str] = Query(None, description="Filter by company (partial match)"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.LinkedInConnection).filter(
        models.LinkedInConnection.user_id == current_user.id
    )
    if company:
        q = q.filter(models.LinkedInConnection.company.ilike(f"%{company}%"))
    return q.order_by(models.LinkedInConnection.company).all()


@router.get("/match")
def match_company(
    company: str = Query(..., description="Company name to match against your connections"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Return connections that work (or worked) at the given company.
    Used by the job detail page to surface 'you know someone here'.
    """
    matches = (
        db.query(models.LinkedInConnection)
        .filter(
            models.LinkedInConnection.user_id == current_user.id,
            models.LinkedInConnection.company.ilike(f"%{company}%"),
        )
        .all()
    )
    return {
        "company": company,
        "matches": [schemas.LinkedInConnectionOut.model_validate(m) for m in matches],
        "has_contact": len(matches) > 0,
    }


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_connections(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete all connections for the current user."""
    db.query(models.LinkedInConnection).filter(
        models.LinkedInConnection.user_id == current_user.id
    ).delete()
    db.commit()
