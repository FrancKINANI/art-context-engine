"""The adapter contract (§4.2, §12.5).

Every adapter implements four things and nothing else:

    search(query) -> Hit[]        find candidate documents
    fetch(hit)    -> Source|None  retrieve one, with rights attached
    normalize()                   turn a payload into our model
    rights()                      state the licence, or return UNKNOWN

The contract exists to keep one promise: **an adapter cannot grant itself
rights**. If it cannot name the licence, it returns `UNKNOWN`, and the gate
(§3.4) stores metadata and renders a link. Adapters also cannot decide display
policy — they report a `RightsInfo` built from the §5 matrix; the gate decides.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.models import RightsInfo, Source, Work
from src.retrieval.http import Backend, FetchError, Response


@dataclass
class Hit:
    """A search result, before it is fetched."""

    adapter: str
    key: str                 # adapter-native identifier (pageid, QID, slug)
    title: str
    url: str | None = None
    snippet: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return f"{self.adapter}:{self.key}"


class AdapterError(RuntimeError):
    pass


class SourceAdapter(ABC):
    """Base class. Subclasses implement `search` and `fetch`."""

    name: str = "base"
    #: SourceType/AuthorityTier defaults; subclasses override
    default_subtype: str = "document"

    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.errors: list[str] = []

    # ── required ──────────────────────────────────────────────────────────

    @abstractmethod
    def search(self, query: str, work: Work | None = None, limit: int = 3) -> list[Hit]:
        """Return candidate documents. Must not raise: return [] and record."""

    @abstractmethod
    def fetch(self, hit: Hit, work: Work) -> Source | None:
        """Retrieve one document. Must not raise: return None and record."""

    # ── optional hooks ────────────────────────────────────────────────────

    def normalize(self, source: Source) -> Source:
        """Fix spelling, strip boilerplate, dedupe known furniture."""
        return source

    def rights(self, payload: dict | None = None) -> RightsInfo:
        """Licence determination. Default is fail-closed."""
        return RightsInfo.unknown()

    # ── helpers ───────────────────────────────────────────────────────────

    def _get(self, url: str) -> Response | None:
        try:
            response = self.backend.get(url)
        except FetchError as exc:
            self.errors.append(f"{self.name}: {exc}")
            return None
        except Exception as exc:  # network, DNS, timeout
            self.errors.append(f"{self.name}: {type(exc).__name__}: {exc}")
            return None
        if not response.ok:
            self.errors.append(f"{self.name}: HTTP {response.status} for {url}")
            return None
        return response

    def _json(self, url: str) -> dict | None:
        response = self._get(url)
        if response is None:
            return None
        try:
            return response.json()
        except FetchError as exc:
            self.errors.append(f"{self.name}: {exc}")
            return None
