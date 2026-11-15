"""HTTP hardening: headers, body limits, rate limits, visitor identity.

Everything here runs for every request. It is one ASGI middleware instead of
four so the order is explicit and a request that is too large or too frequent
is rejected before it touches the pipeline, the SQLite store, or Stripe.

Threats, and the line that answers each:

* **Clickjacking / script injection** — CSP without ``unsafe-inline``: the CSS
  and JS live in ``/static``, so a compromised source string cannot execute.
* **Content sniffing** — ``X-Content-Type-Options: nosniff``.
* **Referer leakage to cited sites** — ``Referrer-Policy: no-referrer``.
* **Body-flood DoS** — requests over ``ACE_MAX_BODY_BYTES`` get 413.
* **Scraping / quota farming** — per-IP sliding window, tighter on sign-in.
* **Shared anonymous bucket** — each browser gets an ``ace_anon`` cookie, so one
  visitor cannot exhaust another's free answers.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.config import settings

ANON_COOKIE = "ace_anon"
ANON_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

#: Headers applied to every response. `frame-ancestors 'none'` is the modern
#: clickjacking control; X-Frame-Options is kept for old browsers.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


def new_anon_id() -> str:
    return f"anon_{secrets.token_hex(16)}"


#: Paths whose responses carry or accept a credential. They are never cached.
_SENSITIVE_PREFIXES = ("/account", "/auth/", "/api/v1/auth/", "/api/v1/billing/", "/billing/")


def sensitive_path(path: str) -> bool:
    return path.startswith(_SENSITIVE_PREFIXES)


def client_ip(request: Request) -> str:
    """The address a rate limit is charged to.

    Behind a reverse proxy every request looks like 127.0.0.1, so the proxy must
    be trusted explicitly (``ACE_TRUST_PROXY=1``) and the header taken from the
    right-hand side entry the proxy appended, not the left one a client can
    forge. With one proxy hop, the last entry is the proxy's own addition.
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════

class SlidingWindow:
    """Requests per IP per minute.

    ponytail: process-local memory. Correct for one uvicorn worker, which is what
    SQLite wants anyway; if this ever runs multi-worker, move the counters to
    Redis or the database rather than raising the limit.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            # Bound memory: an attacker rotating addresses must not grow the map
            # without limit.
            if len(self._hits) > 10_000:
                for stale in [k for k, q in self._hits.items() if not q]:
                    self._hits.pop(stale, None)
            return True


_general = SlidingWindow(settings.rate_limit_per_minute)
_sign_in = SlidingWindow(settings.sign_in_limit_per_minute)


def rate_limited(request: Request) -> Response | None:
    """Returns a 429 response when the caller must wait, else None."""
    ip = client_ip(request)
    path = request.url.path
    if request.method != "POST":
        return None

    if path in ("/auth/sign-in", "/billing/subscribe", "/account/sign-in") or path.startswith("/api/v1/auth/"):
        # Key includes the path so a blocked sign-in does not block reading.
        allowed = _sign_in.allow(f"{path}:{ip}")
    elif (
        path.startswith("/api/v1/annotations")
        or path.endswith("/feedback")
        or path in ("/ask", "/api/v1/answers", "/api/v1/billing/checkout")
    ):
        allowed = _general.allow(f"{path}:{ip}")
    else:
        return None

    if allowed:
        return None

    retry_after = str(int(_general.window))
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(
            "<!doctype html><title>Too many requests</title>"
            "<h1>Slow down</h1><p>Too many requests from this address. "
            "Please wait a minute and try again.</p>",
            status_code=429,
            headers={"Retry-After": retry_after},
        )
    return JSONResponse(
        {"error": "rate_limited", "message": "Too many requests; retry later."},
        status_code=429,
        headers={"Retry-After": retry_after},
    )


# ═══════════════════════════════════════════════════════════════════════════
# VISITOR IDENTITY
# ═══════════════════════════════════════════════════════════════════════════

def assign_visitor(request: Request) -> str | None:
    """Bind a visitor id to the request before the handler runs.

    Setting the cookie only on the response was too late: the *first* request
    from a new browser reached the route with no identity, so its annotation (or
    its first answer) was charged to nobody. The id is minted here and the cookie
    is written on the way out.
    """
    if request.cookies.get(ANON_COOKIE) or request.cookies.get("ace_session"):
        return None
    visitor_id = new_anon_id()
    request.state.anon_id = visitor_id
    return visitor_id


def ensure_visitor(request: Request, response: Response) -> None:
    """Give an anonymous browser the id it was metered and annotated against.

    Without this cookie every visitor shared the single ``anon`` quota row, so
    the first two answers exhausted the free tier for the entire internet.
    """
    if request.cookies.get(ANON_COOKIE) or request.cookies.get("ace_session"):
        return
    visitor_id = getattr(request.state, "anon_id", None)
    if visitor_id is None:
        return
    response.set_cookie(
        ANON_COOKIE,
        visitor_id,
        max_age=ANON_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
