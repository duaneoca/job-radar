"""
Email Monitor Service — Phase 4
---------------------------------
Polls Gmail via the Gmail API for messages from companies
you've applied to, and creates timeline events in the tracker-api.

Auth flow: OAuth2 — user authorizes once, tokens stored securely.
"""

import time
from app.config import settings


def run():
    """Main polling loop. Implemented in Phase 4."""
    print("Email monitor started. Phase 4 implementation pending.")
    while True:
        # TODO (Phase 4): poll inbox, match against applied companies,
        # post events to tracker-api
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    run()
