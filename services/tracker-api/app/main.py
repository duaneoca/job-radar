"""
JobRadar — Tracker API
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.routers import admin, auth, connections, criteria, generate, jobs, keys, profile
from app.security import hash_password

logger = logging.getLogger(__name__)

app = FastAPI(
    title="JobRadar Tracker API",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],   # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(jobs.router)
app.include_router(criteria.router)
app.include_router(profile.router)
app.include_router(connections.router)
app.include_router(keys.router)
app.include_router(generate.router)


@app.on_event("startup")
def seed_admin():
    """
    If ADMIN_EMAIL is set and no users exist yet, create the bootstrap
    admin account with ADMIN_PASSWORD (default: changeme123).
    The admin is forced to change their password on first login.
    """
    if not settings.admin_email:
        return

    db: Session = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return   # users already exist, skip seeding

        admin_user = User(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            full_name="Admin",
            is_approved=True,
            is_admin=True,
            must_change_password=True,
        )
        db.add(admin_user)
        db.commit()
        logger.info("Bootstrap admin created: %s", settings.admin_email)
    finally:
        db.close()


@app.get("/", tags=["health"])
def root():
    return {"service": "jobradar-tracker-api", "version": "0.2.0", "status": "ok"}


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
