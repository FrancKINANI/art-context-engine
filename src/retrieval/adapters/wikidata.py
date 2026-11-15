"""Wikidata adapter (§5: CC0, METADATA_ONLY).

Wikidata is the only source in the MVP that states facts *structurally*. That is
what makes it valuable for conflict detection — but it has no prose, so claims
extracted from it have no quotable span in the usual sense.

The resolution: the adapter serialises each relevant statement onto its own line
of `source.text`, e.g.

    P571  inception: 1822 (precision year)

Those lines are the span the claim points at. The gate then refuses to render
them (CC0 facts are METADATA_ONLY), so nothing but a citation to the item
reaches the reader. The span stays verifiable for evaluation, which is exactly
what §10 metric 1 asks of it.
"""

from __future__ import annotations

from src import rights as rights_mod
from src.models import AuthorityTier, Source, SourceType, Work
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import parse_wikidata_time, unknown

API = "https://www.wikidata.org/w/api.php"

#: Properties we care about in the MVP. Seeded, not discovered: a controlled
#: vocabulary is the main defence against false conflicts (§4.5.4).
DEFAULT_PROPERTIES: dict[str, str] = {
    "P571": "inception",
    "P577": "publication_date",
    "P86": "composer",
    "P50": "author",
    "P108": "employer",
    "P31": "instance_of",
    "P1476": "title",
    "P407": "language_of_work",
    "P1191": "first_performance",
    "P2635": "number_of_parts",
    "P7937": "form_of_creative_work",
    "P276": "location",
}


class WikidataAdapter(SourceAdapter):
    name = "wikidata"
    default_subtype = "structured_data"

    def __init__(self, backend, properties: dict[str, str] | None = None) -> None:
        super().__init__(backend)
        self.properties = properties or DEFAULT_PROPERTIES

    # ── search ────────────────────────────────────────────────────────────

    def search(self, query: str, work: Work | None = None, limit: int = 3) -> list[Hit]:
        qid = (work.identifiers.get("wikidata_qid") if work else None)
        if qid:
            return [Hit(adapter=self.name, key=qid, title=work.title,
                        url=f"https://www.wikidata.org/wiki/{qid}")]

        url = build_url(API, {
            "action": "wbsearchentities", "search": query, "language": "en",
            "uselang": "en", "limit": limit, "format": "json", "type": "item",
        })
        data = self._json(url)
        if not data:
            return []
        return [
            Hit(
                adapter=self.name,
                key=item["id"],
                title=item.get("label") or item["id"],
                url=f"https://www.wikidata.org/wiki/{item['id']}",
                snippet=item.get("description", ""),
            )
            for item in data.get("search", [])[:limit]
        ]

    # ── fetch ─────────────────────────────────────────────────────────────

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        url = build_url(API, {
            "action": "wbgetentities", "ids": hit.key, "format": "json",
            "props": "claims|labels|descriptions", "languages": "en",
        })
        data = self._json(url)
        if not data:
            return None
        entity = data.get("entities", {}).get(hit.key)
        if not entity or "missing" in entity:
            self.errors.append(f"wikidata: entity missing {hit.key}")
            return None

        statements = self._statements(entity)
        text = "\n".join(s["line"] for s in statements)
        label = entity.get("labels", {}).get("en", {}).get("value", hit.title)

        return self.normalize(Source(
            id=f"{self.name}:{hit.key}",
            work_id=work.id,
            type=SourceType.TERTIARY,
            subtype=self.default_subtype,
            title=f"{label} ({hit.key})",
            author="Wikidata contributors",
            date_document=unknown("undated"),
            url=f"https://www.wikidata.org/wiki/{hit.key}",
            text=text,
            language="en",
            rights=self.rights(),
            authority=AuthorityTier.REFERENCE,
        ).model_copy(update={"metadata": {"qid": hit.key, "statements": statements}}))

    def rights(self, payload: dict | None = None):
        return rights_mod.wikidata_rights()

    # ── normalization ─────────────────────────────────────────────────────

    def _statements(self, entity: dict) -> list[dict]:
        out: list[dict] = []
        claims = entity.get("claims", {})
        for prop, label in self.properties.items():
            for claim in claims.get(prop, []):
                value = _main_value(claim)
                if value is None:
                    continue
                time_raw = value.get("time")
                precision_code = value.get("precision")
                temporal = (
                    parse_wikidata_time(time_raw, precision_code)
                    if time_raw else None
                )
                display = (
                    temporal.display if temporal and temporal.earliest else _plain_value(value)
                )
                out.append({
                    "property": prop,
                    "property_label": label,
                    "line": f"{prop} {label}: {display} (precision {_precision_name(precision_code)})",
                    "value_text": display,
                    "value": {"label": label, "value": display},
                    "time_raw": time_raw,
                    "precision": precision_code,
                    "rank": claim.get("rank", "normal"),
                })
        return out


# ── helpers ───────────────────────────────────────────────────────────────

def _main_value(claim: dict) -> dict | None:
    try:
        return claim["mainsnak"]["datavalue"]["value"]
    except (KeyError, TypeError):
        return None


def _plain_value(value: dict) -> str:
    if "id" in value:
        return value["id"]
    if "text" in value:
        return value["text"]
    if "amount" in value:
        return value["amount"]
    if "time" in value:
        return value["time"]
    return str(value)


def _precision_name(code: int | None) -> str:
    return {11: "day", 10: "month", 9: "year", 8: "decade", 7: "century"}.get(
        code or 9, "year"
    )
