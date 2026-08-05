"""Classify an LLM exception as permanent (the user must fix it) or transient.

A deliberate small copy of tracker-api's logic — the two services build separate
images and share no package (same call already made for `_skills_block`).

The distinction drives two things that must not be confused:

  * permanent → recorded on the user's key, shown as a banner, task NOT retried
  * transient → nothing recorded, task retried

Getting it backwards is worse than not classifying at all: telling a rate-limited
user their model was retired sends them to change a setting that was never wrong.
"""

import logging

import litellm

logger = logging.getLogger(__name__)

INVALID_MODEL = "invalid_model"
INVALID_KEY = "invalid_key"
# Reported only after every retry is exhausted — see main.py. classify_llm_error
# never returns these: at classification time a 429 is still just transient, and
# treating one as a ceiling would nag a user who is merely mid-burst.
#
# The two are kept apart because they call for different advice. Telling someone
# their provider is throttling them when it is actually unreachable sends them to
# change a quota that was never the problem.
RATE_LIMITED = "rate_limited"
PROVIDER_UNAVAILABLE = "provider_unavailable"

# The call succeeded and the model answered — with something that isn't the JSON
# the scorer needs. Not a call failure at all, so it never comes from
# classify_llm_error; reviewer.py returns None and main.py reports this.
# tracker-api counts a streak before showing the user anything.
UNUSABLE_OUTPUT = "unusable_output"

# Substrings that identify the model — not the key, not the quota — as the thing
# the provider rejected.
_MODEL_GONE_PHRASES = ("not found", "deprecated", "invalid", "does not exist", "unsupported")

# Errors that mean "try again later". These are checked FIRST, by exception TYPE,
# before any message text is examined.
_TRANSIENT_TYPES = (
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
    litellm.Timeout,
    litellm.APIConnectionError,
)


def classify_llm_error(exc: Exception) -> str | None:
    """Return INVALID_MODEL / INVALID_KEY for permanent failures, else None.

    None means "transient or unknown" — the caller should retry and record
    nothing. Unknown is deliberately grouped with transient: a wrong retry costs
    one API call, a wrong banner costs the user's trust in the banner.
    """
    # Type first, always. A 429 body can easily contain the word "invalid", and
    # sniffing text before types would read that as a retired model.
    if isinstance(exc, _TRANSIENT_TYPES):
        return None

    # A prompt too long for the model is about this one job, not the key.
    if isinstance(exc, litellm.ContextWindowExceededError):
        return None

    if isinstance(exc, litellm.AuthenticationError):
        return INVALID_KEY

    err = str(exc).lower()

    # An auth failure that arrived as some other exception type.
    if "api key" in err:
        return INVALID_KEY

    if "model" in err and any(p in err for p in _MODEL_GONE_PHRASES):
        return INVALID_MODEL

    if isinstance(exc, litellm.NotFoundError):
        # A 404 from a chat-completions call is about the model; there is nothing
        # else in the URL that could be missing.
        return INVALID_MODEL

    return None


# Markers that a failure really is a quota/throttle rather than an outage.
# Checked only after the exception TYPE, and only for errors already known to be
# transient — so this can never turn a 429 into a "dead model".
_RATE_LIMIT_PHRASES = ("rate limit", "ratelimit", "quota", "resource_exhausted", "too many requests")


def transient_kind(exc: Exception) -> str:
    """For a transient failure that outlasted every retry: which kind was it?

    Only consulted at retry exhaustion. The distinction exists because a quota
    ceiling is something the user can act on (raise the tier, pick a cheaper
    model) while an unreachable provider is something they can only wait out.
    """
    if isinstance(exc, litellm.RateLimitError):
        return RATE_LIMITED
    err = str(exc).lower()
    if any(p in err for p in _RATE_LIMIT_PHRASES):
        return RATE_LIMITED
    # Timeouts, connection failures, 5xx — the provider is not answering. Saying
    # "you are being rate-limited" here would be a confident wrong diagnosis.
    return PROVIDER_UNAVAILABLE


class LLMCallFailed(Exception):
    """A completion call failed. `kind` is a permanent verdict, or None if the
    failure looks transient and the task should retry."""

    def __init__(self, kind: str | None, message: str, transient: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        # Which flavour of transient this was, for the caller that runs out of
        # retries. None for permanent failures, which never reach that branch.
        self.transient = transient

    @property
    def permanent(self) -> bool:
        return self.kind is not None
