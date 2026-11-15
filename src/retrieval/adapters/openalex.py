"""OpenAlex adapter (§5: scholarly, CC0 metadata).

The scholarly tier is what §10.6 requires before the engine is allowed to answer
at all, and it is the tier the MVP corpus is thinnest on (Grove and JSTOR are
out, §5). OpenAlex is the honest way to get there without a partnership: open
metadata, open abstracts where publishers allow them, and a DOI so the reader
can go to the paper itself.

OpenAlex returns abstracts as an inverted index (word → positions) for licensing
reasons. Reconstructing them is exact, not a paraphrase — which matters, because
these strings are the spans our claims will point at.
"""

from __future__ import annotations

from src import rights as rights_mod
from src.models import AuthorityTier, Source, SourceType, Work
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import parse_human, unknown

API = "https://api.openalex.org/works"
SELECT = "id,doi,title,publication_year,abstract_inverted_index,authorships,primary_location,type"


class OpenAlexAdapter(SourceAdapter):
    name = "openalex"
    default_subtype = "scholarly_article"

    def search(self, query: str, work: Work | None = None, limit: int = 3) -> list[Hit]:
        q = (work.identifiers.get("scholar_query") if work else None) or query
        data = self._json(build_url(API, {
            "search": q, "per-page": limit, "select": SELECT, "mailto": "julius@example.org",
        }))
        if not data:
            return []

        hits: list[Hit] = []
        for item in data.get("results", [])[:limit]:
            abstract = reconstruct_abstract(item.get("abstract_inverted_index"))
            if not abstract:
                # No abstract means nothing quotable; a citation we cannot quote
                # is not scholarly evidence (§10.6).
                continue
            hits.append(Hit(
                adapter=self.name,
                key=item.get("id", "").rsplit("/", 1)[-1],
                title=item.get("title", ""),
                url=(item.get("doi") or item.get("id")),
                snippet=abstract[:240],
                extra={"item": item, "abstract": abstract},
            ))
        if not hits:
            self.errors.append(f"openalex: no quotable results for {q!r}")
        return hits

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        item = hit.extra.get("item", {})
        abstract = hit.extra.get("abstract", "")
        if not abstract:
            return None

        authors = ", ".join(
            a.get("author", {}).get("display_name", "")
            for a in item.get("authorships", [])
            if a.get("author", {}).get("display_name")
        )
        year = item.get("publication_year")
        return Source(
            id=f"{self.name}:{hit.key}",
            work_id=work.id,
            type=SourceType.SECONDARY,
            subtype=self.default_subtype,
            title=item.get("title", hit.title),
            author=authors or "unknown",
            date_document=parse_human(str(year)) if year else unknown("year missing"),
            url=item.get("doi") or item.get("id"),
            text=abstract,
            language="en",
            rights=self.rights(),
            authority=AuthorityTier.SCHOLARLY,
        )

    def rights(self, payload: dict | None = None):
        return rights_mod.openalex_rights()


def reconstruct_abstract(inverted: dict | None) -> str:
    """Turn OpenAlex's word→positions index back into readable text."""
    if not inverted:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inverted.items():
        for pos in positions:
            slots[int(pos)] = word
    if not slots:
        return ""
    return " ".join(slots[i] for i in sorted(slots))
