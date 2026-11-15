"""Wikipedia adapter (§5: CC BY-SA 4.0, EXCERPT + attribution).

The encyclopedia article is the *reference tier*, not the evidence base. It is
worth retrieving for two reasons: it names the questions (what the work is, when
it was written) and it points at the sources we actually want. It is never
treated as primary, and its claims are dated by the revision timestamp so a
2011 sentence and a 2026 sentence are distinguishable.
"""

from __future__ import annotations

import re

from src import rights as rights_mod
from src.models import (
    AuthorityTier,
    Source,
    SourceType,
    Work,
)
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import day, unknown

API = "https://en.wikipedia.org/w/api.php"


class WikipediaAdapter(SourceAdapter):
    name = "wikipedia"
    default_subtype = "encyclopedia_article"

    # ── search ────────────────────────────────────────────────────────────

    def search(self, query: str, work: Work | None = None, limit: int = 3) -> list[Hit]:
        hits: list[Hit] = []

        # A curated work knows its article title: deterministic, no search noise.
        title = (work.identifiers.get("wikipedia_title") if work else None)
        if title:
            hits.append(Hit(adapter=self.name, key=title, title=title,
                            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"))
            return hits[:limit]

        url = build_url(API, {
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": limit, "format": "json", "srprop": "snippet",
        })
        data = self._json(url)
        if not data:
            return []
        for item in data.get("query", {}).get("search", [])[:limit]:
            hits.append(Hit(
                adapter=self.name,
                key=item["title"],
                title=item["title"],
                url=f"https://en.wikipedia.org/wiki/{item['title'].replace(' ', '_')}",
                snippet=_strip_tags(item.get("snippet", "")),
                extra={"pageid": item.get("pageid")},
            ))
        return hits

    # ── fetch ─────────────────────────────────────────────────────────────

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        url = build_url(API, {
            "action": "query", "format": "json", "prop": "extracts|revisions|info",
            "explaintext": 1, "exsectionformat": "plain", "redirects": 1,
            "rvprop": "timestamp", "rvslots": "main", "titles": hit.key,
        })
        data = self._json(url)
        if not data:
            return None
        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values()), None)
        if not page or page.get("missing") is not None:
            self.errors.append(f"wikipedia: page missing for {hit.key}")
            return None

        text = _clean_extract(page.get("extract", ""))
        pageid = page.get("pageid", hit.extra.get("pageid", 0))
        revision = _revision_timestamp(page)

        return self.normalize(Source(
            id=f"{self.name}:{pageid}",
            work_id=work.id,
            type=SourceType.TERTIARY,
            subtype=self.default_subtype,
            title=page.get("title", hit.title),
            author="Wikipedia contributors",
            date_document=revision,
            url=f"https://en.wikipedia.org/?curid={pageid}",
            text=text,
            language="en",
            rights=self.rights({"page": page}),
            authority=AuthorityTier.REFERENCE,
        ))

    # ── policy ────────────────────────────────────────────────────────────

    def rights(self, payload: dict | None = None):  # noqa: D401
        return rights_mod.wikipedia_rights()

    def normalize(self, source: Source) -> Source:
        # Extracts carry section headings as bare lines and reference furniture
        # like "[edit]"; stripping them keeps spans honest.
        text = re.sub(r"\n{3,}", "\n\n", source.text)
        text = text.replace("[edit]", "")
        return source.model_copy(update={"text": text.strip()})


# ── helpers ───────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).replace("&quot;", '"').strip()


def _clean_extract(text: str) -> str:
    text = text.replace("\u200b", "")
    return text


def _revision_timestamp(page: dict):
    try:
        revs = page["revisions"][0]
        stamp = revs.get("timestamp", "")
        # "2024-03-11T18:22:01Z"
        return day(
            int(stamp[0:4]), int(stamp[5:7]), int(stamp[8:10]),
            display=stamp[0:10],
            confidence=1.0,
        )
    except (KeyError, IndexError, ValueError):
        return unknown("revision timestamp unavailable")
