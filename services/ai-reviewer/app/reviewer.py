"""
Job reviewer using the Claude API — Phase 3
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ReviewResult:
    job_id: str
    score: float          # 0.0 – 10.0
    summary: str          # 1-2 sentence plain-English match summary
    pros: list[str]
    cons: list[str]
    recommended: bool


class JobReviewer:
    """
    Scores a job against user-defined criteria using Claude.
    Phase 3: implement with anthropic SDK.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def review(
        self,
        job_title: str,
        company: str,
        description: str,
        criteria: dict,
    ) -> Optional[ReviewResult]:
        # TODO (Phase 3): build prompt, call Claude, parse response
        raise NotImplementedError("AI reviewer coming in Phase 3")
