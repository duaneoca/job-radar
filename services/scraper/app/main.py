"""
Scraper Service — Phase 2
-------------------------
Celery worker that scrapes job boards on a schedule and pushes
new listings to the tracker-api via HTTP.

Supported sources (Phase 2):
  - Indeed
  - LinkedIn
  - Glassdoor
  - Dice / Stack Overflow Jobs

Run locally:
  celery -A app.main worker --loglevel=info --beat
"""

from celery import Celery
from celery.schedules import crontab
from app.config import settings

app = Celery("scraper", broker=settings.redis_url, backend=settings.redis_url)

app.conf.beat_schedule = {
    "scrape-all-sources": {
        "task": "app.tasks.scrape_all",
        "schedule": crontab(minute=0, hour="*/2"),  # every 2 hours
    },
}

app.conf.timezone = "UTC"


@app.task(name="app.tasks.scrape_all")
def scrape_all():
    """Trigger all scraper sources. Implemented in Phase 2."""
    # TODO (Phase 2): import and call each scraper
    pass
