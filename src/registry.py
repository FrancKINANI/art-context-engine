"""Curated work registry.

Entity resolution against a live knowledge graph is §4.1, but an MVP cannot
invent a work from a Wikipedia search alone: it needs to know *which* article,
QID, score page and song page all describe the same thing, and it needs the
work's own dating. That mapping is curated here, in `data/seed/works.json`,
and the resolver matches queries against it before falling back to search.

Curating three works is not a shortcut around §10.3 ("depth beats breadth"); it
is that decision made concrete.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache

from src.config import SEED_DIR
from src.models import CreatorRef, GeoRef, Work, WorkType
from src.temporal import century, day, from_range, month, unknown, year

WORKS_FILE = SEED_DIR / "works.json"


def _temporal(spec: dict | None):
    """Parse the compact JSON date form into a Temporal.

    Accepted shapes: {"year": 1821}, {"month": "1821-05"}, {"date": "1821-05-03"},
    {"start": 1821, "end": 1822}, {"decade": 1820}, {"century": 1821},
    {"unknown": "reason"}.
    """
    if spec is None:
        return None
    if "unknown" in spec:
        return unknown(spec["unknown"])
    if "start" in spec or "end" in spec:
        return from_range(spec.get("start"), spec.get("end"),
                          display=spec.get("display", ""))
    if "date" in spec:
        y, m, d = (int(x) for x in spec["date"].split("-"))
        return day(y, m, d, display=spec.get("display", ""))
    if "month" in spec:
        y, m = (int(x) for x in spec["month"].split("-"))
        return month(y, m, display=spec.get("display", ""))
    if "decade" in spec:
        return century_or_decade(spec)
    if "century" in spec:
        return century(spec["century"], display=spec.get("display", ""))
    if "year" in spec:
        return year(spec["year"], display=spec.get("display", ""))
    return unknown("unparsed date spec")


def century_or_decade(spec: dict):
    from src.temporal import decade as decade_t

    return decade_t(spec["decade"], display=spec.get("display", ""))


def _geo(spec: dict | None) -> GeoRef | None:
    if not spec:
        return None
    return GeoRef(
        name=spec["name"],
        wikidata_id=spec.get("wikidata_id"),
        country=spec.get("country"),
        lat=spec.get("lat"),
        lon=spec.get("lon"),
    )


def _creator(spec: dict) -> CreatorRef:
    return CreatorRef(
        name=spec["name"],
        wikidata_id=spec.get("wikidata_id"),
        role=spec.get("role", "creator"),
        birth_date=_temporal(spec.get("birth_date")),
        death_date=_temporal(spec.get("death_date")),
    )


def work_from_dict(spec: dict) -> Work:
    return Work(
        id=spec["id"],
        title=spec["title"],
        type=WorkType(spec["type"]),
        creators=[_creator(c) for c in spec.get("creators", [])],
        date_created=_temporal(spec.get("date_created")),
        date_published=_temporal(spec.get("date_published")),
        location_created=_geo(spec.get("location_created")),
        location_premiered=_geo(spec.get("location_premiered")),
        identifiers=spec.get("identifiers", {}),
        metadata={**spec.get("metadata", {}), "aliases": spec.get("aliases", [])},
    )


@lru_cache(maxsize=1)
def load_works() -> dict[str, Work]:
    if not WORKS_FILE.exists():
        return {}
    raw = json.loads(WORKS_FILE.read_text(encoding="utf-8"))
    return {spec["id"]: work_from_dict(spec) for spec in raw["works"]}


def get_work(work_id: str) -> Work | None:
    return load_works().get(work_id)


def all_works() -> list[Work]:
    return list(load_works().values())


def aliases_of(work: Work) -> list[str]:
    return list(work.metadata.get("aliases", []))
