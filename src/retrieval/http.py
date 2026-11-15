"""HTTP backends: live, fixture, and recording.

The point of this indirection is evaluation honesty. Recorded responses are the
corpus; if the corpus is live and unpredictable, every metric moves when the
internet does, and "did the prompt change make it better?" becomes unanswerable
(§10.1).
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from src.config import FIXTURE_DIR, settings


class FetchError(RuntimeError):
    """A response could not be produced in the current mode."""


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: str
    content_type: str = "application/json"
    fetched_at: datetime = datetime.now(timezone.utc)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> dict:
        if not self.ok:
            raise FetchError(f"{self.status} for {self.url}")
        try:
            return json.loads(self.body)
        except json.JSONDecodeError as exc:  # pragma: no cover - fixture corruption
            raise FetchError(f"non-JSON body for {self.url}: {exc}") from exc


class Backend(Protocol):
    def get(self, url: str) -> Response: ...


# ═══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════

def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


class FixtureBackend:
    """Reads recorded responses from ``tests/fixtures/http``.

    ``index.json`` maps the exact URL to ``{file, status, content_type,
    fetched_at}``. Misses raise instead of silently hitting the network in
    fixture mode — a test that reaches the internet is not a test.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = Path(directory or FIXTURE_DIR)
        self._index: dict[str, dict] | None = None
        self._lock = threading.Lock()

    @property
    def index(self) -> dict[str, dict]:
        with self._lock:
            if self._index is None:
                path = self.dir / "index.json"
                self._index = json.loads(path.read_text()) if path.exists() else {}
            return self._index

    def has(self, url: str) -> bool:
        return url in self.index

    def get(self, url: str) -> Response:
        entry = self.index.get(url)
        if entry is None:
            raise FetchError(f"no fixture for {url} (run `python -m src.retrieval.record`)")
        body = (self.dir / entry["file"]).read_text(encoding="utf-8")
        return Response(
            url=url,
            status=entry.get("status", 200),
            body=body,
            content_type=entry.get("content_type", "application/json"),
            fetched_at=datetime.fromisoformat(entry["fetched_at"]),
        )


# ═══════════════════════════════════════════════════════════════════════════
# LIVE
# ═══════════════════════════════════════════════════════════════════════════

class LiveBackend:
    def __init__(self, timeout: float | None = None, user_agent: str | None = None) -> None:
        import httpx

        self.timeout = timeout or settings.timeout_seconds
        self._client = httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": user_agent or settings.user_agent},
            follow_redirects=True,
        )

    def get(self, url: str) -> Response:
        r = self._client.get(url)
        return Response(
            url=url,
            status=r.status_code,
            body=r.text,
            content_type=r.headers.get("content-type", ""),
        )

    def close(self) -> None:
        self._client.close()


# ═══════════════════════════════════════════════════════════════════════════
# RECORDING / AUTO
# ═══════════════════════════════════════════════════════════════════════════

class RecordingBackend:
    """Fetches live and writes the response into the fixture directory."""

    def __init__(self, inner: LiveBackend | None = None, directory: Path | None = None) -> None:
        self.dir = Path(directory or FIXTURE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.inner = inner or LiveBackend()
        self._index_path = self.dir / "index.json"
        self._index: dict[str, dict] = (
            json.loads(self._index_path.read_text()) if self._index_path.exists() else {}
        )

    def get(self, url: str) -> Response:
        response = self.inner.get(url)
        filename = f"{_key(url)}.txt"
        (self.dir / filename).write_text(response.body, encoding="utf-8")
        self._index[url] = {
            "file": filename,
            "status": response.status,
            "content_type": response.content_type,
            "fetched_at": response.fetched_at.isoformat(),
        }
        self._index_path.write_text(json.dumps(self._index, indent=2, sort_keys=True))
        return response

    def close(self) -> None:
        self.inner.close()


class AutoBackend:
    """Fixture first, live on miss (never in tests, which pin ``fixture``)."""

    def __init__(self) -> None:
        self.fixtures = FixtureBackend()
        self.live: LiveBackend | None = None

    def get(self, url: str) -> Response:
        if self.fixtures.has(url):
            return self.fixtures.get(url)
        if self.live is None:
            self.live = LiveBackend()
        return self.live.get(url)


# ═══════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════

_BACKEND: Backend | None = None


def get_backend(force: str | None = None) -> Backend:
    global _BACKEND
    mode = force or settings.mode
    if force is not None:
        return _build(mode)
    if _BACKEND is None:
        _BACKEND = _build(mode)
    return _BACKEND


def _build(mode: str) -> Backend:
    if mode == "fixture":
        return FixtureBackend()
    if mode == "live":
        return LiveBackend()
    if mode == "record":
        return RecordingBackend()
    return AutoBackend()


def reset_backend() -> None:
    """Tests only: drop the cached backend so a mode change takes effect."""
    global _BACKEND
    _BACKEND = None


# ═══════════════════════════════════════════════════════════════════════════
# QUERY BUILDING
# ═══════════════════════════════════════════════════════════════════════════

def build_url(base: str, params: dict[str, object] | None) -> str:
    """Deterministic URL construction — fixture keys depend on stability."""
    if not params:
        return base
    from urllib.parse import urlencode

    clean = {k: v for k, v in params.items() if v is not None}
    return f"{base}?{urlencode(clean)}"
