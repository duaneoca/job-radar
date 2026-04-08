"""
Criteria router — manage job matching criteria for the AI reviewer.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/criteria", tags=["criteria"])


@router.get("", response_model=list[schemas.CriteriaOut])
def list_criteria(db: Session = Depends(get_db)):
    """List all criteria profiles."""
    return db.query(models.Criteria).order_by(models.Criteria.created_at).all()


@router.post("", response_model=schemas.CriteriaOut, status_code=status.HTTP_201_CREATED)
def create_criteria(payload: schemas.CriteriaCreate, db: Session = Depends(get_db)):
    """Create a new criteria profile."""
    criteria = models.Criteria(**payload.model_dump())
    db.add(criteria)
    db.commit()
    db.refresh(criteria)
    return criteria


@router.get("/active", response_model=schemas.CriteriaOut)
def get_active_criteria(db: Session = Depends(get_db)):
    """Get the currently active criteria profile."""
    criteria = db.query(models.Criteria).filter(models.Criteria.is_active == True).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="No active criteria found. Create one first.")
    return criteria


@router.get("/{criteria_id}", response_model=schemas.CriteriaOut)
def get_criteria(criteria_id: UUID, db: Session = Depends(get_db)):
    criteria = db.query(models.Criteria).filter(models.Criteria.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")
    return criteria


@router.put("/{criteria_id}", response_model=schemas.CriteriaOut)
def update_criteria(
    criteria_id: UUID,
    payload: schemas.CriteriaUpdate,
    db: Session = Depends(get_db),
):
    criteria = db.query(models.Criteria).filter(models.Criteria.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(criteria, field, value)

    db.commit()
    db.refresh(criteria)
    return criteria


@router.post("/{criteria_id}/activate", response_model=schemas.CriteriaOut)
def activate_criteria(criteria_id: UUID, db: Session = Depends(get_db)):
    """Set a criteria profile as the active one (deactivates all others)."""
    # Deactivate all
    db.query(models.Criteria).update({"is_active": False})

    criteria = db.query(models.Criteria).filter(models.Criteria.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")

    criteria.is_active = True
    db.commit()
    db.refresh(criteria)
    return criteria


@router.delete("/{criteria_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_criteria(criteria_id: UUID, db: Session = Depends(get_db)):
    criteria = db.query(models.Criteria).filter(models.Criteria.id == criteria_id).first()
    if not criteria:
        raise HTTPException(status_code=404, detail="Criteria not found")
    db.delete(criteria)
    db.commit()
