"""Resolving a work that is not in the curated registry (§4.1, widened).

The registry answers with certainty for a handful of works. Everything else —
a Booba track, a Vermeer, a poem nobody curated — has to be found somewhere, and
the only honest way to do it is to ask public sources for a *candidate*, score
it, and let the resolver treat it as one.

Three sources, in order of what they can give:

* **Wikidata** — the work's own identity: instance-of, creator, publication date,
  and the QID the other adapters can use.
* **Wikipedia** — an article, which is often the only prose about a song or a
  poem in a language we can read.
* **Deezer** — the catalogue facts for music: artist, album, release date. Rap
  and pop are poorly served by Wikidata, and this closes that gap without a key.

What this module deliberately does *not* do is invent. Every field it returns is
traceable to the response it came from; when a date is unknown the work has no
date, and the document shows "undated" rather than a plausible year.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from src.models import CreatorRef, Precision, Qualifier, Temporal, Work, WorkType
from src.retrieval.http import Backend, FetchError, build_url
from src.temporal import unknown

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
DEEZER_API = "https://api.deezer.com/search"
DEEZER_ALBUM = "https://api.deezer.com/album/"

#: Wikidata "instance of" → our work type. Only the classes that actually turn up
#: in a search for a work; anything else falls back to a keyword read of the
#: description, and then to LITERATURE.
_INSTANCE_OF = {
    "Q7366": WorkType.MUSIC,        # song
    "Q134556": WorkType.MUSIC,      # single
    "Q482994": WorkType.MUSIC,      # album
    "Q208569": WorkType.MUSIC,      # studio album
    "Q2188189": WorkType.MUSIC,     # musical work
    "Q5185279": WorkType.POETRY,    # poem
    "Q3305213": WorkType.VISUAL_ART,  # painting
    "Q11424": WorkType.FILM,        # film
    "Q7725634": WorkType.LITERATURE,  # literary work
    "Q47461344": WorkType.LITERATURE,  # written work
    "Q25379": WorkType.THEATER,     # play
}

_KEYWORDS = (
    (WorkType.MUSIC, ("song", "single", "album", "track", "mixtape", "composition",
                      "sonata", "symphony", "rap", "hip hop", "record")),
    (WorkType.POETRY, ("poem", "poetry", "verse", "sonnet")),
    (WorkType.VISUAL_ART, ("painting", "portrait", "fresco", "drawing", "sculpture")),
    (WorkType.FILM, ("film", "movie", "documentary")),
    (WorkType.LITERATURE, ("novel", "book", "short story", "essay")),
    (WorkType.THEATER, ("play", "opera", "musical theatre")),
)

#: Creator properties on Wikidata, in order of preference.
_CREATOR_PROPS = (("P86", "composer"), ("P50", "author"), ("P175", "performer"),
                  ("P170", "creator"), ("P57", "director"))

_STOPWORDS = {"the", "a", "an", "of", "in", "on", "and", "to", "for", "why", "how",
              "does", "do", "is", "it", "what", "when", "be", "was", "by"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in _STOPWORDS and len(t) > 1}


def _score(title: str, creator: str, query: str) -> float:
    """How well a candidate answers the query. Same idea as the registry scorer."""
    q = _tokens(query)
    if not q:
        return 0.0
    name = _tokens(f"{title} {creator}")
    if not name:
        return 0.0
    overlap = q & name
    if not overlap:
        return 0.0
    precision = len(overlap) / len(q)
    # Recall is measured against the *title*: a record that also knows the artist
    # must not be punished for it. Scoring against title+creator ranks a bare
    # "Las Meninas" book chapter above Velázquez's painting, which is backwards.
    recall = len(overlap & _tokens(title)) / max(len(_tokens(title)), 1)
    score = 0.72 * precision + 0.28 * recall
    if len(overlap) == 1 and len(next(iter(overlap))) <= 4:
        score *= 0.6
    # An exact title match inside the query is the strongest signal available
    # without an identifier — but only for the part of the query the title
    # explains. "Paname" inside "booba paname" is half an answer; the Deezer hit
    # that also knows the artist is the whole one.
    if normalize(title) and normalize(title) in normalize(query):
        score = max(score, 0.88 * precision)
    return min(0.95, score)


def _type_from(description: str, label: str) -> WorkType:
    haystack = f"{description} {label}".lower()
    for work_type, words in _KEYWORDS:
        if any(word in haystack for word in words):
            return work_type
    return WorkType.LITERATURE


def _year_temporal(year: int, *, display: str | None = None) -> Temporal:
    return Temporal(
        earliest=date(year, 1, 1),
        latest=date(year, 12, 31),
        precision=Precision.YEAR,
        qualifier=Qualifier.EXACT,
        display=display or str(year),
    )


def _date_temporal(raw: str, *, note: str = "") -> Temporal | None:
    """`2017-03-03`, `2017-03`, `2017` — precision follows what was given.

    ``note`` goes into the display string, because a catalogue date is often the
    date of a *release* and not of the work: Deezer dates "Paname" to the 2017
    reissue of the mixtape it sits on. Saying which date it is costs six
    characters and keeps the claim true.
    """
    raw = (raw or "").strip()
    match = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", raw)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2) or 0)
    day = int(match.group(3) or 0)
    if year < 1000 or year > 2100:
        return None
    if day:
        try:
            exact = date(year, month or 1, day)
        except ValueError:
            return _year_temporal(year)
        return Temporal(earliest=exact, latest=exact, precision=Precision.DAY,
                        qualifier=Qualifier.EXACT,
                        display=f"{exact.isoformat()}{note}")
    if month:
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1)
        return Temporal(earliest=start, latest=end, precision=Precision.MONTH,
                        qualifier=Qualifier.EXACT, display=f"{year}-{month:02d}{note}")
    return _year_temporal(year, display=f"{year}{note}")


@dataclass
class Candidate:
    work: Work
    score: float
    why: str


@dataclass
class _Builder:
    """Collects candidates from one source and drops the unreadable ones."""

    source: str
    hits: list[Candidate] = field(default_factory=list)

    def add(self, *, key: str, title: str, creator: str, work_type: WorkType,
            query: str, role: str = "author", temporal: Temporal | None = None,
            identifiers: dict[str, str] | None = None) -> None:
        title = (title or "").strip()
        if not title or len(title) < 2:
            return
        score = _score(title, creator, query)
        if score <= 0.05:
            return
        work = Work(
            id=f"{self.source}:{key}",
            title=title,
            type=work_type,
            creators=[CreatorRef(name=creator, role=role)] if creator else [],
            date_created=temporal,
            identifiers={**(identifiers or {})},
        )
        self.hits.append(Candidate(work=work, score=round(score, 3),
                                   why=f"{self.source} match"))


# ═══════════════════════════════════════════════════════════════════════════
# SOURCES
# ═══════════════════════════════════════════════════════════════════════════

def _wikidata(query: str, backend: Backend, limit: int) -> list[Candidate]:
    search = backend.get(build_url(WIKIDATA_API, {
        "action": "wbsearchentities", "search": query, "language": "en",
        "uselang": "en", "type": "item", "limit": str(limit), "format": "json",
    }))
    rows = search.json().get("search") or []
    if not rows:
        return []
    ids = [row["id"] for row in rows if row.get("id")]
    if not ids:
        return []

    entities_response = backend.get(build_url(WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(ids),
        "props": "claims|labels|descriptions", "languages": "en|fr",
        "format": "json",
    }))
    entities = (entities_response.json().get("entities") or {})

    builder = _Builder("wikidata")
    for row in rows:
        qid = row.get("id")
        entity = entities.get(qid) or {}
        labels = entity.get("labels") or {}
        descriptions = entity.get("descriptions") or {}
        label = (labels.get("en") or labels.get("fr") or {}).get("value") or row.get("label") or ""
        description = (descriptions.get("en") or descriptions.get("fr") or {}).get("value") or row.get("description") or ""

        work_type = WorkType.LITERATURE
        for claim in (entity.get("claims", {}).get("P31") or []):
            value = (claim.get("mainsnak", {}).get("datavalue") or {}).get("value") or {}
            if isinstance(value, dict) and value.get("id") in _INSTANCE_OF:
                work_type = _INSTANCE_OF[value["id"]]
                break
        else:
            work_type = _type_from(description, label)

        creator, role = "", "author"
        for prop, role_name in _CREATOR_PROPS:
            claims = entity.get("claims", {}).get(prop) or []
            if not claims:
                continue
            value = (claims[0].get("mainsnak", {}).get("datavalue") or {}).get("value") or {}
            if isinstance(value, dict) and value.get("id"):
                creator = _label_of(backend, value["id"])
                role = role_name
                if creator:
                    break

        temporal = None
        for prop in ("P577", "P571"):
            claims = entity.get("claims", {}).get(prop) or []
            if not claims:
                continue
            value = (claims[0].get("mainsnak", {}).get("datavalue") or {}).get("value") or {}
            raw = value.get("time", "") if isinstance(value, dict) else ""
            match = re.match(r"^[+-](\d{4})", raw or "")
            if match:
                temporal = _year_temporal(int(match.group(1)))
                break

        builder.add(key=qid, title=label, creator=creator, work_type=work_type,
                    query=query, role=role, temporal=temporal,
                    identifiers={"wikidata_qid": qid})
    return builder.hits


def _label_of(backend: Backend, qid: str) -> str:
    """One extra call, only when a candidate is about to be shown."""
    try:
        response = backend.get(build_url(WIKIDATA_API, {
            "action": "wbgetentities", "ids": qid, "props": "labels",
            "languages": "en|fr", "format": "json",
        }))
        labels = ((response.json().get("entities") or {}).get(qid) or {}).get("labels") or {}
        return (labels.get("en") or labels.get("fr") or {}).get("value", "")
    except (FetchError, KeyError, TypeError):
        return ""


def _wikipedia(query: str, backend: Backend, limit: int) -> list[Candidate]:
    response = backend.get(build_url(WIKIPEDIA_API, {
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": str(limit), "format": "json",
    }))
    rows = (response.json().get("query") or {}).get("search") or []
    builder = _Builder("wikipedia")
    for row in rows:
        title = row.get("title") or ""
        snippet = re.sub(r"<[^>]+>", "", row.get("snippet") or "")
        builder.add(
            key=title, title=title, creator="",
            work_type=_type_from(snippet, title), query=query,
            identifiers={"wikipedia_title": title},
        )
    return builder.hits


def _deezer(query: str, backend: Backend, limit: int) -> list[Candidate]:
    response = backend.get(build_url(DEEZER_API, {"q": query, "limit": str(limit)}))
    rows = response.json().get("data") or []
    builder = _Builder("deezer")
    for row in rows[:limit]:
        title = row.get("title") or ""
        artist = ((row.get("artist") or {}).get("name")) or ""
        album = row.get("album") or {}
        temporal = None
        album_id = album.get("id")
        if album_id:
            try:
                detail = backend.get(f"{DEEZER_ALBUM}{album_id}").json()
                temporal = _date_temporal(detail.get("release_date") or "",
                                          note=" · album release")
            except (FetchError, ValueError):
                temporal = None
        builder.add(
            key=str(row.get("id") or title), title=title, creator=artist,
            work_type=WorkType.MUSIC, query=query, role="performer",
            temporal=temporal,
            identifiers={"deezer_id": str(row.get("id") or ""),
                         "deezer_album": str(album.get("title") or "")},
        )
    return builder.hits


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def search(query: str, backend: Backend | None = None, limit: int = 6) -> list[Candidate]:
    """Candidates from every open source, merged, deduped and ranked.

    Failures are per-source: a Wikidata outage must not cost the reader the
    Deezer hit that would have identified the track.
    """
    from src.config import settings

    if not settings.allow_live:
        return []
    if backend is None:
        from src.retrieval.http import get_backend

        backend = get_backend()

    found: list[Candidate] = []
    for finder in (_deezer, _wikidata, _wikipedia):
        try:
            found.extend(finder(query, backend, limit))
        except (FetchError, ValueError, KeyError, TypeError):
            continue

    # The same work arrives from several catalogues — a song from Deezer and its
    # Wikidata item, a painting from Wikipedia and its QID. Title *and form* is
    # the key: "Las Meninas" is a painting and a song, and collapsing them would
    # hide one behind the other.
    best: dict[tuple[str, str], Candidate] = {}
    for candidate in found:
        key = (normalize(candidate.work.title), candidate.work.type.value)
        current = best.get(key)
        if current is None or _rank(candidate) > _rank(current):
            best[key] = candidate
    return sorted(best.values(), key=_rank, reverse=True)[:limit]


def _rank(candidate: Candidate) -> tuple[float, int, int, int]:
    """Score first, then whoever can say more about the work."""
    work = candidate.work
    return (
        candidate.score,
        int(bool(work.creators)),
        int(bool(work.date_created)),
        int(bool(work.identifiers.get("wikidata_qid"))),
    )
