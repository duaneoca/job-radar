"""
JobRadar — Tracker API
Entry point for the FastAPI application.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import jobs, criteria

app = FastAPI(
    title="JobRadar Tracker API",
    description=(
        "Central hub for JobRadar. Stores job postings, tracks application status, "
        "manages AI review criteria, and records timeline events."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — in production, lock this down to your actual frontend domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(criteria.router)


@app.get("/", tags=["health"])
def root():
    return {"service": "jobradar-tracker-api", "status": "ok"}


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
