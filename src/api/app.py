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
from pathlib import Path

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
    title="Subtext",
    version="0.3.0",
    description=(
        "What a work means, and who said so: a sourced, dated, multi-perspective "
        "document about a work of art. "
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

FONTS = Path(__file__).resolve().parent / "static" / "fonts"

#: An allowlist, not a directory server: no traversal, no listings. The names are
#: literal, so `../` cannot resolve to anything and the OFL text is reachable
#: for the licence notice the fonts require.
_FONT_FILES = {
    "fonts/fraunces-latin.woff2": "font/woff2",
    "fonts/newsreader-latin.woff2": "font/woff2",
    "fonts/newsreader-italic-latin.woff2": "font/woff2",
    "fonts/OFL.txt": "text/plain; charset=utf-8",
}


@app.get("/static/{name:path}", include_in_schema=False)
def static_asset(name: str) -> Response:
    """Serve the design system and its fonts, and nothing else."""
    from src.api import reader

    if name == "app.css":
        return Response(
            reader.stylesheet(),
            media_type="text/css; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    if name == "app.js":
        return Response(
            reader.SCRIPT,
            media_type="text/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    media_type = _FONT_FILES.get(name)
    if media_type is None or ".." in name:
        return JSONResponse({"error": "not_found"}, status_code=404)

    try:
        payload = (FONTS / name.removeprefix("fonts/")).read_bytes()
    except OSError:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return Response(
        payload,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "mode": settings.mode, "version": "0.3.0"}


# ═══════════════════════════════════════════════════════════════════════════
# CRAWLERS (the document is public; the account is not)
# ═══════════════════════════════════════════════════════════════════════════

def _base() -> str:
    return (settings.base_url or "").rstrip("/")


@app.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /account\n"
        "Disallow: /api/\n"
        "Disallow: /static/\n"
        f"Sitemap: {_base()}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    """Static pages plus every published document.

    Documents are the long tail: each one is titled by the question it answers,
    which is exactly what someone types into a search engine.
    """
    from xml.sax.saxutils import escape as xml_escape

    from src.api.deps import store

    base = _base()
    rows = store().recent(limit=2000)
    urls = [
        f"<url><loc>{xml_escape(base + '/')}</loc><changefreq>daily</changefreq>"
        "<priority>1.0</priority></url>",
        f"<url><loc>{xml_escape(base + '/pricing')}</loc><changefreq>monthly</changefreq>"
        "<priority>0.4</priority></url>",
    ]
    for answer in rows:
        urls.append(
            f"<url><loc>{xml_escape(base + '/a/' + answer.id)}</loc>"
            f"<lastmod>{answer.generated_at:%Y-%m-%d}</lastmod>"
            "<changefreq>monthly</changefreq><priority>0.8</priority></url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls)
        + "</urlset>"
    )
    return Response(
        body,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )
