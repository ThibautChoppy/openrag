"""OIDC authentication routes — phase 4 of the OIDC integration.

Routes exposed (all bypassed by ``AuthMiddleware``):
  - ``GET  /auth/login``              — start Authorization Code + PKCE flow
  - ``GET  /auth/callback``           — handle IdP redirect, create session
  - ``POST /auth/backchannel-logout`` — IdP-driven session revocation (OIDC spec)
  - ``GET  /auth/logout``             — RP-initiated logout (local + IdP)

One more route sits *behind* the middleware:
  - ``GET  /auth/me``                 — debug endpoint returning the current user.

All routes return ``400`` when ``AUTH_MODE != "oidc"`` — the feature is dormant
in ``token`` mode.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

from components.auth import (
    OIDCClient,
    StateCookiePayload,
    StateCookieSerializer,
    decrypt_token,
    encrypt_token,
    get_oidc_client,
    issue_session_token,
)
from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from models.user import UserCreate
from utils.dependencies import get_vectordb
from utils.logger import get_logger, mask_email

# Whitelist mirrors ``api._OIDC_CLAIM_MAPPING_ALLOWED_FIELDS`` — kept in sync
# at the DB layer too (``PartitionFileManager.update_user_fields``).
_OIDC_CLAIM_MAPPING_ALLOWED_FIELDS = {"display_name", "email"}

logger = get_logger()
router = APIRouter()


SESSION_COOKIE_NAME = "openrag_session"


# ---------------------------------------------------------------------------
# Env helpers — read lazily so tests can monkeypatch os.environ
# ---------------------------------------------------------------------------


def _auth_mode() -> str:
    return os.getenv("AUTH_MODE", "token").strip().lower()


def _token_encryption_key() -> str:
    key = os.getenv("OIDC_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("OIDC_TOKEN_ENCRYPTION_KEY is not set")
    return key


def _claim_source() -> str:
    return os.getenv("OIDC_CLAIM_SOURCE", "id_token").strip().lower()


def _auto_provision_login() -> bool:
    """Whether to auto-provision a non-admin user on first OIDC login.

    Defaults to ``False`` — keeping the historical "admin pre-creates every
    user" model. Set ``OIDC_AUTO_PROVISION_LOGIN=true`` to enable: when the
    callback receives a ``sub`` that isn't yet mapped to an OpenRAG user,
    a row is created on the fly using the ID-token claims (``name`` /
    ``preferred_username`` for the display name, ``email`` if present).

    Auto-provisioned users are **never** admin and inherit the default file
    quota — operators can promote / adjust afterwards via ``/users/``.
    """
    return os.getenv("OIDC_AUTO_PROVISION_LOGIN", "false").strip().lower() == "true"


def _display_name_from_claims(claims: dict[str, Any], sub: str) -> str:
    """Pick a sensible display name from the standard OIDC claims.

    Falls back to a short ``sub`` prefix when nothing readable is available
    so the user row always has something printable for the UI.
    """
    for key in ("name", "preferred_username"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    given = claims.get("given_name") or ""
    family = claims.get("family_name") or ""
    composed = f"{given} {family}".strip()
    if composed:
        return composed
    # Last-resort fallback — keep enough of the sub to be unique-ish in the UI.
    return f"oidc-{sub[:8]}"


def _claim_mapping() -> dict[str, str]:
    """Parse ``OIDC_CLAIM_MAPPING`` at request time so tests can monkeypatch it.

    Shares the validation rules with ``api._parse_oidc_claim_mapping``: entries
    whose ``db_field`` is not whitelisted are silently dropped here because the
    hard-failure path belongs to the startup validator in ``api.py`` — at login
    time we prefer to log and continue rather than break the flow on a
    misconfiguration the operator has already been warned about.
    """
    raw = os.getenv("OIDC_CLAIM_MAPPING", "").strip()
    if not raw:
        return {}
    mapping: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        db_field, claim = pair.split(":", 1)
        db_field = db_field.strip()
        claim = claim.strip()
        if db_field not in _OIDC_CLAIM_MAPPING_ALLOWED_FIELDS or not claim:
            continue
        mapping[db_field] = claim
    return mapping


def _post_logout_redirect_uri() -> str | None:
    """Return the configured post-logout redirect URI, or None if unset.

    No default is provided: a default of "/" would land the user back on
    OpenRag's root which immediately re-triggers OIDC login (silent re-auth
    if the IdP session is still alive, or a loop on the IdP form if not).
    Operators deliberately choose a URL outside OpenRag (corporate intranet,
    a static 'you are logged out' page, the IdP's own post-logout page).
    """
    return os.getenv("OIDC_POST_LOGOUT_REDIRECT_URI")


def _oidc_client_id() -> str:
    return os.environ["OIDC_CLIENT_ID"]


def _require_oidc_mode():
    if _auth_mode() != "oidc":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AUTH_MODE is not 'oidc' — authentication routes are disabled.",
        )


def _is_request_secure(request: Request) -> bool:
    """True if the client-observed scheme is HTTPS.

    Checks multiple indicators:
    1. ``PREFERRED_URL_SCHEME`` env var (set when behind a TLS-terminating proxy)
    2. ``X-Forwarded-Proto`` header (set by reverse proxies like Traefik/Nginx)
    3. ``request.url.scheme`` (accounts for proxy_headers=True in uvicorn)
    """
    if os.environ.get("PREFERRED_URL_SCHEME", "").lower() == "https":
        return True
    # X-Forwarded-Proto can be comma-separated when chained through multiple
    # proxies (e.g. "https, http"); the client-most hop is the first entry.
    xfp = request.headers.get("x-forwarded-proto", "")
    if xfp.split(",", 1)[0].strip().lower() == "https":
        return True
    return request.url.scheme == "https"


def _state_serializer() -> StateCookieSerializer:
    return StateCookieSerializer(secret_key=_token_encryption_key())


def _allowed_next_origins() -> set[str]:
    """Origins (scheme://host[:port]) accepted as redirect targets after login.

    Mirrors the CORS allow_origins from ``api.py``: localhost dev ports plus
    ``INDEXERUI_URL`` so that the indexer-ui (served on a different port) can
    receive the user back after the OIDC flow completes.
    """
    origins = {"http://localhost:3042", "http://localhost:5173"}
    indexer_ui = os.getenv("INDEXERUI_URL")
    if indexer_ui:
        origins.add(indexer_ui.rstrip("/"))
    return origins


def _sanitize_next_url(next_url: str | None) -> str:
    """Accept either a same-origin relative path (``/...`` but not ``//...``)
    or an absolute URL whose origin is explicitly whitelisted (indexer-ui,
    dev-only localhost). Fall back to ``/`` on any mismatch — protects against
    open-redirect attacks.
    """
    if not next_url:
        return "/"
    # Reject backslashes (browsers treat "\" as "/", so "/\evil.com" resolves
    # protocol-relative) and any control chars (CR/LF header injection).
    if "\\" in next_url or any(ord(c) < 0x20 or ord(c) == 0x7F for c in next_url):
        return "/"
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    # Absolute URL: only allow whitelisted origins.
    parsed = urlparse(next_url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in _allowed_next_origins():
            return next_url
    return "/"


def _utcnow() -> datetime:
    # DB-side timestamps are naive local time (models' default is ``datetime.now``),
    # and every read site compares against ``datetime.now()``. Using ``datetime.now()``
    # here keeps newly-issued sessions from appearing pre-expired on non-UTC hosts.
    return datetime.now()


def _delete_state_cookie(response: Response) -> None:
    response.delete_cookie(
        key=StateCookieSerializer.COOKIE_NAME,
        path="/",
    )


def _json_error(status_code: int, detail: str, *, delete_state_cookie: bool = False) -> JSONResponse:
    r = JSONResponse(status_code=status_code, content={"detail": detail})
    if delete_state_cookie:
        _delete_state_cookie(r)
    return r


# ---------------------------------------------------------------------------
# GET /auth/login
# ---------------------------------------------------------------------------


@router.get("/auth/login", include_in_schema=False)
async def login(request: Request, next: str | None = None):
    _require_oidc_mode()
    client: OIDCClient = get_oidc_client()

    state, nonce = OIDCClient.generate_state_and_nonce()
    code_verifier, code_challenge = OIDCClient.generate_pkce_pair()

    try:
        auth_url = await client.build_authorization_url(state=state, nonce=nonce, code_challenge=code_challenge)
    except Exception as e:
        logger.error(f"Failed to build OIDC authorization URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OIDC discovery failed — see server logs.",
        ) from e

    payload = StateCookiePayload(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        next_url=_sanitize_next_url(next),
    )
    cookie_value = _state_serializer().dumps(payload)

    response = RedirectResponse(url=auth_url, status_code=302)
    response.set_cookie(
        key=StateCookieSerializer.COOKIE_NAME,
        value=cookie_value,
        max_age=StateCookieSerializer.DEFAULT_TTL_SECONDS,
        httponly=True,
        secure=_is_request_secure(request),
        samesite="lax",
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# GET /auth/callback
# ---------------------------------------------------------------------------


@router.get("/auth/callback", include_in_schema=False)
async def callback(request: Request, code: str | None = None, state: str | None = None):
    _require_oidc_mode()

    if not code or not state:
        return _json_error(
            status.HTTP_400_BAD_REQUEST,
            "Missing 'code' or 'state' query parameter.",
            delete_state_cookie=True,
        )

    # --- 1. Parse state cookie -------------------------------------------------
    cookie_raw = request.cookies.get(StateCookieSerializer.COOKIE_NAME)
    if not cookie_raw:
        return _json_error(
            status.HTTP_400_BAD_REQUEST,
            "OIDC state cookie missing.",
            delete_state_cookie=True,
        )

    try:
        payload = _state_serializer().loads(cookie_raw)
    except ValueError as e:
        logger.warning(f"Invalid OIDC state cookie: {e}")
        return _json_error(
            status.HTTP_400_BAD_REQUEST,
            "Invalid or expired OIDC state cookie.",
            delete_state_cookie=True,
        )

    # --- 2. CSRF check --------------------------------------------------------
    if state != payload.state:
        logger.warning("OIDC state mismatch between query and cookie")
        return _json_error(
            status.HTTP_400_BAD_REQUEST,
            "OIDC state mismatch.",
            delete_state_cookie=True,
        )

    # --- 3. Exchange code ------------------------------------------------------
    client: OIDCClient = get_oidc_client()
    try:
        bundle = await client.exchange_code(
            code=code,
            code_verifier=payload.code_verifier,
            expected_nonce=payload.nonce,
        )
    except Exception:
        # Log full exception for operators; return a generic message so IdP
        # URLs / stack-adjacent internals don't leak via the HTTP response.
        logger.exception("OIDC code exchange failed")
        return _json_error(
            status.HTTP_400_BAD_REQUEST,
            "OIDC code exchange failed",
            delete_state_cookie=True,
        )

    # --- 4. Extract sub and match user ----------------------------------------
    sub = bundle.claims.get("sub")
    if not sub:
        return _json_error(
            status.HTTP_400_BAD_REQUEST,
            "ID token missing 'sub' claim.",
            delete_state_cookie=True,
        )

    vdb = get_vectordb()
    user: dict[str, Any] | None = await vdb.get_user_by_external_id.remote(sub)
    if user is None:
        if not _auto_provision_login():
            logger.warning(f"OIDC login rejected — user not registered (sub={sub!r})")
            return _json_error(
                status.HTTP_403_FORBIDDEN,
                "User not registered",
                delete_state_cookie=True,
            )

        # Auto-provision: create a non-admin user from the ID-token claims.
        # Email is best-effort — populated when the IdP exposes it on the
        # ``email`` claim (typically via the ``email`` scope, which is in the
        # default ``OIDC_SCOPES``). Display name falls back to the sub when
        # the IdP exposes nothing readable.
        display_name = _display_name_from_claims(bundle.claims, sub)
        email = bundle.claims.get("email")
        try:
            user = await vdb.create_user.remote(
                UserCreate(
                    display_name=display_name,
                    external_user_id=sub,
                    email=email if isinstance(email, str) and email.strip() else None,
                    is_admin=False,
                )
            )
        except Exception as e:
            # create_user failed. Separate the two causes:
            #   1. Concurrent first-login on the same sub — another request
            #      already inserted the row; re-read by external_id and proceed.
            #   2. The unique email index rejected the insert because a row with
            #      this email already exists under a different identity. Matching
            #      is external_id-only, so it wasn't found above, and only an
            #      admin can reconcile it — surface an actionable 409 rather than
            #      an opaque 500.
            logger.exception(f"OIDC auto-provisioning failed for sub={sub!r}: {e}")
            user = await vdb.get_user_by_external_id.remote(sub)
            if user is None:
                if isinstance(email, str) and email.strip() and await vdb.get_user_by_email.remote(email):
                    logger.error(
                        f"OIDC auto-provisioning blocked for sub={sub!r}: an account with email "
                        f"{mask_email(email)} already exists under a different identity. Set that "
                        f"user's external_user_id to this sub to allow login."
                    )
                    return _json_error(
                        status.HTTP_409_CONFLICT,
                        "An account with this email already exists. Ask your administrator to "
                        "link it to your identity provider login.",
                        delete_state_cookie=True,
                    )
                return _json_error(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Failed to provision user",
                    delete_state_cookie=True,
                )
        else:
            # display_name (the user's real name) is intentionally not logged — id + sub
            # identify the row without writing PII to the logs.
            logger.info(f"OIDC user auto-provisioned (id={user['id']}, sub={sub!r})")

    # --- 4b. Auto-provision: keep display_name + email in sync with claims ----
    # When OIDC_AUTO_PROVISION_LOGIN is on, the IdP is treated as the source of
    # truth for these two fields on every login (not just at creation), so a
    # user renamed in the IdP doesn't drift out of sync. No-op for users whose
    # row already matches the claims (including the user we just created).
    if _auto_provision_login():
        derived_display = _display_name_from_claims(bundle.claims, sub)
        derived_email_raw = bundle.claims.get("email")
        derived_email = (
            derived_email_raw.strip() if isinstance(derived_email_raw, str) and derived_email_raw.strip() else None
        )

        sync_updates: dict[str, Any] = {}
        if derived_display and user.get("display_name") != derived_display:
            sync_updates["display_name"] = derived_display
        if derived_email is not None and user.get("email") != derived_email:
            sync_updates["email"] = derived_email

        if sync_updates:
            try:
                await vdb.update_user_fields.remote(user["id"], sync_updates)
            except Exception as e:
                logger.warning(f"OIDC auto-provision sync failed for user_id={user['id']}: {e}")
            else:
                refreshed = await vdb.get_user_by_external_id.remote(sub)
                if refreshed is not None:
                    user = refreshed

    # --- 5. Optional claim-mapping update --------------------------------------
    mapping = _claim_mapping()
    if mapping:
        if _claim_source() == "userinfo":
            try:
                claims_for_mapping: dict[str, Any] = await client.fetch_userinfo(bundle.access_token)
            except Exception as e:
                logger.warning(f"OIDC userinfo fetch failed: {e}")
                return _json_error(
                    status.HTTP_400_BAD_REQUEST,
                    "Failed to fetch userinfo from IdP.",
                    delete_state_cookie=True,
                )
            # Bind userinfo to the verified ID token: per the OIDC spec the
            # userinfo `sub` MUST equal the ID token `sub`, else the response
            # could describe a different principal (token substitution).
            if claims_for_mapping.get("sub") != sub:
                logger.warning(f"OIDC userinfo sub mismatch for user_id={user['id']}")
                return _json_error(
                    status.HTTP_400_BAD_REQUEST,
                    "userinfo sub does not match ID token.",
                    delete_state_cookie=True,
                )
        else:
            claims_for_mapping = bundle.claims

        updates: dict[str, Any] = {}
        for db_field, claim in mapping.items():
            value = claims_for_mapping.get(claim)
            if value is None:
                continue
            # No-op filter: skip fields already matching, so we don't churn the DB.
            if user.get(db_field) == value:
                continue
            updates[db_field] = value

        if updates:
            try:
                await vdb.update_user_fields.remote(user["id"], updates)
            except Exception as e:
                logger.warning(f"update_user_fields failed for user_id={user['id']}: {e}")
            else:
                # Refresh the user dict so anything downstream sees the new values.
                refreshed = await vdb.get_user_by_external_id.remote(sub)
                if refreshed is not None:
                    user = refreshed

    # --- 6. Timestamps ---------------------------------------------------------
    now = _utcnow()
    expires_in = max(int(bundle.expires_in or 0), 60)
    access_token_expires_at = now + timedelta(seconds=expires_in)
    if bundle.refresh_token:
        session_expires_at = now + timedelta(days=7)
    else:
        session_expires_at = access_token_expires_at

    # --- 7. Issue session & encrypt ------------------------------------------
    plain, _hashed = issue_session_token()
    key = _token_encryption_key()
    id_token_encrypted = encrypt_token(bundle.id_token, key=key)
    access_token_encrypted = encrypt_token(bundle.access_token, key=key)
    refresh_token_encrypted = encrypt_token(bundle.refresh_token, key=key)
    sid = bundle.claims.get("sid")

    await vdb.create_oidc_session.remote(
        user_id=user["id"],
        sub=sub,
        sid=sid,
        session_token_plain=plain,
        id_token_encrypted=id_token_encrypted,
        access_token_encrypted=access_token_encrypted,
        refresh_token_encrypted=refresh_token_encrypted,
        access_token_expires_at=access_token_expires_at,
        session_expires_at=session_expires_at,
    )

    # --- 8. Build redirect: clear state cookie, set session cookie -----------
    next_url = _sanitize_next_url(payload.next_url)
    redirect = RedirectResponse(url=next_url, status_code=302)
    _delete_state_cookie(redirect)

    max_age = max(int((session_expires_at - now).total_seconds()), 1)
    redirect.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=plain,
        max_age=max_age,
        httponly=True,
        secure=_is_request_secure(request),
        samesite="lax",
        path="/",
    )

    logger.info(f"OIDC login success — user_id={user['id']}, sid={sid!r}, next={next_url!r}")
    return redirect


# ---------------------------------------------------------------------------
# POST /auth/backchannel-logout
# ---------------------------------------------------------------------------


# Seen logout-token jti -> exp, so a token is only consumed once. Per-worker;
# logout is idempotent so this is just defence-in-depth.
_seen_logout_jti: dict[str, int] = {}


def _logout_jti_is_replay(jti: str, exp: int) -> bool:
    """Record a logout-token jti and report whether it was already seen."""
    now = int(time.time())
    # Prune expired entries to bound memory.
    for old_jti, old_exp in list(_seen_logout_jti.items()):
        if old_exp < now:
            del _seen_logout_jti[old_jti]
    if jti in _seen_logout_jti:
        return True
    _seen_logout_jti[jti] = exp
    return False


@router.post("/auth/backchannel-logout", include_in_schema=False)
async def backchannel_logout(logout_token: str = Form(...)):
    """IdP-initiated logout per OIDC Back-Channel Logout spec.

    Content-Type: ``application/x-www-form-urlencoded`` with field ``logout_token``.
    """
    _require_oidc_mode()

    client: OIDCClient = get_oidc_client()

    try:
        claims = await client.verify_logout_token(logout_token)
    except ValueError as e:
        logger.warning(f"Invalid back-channel logout token: {e}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_request", "error_description": str(e)},
            headers={"Cache-Control": "no-store"},
        )
    except Exception as e:
        logger.warning(f"Back-channel logout token verification failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_request"},
            headers={"Cache-Control": "no-store"},
        )

    # Reject replays: a given logout token (jti) must only be processed once.
    if claims.jti and _logout_jti_is_replay(claims.jti, claims.exp):
        logger.warning(f"Replayed back-channel logout token ignored — jti={claims.jti!r}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_request", "error_description": "logout_token replayed"},
            headers={"Cache-Control": "no-store"},
        )

    if claims.sid:
        vdb = get_vectordb()
        count = await vdb.revoke_oidc_sessions_by_sid.remote(claims.sid)
        logger.info(f"Back-channel logout revoked sessions — sid={claims.sid!r}, count={count}")
    else:
        # Plan §2 #10 limits back-channel logout scope to sid only.
        # Still return 200 to keep the IdP happy.
        logger.warning(
            f"Received sid-less back-channel logout token — not supported; "
            f"ignoring per implementation policy (sub={claims.sub!r})"
        )

    return Response(
        status_code=status.HTTP_200_OK,
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# GET /auth/logout
# ---------------------------------------------------------------------------


def _is_csrf_safe_navigation(request: Request) -> bool:
    """Block cross-site CSRF logout while allowing top-level navigation.

    Logout stays a GET (OIDC redirects to the IdP), so instead of requiring POST
    we use Fetch Metadata: a cross-site non-navigation request (forged <img> etc.)
    is blocked; navigations and same-origin requests pass. Older browsers that
    omit these headers are allowed.
    """
    site = request.headers.get("sec-fetch-site")
    mode = request.headers.get("sec-fetch-mode")
    if site == "cross-site" and mode not in (None, "navigate"):
        return False
    return True


@router.get("/auth/logout", include_in_schema=False)
async def logout(request: Request):
    _require_oidc_mode()

    if not _is_csrf_safe_navigation(request):
        logger.warning("Blocked cross-site non-navigation request to /auth/logout (CSRF)")
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Cross-site logout requests are not allowed"},
        )

    vdb = get_vectordb()
    client: OIDCClient = get_oidc_client()

    # Look up & revoke the session; keep the id_token to forward as id_token_hint.
    id_token_hint: str | None = None
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_value:
        session = await vdb.get_oidc_session_by_token.remote(cookie_value)
        if session:
            enc = session.get("id_token_encrypted")
            if enc:
                try:
                    id_token_hint = decrypt_token(enc, key=_token_encryption_key())
                except ValueError as e:
                    logger.warning(f"Failed to decrypt id_token for logout: {e}")
            try:
                await vdb.revoke_oidc_session_by_id.remote(session["id"])
            except Exception as e:
                logger.warning(f"Failed to revoke oidc_session during logout: {e}")

    # Build redirect target: IdP end_session if discovery provides one,
    # otherwise the configured post-logout URL. If neither is available
    # we return a plain 200 with the cookie deleted — better than a 302
    # loop through the root.
    local_target = _post_logout_redirect_uri()
    redirect_target: str | None = local_target
    try:
        meta = await client.discover()
        end_session = meta.get("end_session_endpoint")
        if end_session:
            params: dict[str, str] = {"client_id": _oidc_client_id()}
            if local_target:
                params["post_logout_redirect_uri"] = local_target
            if id_token_hint:
                params["id_token_hint"] = id_token_hint
            redirect_target = f"{end_session}?{urlencode(params)}"
    except Exception as e:
        logger.warning(f"OIDC discovery failed during logout, skipping IdP redirect: {e}")

    if redirect_target:
        response = RedirectResponse(url=redirect_target, status_code=302)
    else:
        # No IdP end_session and no local post-logout URL → just confirm the
        # logout in-place. The cookie deletion below still takes effect.
        response = JSONResponse(status_code=200, content={"detail": "Logged out"})
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# GET /auth/me  — standard AuthMiddleware applies (route NOT in bypass list)
# ---------------------------------------------------------------------------


@router.get("/auth/me")
async def me(request: Request):
    """Debug/health endpoint — returns the user bound by AuthMiddleware."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No authenticated user on request.state",
        )
    oidc_session = getattr(request.state, "oidc_session", None)
    session_expires_at = None
    if oidc_session and oidc_session.get("session_expires_at"):
        exp = oidc_session["session_expires_at"]
        try:
            # naive datetime → iso str
            session_expires_at = exp.isoformat()
        except AttributeError:
            session_expires_at = str(exp)

    return {
        "user_id": user.get("id"),
        "email": user.get("email"),
        "auth_method": "oidc" if oidc_session else "token",
        "session_expires_at": session_expires_at,
    }
