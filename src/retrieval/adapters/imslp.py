"""IMSLP adapter (§5: per-file status, mostly PD; MVP displays links).

IMSLP is where the *score* lives, and the score is the primary document for a
musical close reading. But "the score is public domain" is a per-file claim
about a 19th-century print, not about the wiki page that describes it, and the
IMSLP page text carries its own licence which we have not verified.

So the adapter is deliberately narrow: it returns the work page as a
`LINK_ONLY` source — enough to cite ("IMSLP lists five editions"), never enough
to quote. This is the fail-closed rule (§3.4) applied to the source we most
want to be permissive about.
"""

from __future__ import annotations

import re

from src import rights as rights_mod
from src.models import AuthorityTier, Source, SourceType, Work
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import unknown

API = "https://imslp.org/api.php"


class IMSLPAdapter(SourceAdapter):
    name = "imslp"
    default_subtype = "score_edition"

    def search(self, query: str, work: Work | None = None, limit: int = 2) -> list[Hit]:
        page = (work.identifiers.get("imslp_page") if work else None)
        if page:
            return [Hit(adapter=self.name, key=page, title=page,
                        url=f"https://imslp.org/wiki/{page.replace(' ', '_')}")]
        url = build_url(API, {
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": limit, "format": "json",
        })
        data = self._json(url)
        if not data:
            return []
        return [
            Hit(adapter=self.name, key=item["title"], title=item["title"],
                url=f"https://imslp.org/wiki/{item['title'].replace(' ', '_')}")
            for item in data.get("query", {}).get("search", [])[:limit]
        ]

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        url = build_url(API, {
            "action": "query", "format": "json", "prop": "extracts|info",
            "explaintext": 1, "titles": hit.key, "redirects": 1,
        })
        data = self._json(url)
        if not data:
            return None
        page = next(iter(data.get("query", {}).get("pages", {}).values()), None)
        if not page or page.get("missing") is not None:
            self.errors.append(f"imslp: page missing {hit.key}")
            return None

        # We keep the metadata, never the body. `text` stays empty on purpose.
        return Source(
            id=f"{self.name}:{_slug(hit.key)}",
            work_id=work.id,
            type=SourceType.PRIMARY,
            subtype=self.default_subtype,
            title=page.get("title", hit.key),
            author="IMSLP / Petrucci Music Library",
            date_document=unknown("wiki page has no stable publication date"),
            url=f"https://imslp.org/wiki/{hit.key.replace(' ', '_')}",
            text="",  # fail-closed: nothing stored, nothing quotable
            language="en",
            rights=self.rights(),
            authority=AuthorityTier.REFERENCE,
        )

    def rights(self, payload: dict | None = None):
        return rights_mod.imslp_rights(public_domain_file=False)


def _slug(page: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", page).strip("-").lower()
