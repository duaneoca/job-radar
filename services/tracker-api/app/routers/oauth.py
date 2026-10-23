"""OAuth endpoints — the HTTP skin over app/oauth.py.

Public (no cookie): metadata, register, token, revoke. These are reached by the
assistants' servers, not browsers.
Cookie-authenticated: the consent page's context + decision, and the user's
Connected apps list. The consent page itself is the SPA route /oauth/authorize;
it calls `context` to learn who is asking, then `decide`.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import assistant, models, oauth, ratelimit, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _base_url(request: Request) -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _client_ip(request: Request) -> str:
    return ratelimit.client_ip(request.headers, request.client)


# Registrations across ALL callers per window. Per-IP limits can be sidestepped
# by anyone with many addresses, and /oauth/register creates rows without
# auth; this bounds the table whatever the caller does. Real traffic is one
# registration per new connection — hundreds a quarter-hour is already absurd.
GLOBAL_REGISTER_LIMIT = 200


async def _form(request: Request) -> dict:
    """RFC 6749 token requests are application/x-www-form-urlencoded; accept
    JSON too so a curl in a hurry still works."""
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


# ── Discovery ─────────────────────────────────────────────────

@router.get("/oauth/metadata", include_in_schema=False)
@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
def authorization_server_metadata(request: Request):
    """RFC 8414. nginx serves this at the issuer's well-known path."""
    return JSONResponse(oauth.metadata(_base_url(request)), headers={"Cache-Control": "public, max-age=300"})


# ── Client registration (RFC 7591) ────────────────────────────

@router.post("/oauth/register", status_code=201)
async def register(request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if (not ratelimit.is_allowed(f"oauth-register:{ip}")
            or not ratelimit.is_allowed("oauth-register:*", GLOBAL_REGISTER_LIMIT)):
        raise HTTPException(status_code=429, detail="Too many registrations. Try again later.")
    ratelimit.record_failure(f"oauth-register:{ip}")     # counts attempts, not failures
    ratelimit.record_failure("oauth-register:*")
    try:
        body = await request.json()
    except Exception:
        body = None
    try:
        client = oauth.register_client(db, body)
    except oauth.RegistrationError as e:
        logger.warning("oauth: registration refused from %s: %s", ip, e.description)
        return JSONResponse({"error": e.error, "error_description": e.description}, status_code=400)
    return JSONResponse({
        "client_id": client.id,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_id_issued_at": int(client.created_at.timestamp()),
    }, status_code=201, headers=_NO_STORE)


# ── Consent (SPA page at /oauth/authorize) ────────────────────

def _authorize_error(e: oauth.AuthorizeError) -> HTTPException:
    return HTTPException(status_code=400, detail={
        "error": e.error, "error_description": e.description, "redirect_to": e.redirect_to,
    })


@router.get("/oauth/authorize/context", response_model=schemas.OAuthAuthorizeContext)
def authorize_context(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Who is asking, and for what — validated. The page shows this and asks
    the user. Errors before the redirect_uri is verified have no redirect_to:
    the user sees them here and is never sent to an unverified address."""
    params = dict(request.query_params)
    try:
        client, scopes = oauth.check_authorization_request(db, params)
    except oauth.AuthorizeError as e:
        raise _authorize_error(e)
    from urllib.parse import urlsplit
    return schemas.OAuthAuthorizeContext(
        vendor=client.vendor,
        vendor_label=oauth.VENDORS[client.vendor],
        client_name=client.client_name,
        client_kind=client.kind,
        redirect_host=urlsplit(params["redirect_uri"]).hostname or "",
        scopes=[schemas.OAuthScopeOut(id=s, label=models.OAUTH_SCOPES[s]) for s in scopes],
    )


@router.post("/oauth/authorize/decide", response_model=schemas.OAuthRedirect)
def authorize_decide(
    payload: schemas.OAuthDecision,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The user's answer. Allow issues a code; deny sends the client an
    access_denied. Either way the page navigates to redirect_to."""
    params = payload.params
    try:
        client, scopes = oauth.check_authorization_request(db, params)
    except oauth.AuthorizeError as e:
        raise _authorize_error(e)
    base = _base_url(request)
    if payload.decision != "allow":
        logger.info("oauth: user %s declined %s", current_user.id, client.vendor)
        return schemas.OAuthRedirect(redirect_to=oauth.deny_url(params, base))
    url = oauth.issue_code(db, current_user, client, params, scopes, base)
    logger.info("oauth: user %s allowed %s (%s)", current_user.id, client.vendor, " ".join(scopes))
    return schemas.OAuthRedirect(redirect_to=url)


# ── Token (RFC 6749 §4.1.3, §6) and revocation (RFC 7009) ─────

@router.post("/oauth/token")
async def token(request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not ratelimit.is_allowed(f"oauth-token:{ip}"):
        return JSONResponse({"error": "invalid_request", "error_description": "Too many failed attempts."},
                            status_code=429, headers=_NO_STORE)
    form = await _form(request)
    grant_type = form.get("grant_type")
    try:
        if grant_type == "authorization_code":
            out = oauth.exchange_code(db, form)
        elif grant_type == "refresh_token":
            out = oauth.refresh(db, form)
        else:
            raise oauth.TokenError("unsupported_grant_type", "Use authorization_code or refresh_token.")
    except oauth.TokenError as e:
        ratelimit.record_failure(f"oauth-token:{ip}")
        logger.warning("oauth: token request from %s refused: %s %s", ip, e.error, e.description)
        return JSONResponse({"error": e.error, "error_description": e.description},
                            status_code=401 if e.error == "invalid_client" else 400, headers=_NO_STORE)
    return JSONResponse(out, headers=_NO_STORE)


@router.post("/oauth/revoke")
async def revoke(request: Request, db: Session = Depends(get_db)):
    oauth.revoke_token(db, await _form(request))
    return JSONResponse({}, headers=_NO_STORE)


# ── Connected apps (Settings) ─────────────────────────────────

def _app_out(g: models.OAuthGrant) -> schemas.ConnectedAppOut:
    return schemas.ConnectedAppOut(
        id=g.id, vendor=g.client.vendor, vendor_label=oauth.VENDORS[g.client.vendor],
        client_name=g.client.client_name, scopes=g.scope.split(),
        created_at=g.created_at, last_used_at=g.last_used_at,
    )


@router.get("/oauth/connected-apps", response_model=list[schemas.ConnectedAppOut])
def connected_apps(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    grants = (db.query(models.OAuthGrant)
                .filter(models.OAuthGrant.user_id == current_user.id, models.OAuthGrant.revoked_at.is_(None))
                .order_by(models.OAuthGrant.created_at.desc()).all())
    return [_app_out(g) for g in grants]


@router.get("/oauth/assistant-usage", response_model=schemas.AssistantUsageOut)
def assistant_usage(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """What connected apps have done in the last 24 h, against the limits."""
    return assistant.usage(db, current_user.id)


@router.delete("/oauth/connected-apps/{grant_id}", status_code=204)
def disconnect_app(
    grant_id: UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    grant = db.get(models.OAuthGrant, grant_id)
    if grant is None or grant.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Not found")
    oauth.revoke_grant(db, grant)
    logger.info("oauth: user %s disconnected %s", current_user.id, grant.client.vendor)
