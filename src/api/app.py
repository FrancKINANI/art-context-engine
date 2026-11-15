"""FastAPI application (§6, §8).

Run it:

    uvicorn src.api.app:app --reload

The API is versioned under /api/v1; the reader pages are unversioned because a
permalink to a document should not break when the API changes.

Three things happen here, in order, before any route:

* the configuration is checked against what a payment-taking install needs
  (`ACE_ENV=production`); a bad deploy fails to boot instead of taking money
  with a forgeable cookie;
* every request is bounded (body size) and metered (per-IP rate limit);
* every response gets security headers and, for a new browser, an anonymous id.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response

from src.api import security
from src.api.routes import annotations, answers, billing, export, pages, works
from src.config import production_problems, settings

logger = logging.getLogger("ace")


def _validate_configuration() -> None:
    problems = production_problems() if settings.is_production else []
    if problems:
        raise RuntimeError(
            "refusing to start in production with an unsafe configuration:\n  - "
            + "\n  - ".join(problems)
        )
    if settings.is_production and not settings.session_secret:
        raise RuntimeError("ACE_SESSION_SECRET is required")


_validate_configuration()

app = FastAPI(
    title="Art Context Engine",
    version="0.3.0",
    description=(
        "A sourced, temporal, multi-perspective explanation of a work of art. "
        "Every sentence is tied to a dated source; disagreements and gaps are "
        "part of the answer, not smoothed over."
    ),
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if not settings.is_production else None,
)

API = "/api/v1"

for router in (
    answers.router, works.router, export.router, annotations.router, billing.router,
):
    app.include_router(router, prefix=API)

app.include_router(pages.router)
app.include_router(billing.pages)


# IPs and ports are not Host headers, so a trusted-host allowlist would reject
# legitimate `curl http://localhost:8000` health checks if we included them
# verbatim. The middleware only defends against a forged Host reaching a URL we
# would embed in a link.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_hosts,
)


@app.middleware("http")
async def harden(request: Request, call_next) -> Response:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.max_body_bytes:
        return JSONResponse({"error": "payload_too_large"}, status_code=413)

    limited = security.rate_limited(request)
    if limited is not None:
        return limited

    security.assign_visitor(request)
    response = await call_next(request)

    for name, value in security.SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if settings.cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    # A sign-in link is a credential in a URL: nothing on the account or auth
    # pages may be cached by a proxy or the browser's back/forward store.
    if security.sensitive_path(request.url.path):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    security.ensure_visitor(request, response)
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace or an internal address to a reader."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": "internal_error"}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════
# STATIC (CSS/JS are files so the CSP can forbid inline code)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/static/{name}", include_in_schema=False)
def static_asset(name: str) -> Response:
    """A two-file allowlist, not a directory server: no traversal, no listings."""
    from src.api import reader

    if name == "app.css":
        return Response(reader.stylesheet(), media_type="text/css; charset=utf-8")
    if name == "app.js":
        return Response(reader.SCRIPT, media_type="text/javascript; charset=utf-8")
    return JSONResponse({"error": "not_found"}, status_code=404)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "mode": settings.mode, "version": "0.3.0"}
