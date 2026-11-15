"""Semantic Scholar adapter (§5: metadata open, abstracts per publisher).

This is the adapter that makes the scholarly tier reachable without a
partnership — which is also its limitation. We get an abstract, not an
argument, and an abstract is a claim the *author* made about their own paper,
not the paper's evidence. Claims extracted here are marked with lower
extractor confidence and the assembler is told to treat them as pointers.
"""

from __future__ import annotations

from src import rights as rights_mod
from src.models import AuthorityTier, Source, SourceType, Work
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import parse_human, unknown

API = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,authors,externalIds,url,openAccessPdf,venue"


class SemanticScholarAdapter(SourceAdapter):
    name = "semantic_scholar"
    default_subtype = "scholarly_article"

    def search(self, query: str, work: Work | None = None, limit: int = 3) -> list[Hit]:
        q = (work.identifiers.get("scholar_query") if work else None) or query
        data = self._json(build_url(API, {"query": q, "limit": limit, "fields": FIELDS}))
        if not data:
            return []
        return [
            Hit(
                adapter=self.name,
                key=paper.get("paperId", ""),
                title=paper.get("title", ""),
                url=paper.get("url"),
                snippet=(paper.get("abstract") or "")[:300],
                extra={"paper": paper},
            )
            for paper in data.get("data", [])[:limit]
        ]

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        paper = hit.extra.get("paper") or {}
        abstract = (paper.get("abstract") or "").strip()
        if not abstract:
            # A paper we cannot read contributes nothing at MVP; recording it as
            # a citation-only source would pad the source count dishonestly.
            self.errors.append(f"semantic_scholar: no abstract for {hit.key}")
            return None

        authors = ", ".join(a.get("name", "") for a in paper.get("authors", []))
        year = paper.get("year")
        date_document = parse_human(str(year)) if year else unknown("year missing")

        return Source(
            id=f"{self.name}:{hit.key}",
            work_id=work.id,
            type=SourceType.SECONDARY,
            subtype=self.default_subtype,
            title=paper.get("title", hit.title),
            author=authors or "unknown",
            date_document=date_document,
            url=paper.get("url"),
            text=abstract,
            language="en",
            rights=self.rights({"has_open_abstract": True}),
            authority=AuthorityTier.SCHOLARLY,
        )

    def rights(self, payload: dict | None = None):
        has_open = bool((payload or {}).get("has_open_abstract", True))
        return rights_mod.semantic_scholar_rights(has_open_abstract=has_open)
