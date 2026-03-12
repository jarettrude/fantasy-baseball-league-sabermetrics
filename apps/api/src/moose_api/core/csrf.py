"""CSRF protection via the Double-Submit Cookie pattern (OWASP recommended).

How it works:
  1. On any state-mutating request (POST/PUT/DELETE/PATCH), the middleware
     checks for a CSRF token in both:
       - A non-HttpOnly cookie (`csrf_token`) — set by the server.
       - A request header (`X-CSRF-Token`) — sent by the client JS.
  2. If both are present and match, the request proceeds.
  3. If either is missing or they don't match, the request is rejected with 403.

The CSRF cookie is set:
  - On successful authentication (login callback).
  - Refreshed by the middleware if missing on safe (GET/HEAD/OPTIONS) requests.

Why double-submit cookie:
  - Stateless: no server-side token store needed (beyond the cookie itself).
  - Works with SPA architectures where the JS app reads the cookie and
    sends it as a header.
  - OWASP-recommended for APIs consumed by browser-based clients.

Why a signed token:
  - Uses HMAC-SHA256 with a server secret to prevent cookie injection attacks.
  - The token is `timestamp.signature` so it can also be time-limited.

Frontend integration:
  - The CSRF cookie is `SameSite=Lax`, `Secure`, but NOT `HttpOnly` (so JS
    can read it via `document.cookie`).
  - The client reads `csrf_token` and sends it in the `X-CSRF-Token` header
    on every mutating request.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from moose_api.core.config import settings

logger = logging.getLogger(__name__)

CSRF_TOKEN_MAX_AGE = 86400
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"

UNSAFE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/callback",
    "/api/health",
    "/api/config",
}


def _get_cookie_domain() -> str | None:
    """Extract the root domain from web_origin for cross-subdomain cookies.

    Examples:
        https://moosesportsempire.ca -> .moosesportsempire.ca
        https://localhost -> None (no domain for localhost)
    """
    parsed = urlparse(settings.web_origin)
    hostname = parsed.hostname

    if not hostname or hostname == "localhost" or hostname.startswith("127."):
        return None

    # Return with leading dot for subdomain sharing
    return f".{hostname}"


def _get_csrf_secret() -> str:
    """Derive CSRF signing key from available secrets."""
    base = settings.csrf_secret or settings.jwt_secret_key
    return hashlib.sha256(f"csrf-{base}".encode()).hexdigest()


def generate_csrf_token() -> str:
    """Generate a signed CSRF token: `nonce.timestamp.signature`."""
    secret = _get_csrf_secret()
    nonce = secrets.token_urlsafe(32)
    timestamp = str(int(time.time()))
    payload = f"{nonce}.{timestamp}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def validate_csrf_token(token: str) -> bool:
    """Validate a signed CSRF token's integrity and freshness."""
    secret = _get_csrf_secret()
    parts = token.split(".")
    if len(parts) != 3:
        return False

    nonce, timestamp_str, signature = parts
    payload = f"{nonce}.{timestamp_str}"

    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False

    try:
        token_time = int(timestamp_str)
        if time.time() - token_time > CSRF_TOKEN_MAX_AGE:
            logger.warning("CSRF token expired (age: %ds)", time.time() - token_time)
            return False
    except ValueError:
        return False

    return True


def set_csrf_cookie(response: Response, token: str | None = None) -> str:
    """Set the CSRF cookie on a response. Returns the token."""
    if token is None:
        token = generate_csrf_token()

    cookie_domain = _get_cookie_domain()

    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=True,
        samesite="lax",
        max_age=CSRF_TOKEN_MAX_AGE,
        path="/",
        domain=cookie_domain,
    )
    return token


class CSRFMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces double-submit cookie CSRF protection."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in UNSAFE_METHODS:
            response = await call_next(request)
            if not request.cookies.get(CSRF_COOKIE_NAME):
                set_csrf_cookie(response)
            return response

        path = request.url.path.rstrip("/")
        if path in CSRF_EXEMPT_PATHS:
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        header_token = request.headers.get(CSRF_HEADER_NAME)

        if not cookie_token or not header_token:
            logger.warning(
                "CSRF validation failed: missing %s for %s %s",
                "cookie" if not cookie_token else "header",
                request.method,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token missing",
            )

        if not hmac.compare_digest(cookie_token, header_token):
            logger.warning(
                "CSRF validation failed: token mismatch for %s %s",
                request.method,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token mismatch",
            )

        if not validate_csrf_token(cookie_token):
            logger.warning(
                "CSRF validation failed: invalid/expired token for %s %s",
                request.method,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token invalid or expired",
            )

        response = await call_next(request)
        return response
