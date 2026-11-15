"""Poetry Foundation adapter (§5: copyrighted texts, link only).

The Foundation's poem pages are the best-annotated free entry point for modern
poetry, and they are exactly the pages we may not reproduce. The adapter
confirms the poem exists, records title/author/date, and stops. Any close
reading of a copyrighted poem has to be written from critical sources *about*
the poem (scholarly tier, quoted under the excerpt policy) rather than from the
poem itself.
"""

from __future__ import annotations

import re

from src import rights as rights_mod
from src.models import AuthorityTier, Source, SourceType, Work
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import unknown

SEARCH = "https://www.poetryfoundation.org/search"


class PoetryFoundationAdapter(SourceAdapter):
    name = "poetry_foundation"
    default_subtype = "poem_page"

    def search(self, query: str, work: Work | None = None, limit: int = 2) -> list[Hit]:
        slug = (work.identifiers.get("poetryfoundation_slug") if work else None)
        if slug:
            return [Hit(adapter=self.name, key=slug, title=(work.title if work else slug),
                        url=f"https://www.poetryfoundation.org/poems/{slug}")]

        response = self._get(build_url(SEARCH, {"query": query}))
        if response is None:
            return []
        # The search page is JS-rendered; scrape whatever poem links exist.
        links = re.findall(r'href="(/poems/\d+/[a-z0-9-]+)"', response.body)
        seen, hits = set(), []
        for link in links:
            if link in seen:
                continue
            seen.add(link)
            hits.append(Hit(adapter=self.name, key=link.strip("/").split("/")[1],
                            title=link.rsplit("/", 1)[-1].replace("-", " "),
                            url=f"https://www.poetryfoundation.org{link}"))
            if len(hits) >= limit:
                break
        return hits

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        # Metadata only. Fetching the poem body would put us in possession of
        # text we are not allowed to store, so we do not fetch it.
        return Source(
            id=f"{self.name}:{hit.key}",
            work_id=work.id,
            type=SourceType.PRIMARY,
            subtype=self.default_subtype,
            title=hit.title or (work.title if work else "poem"),
            author=work.primary_creator.name if work.primary_creator else "unknown",
            date_document=work.date_published or unknown("publication date unrecorded"),
            url=hit.url,
            text="",
            language="en",
            rights=self.rights(),
            authority=AuthorityTier.REFERENCE,
        )

    def rights(self, payload: dict | None = None):
        return rights_mod.poetry_foundation_rights()
