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

#: Things Wikidata calls items that are not works of art. A search for a work
#: returns the artist, the disambiguation page and a paperback edition of the
#: novel; none of them is the work, and a candidate list that offers them is
#: asking the reader to choose between a painting and a person.
_NOT_A_WORK = {
    "Q5",         # human
    "Q4167410",   # Wikimedia disambiguation page
    "Q4167836",   # Wikimedia category
    "Q3331189",   # edition
    "Q101352",    # family name
    "Q22808320",  # Wikimedia human name disambiguation page
}

#: Creator properties on Wikidata, in order of preference: the roles that define
#: the work come first, the incidental ones never win by being listed first.
#: A film has a composer, and the composer is not who made the film.
_CREATOR_PROPS = (("P86", "composer"), ("P50", "author"), ("P175", "performer"),
                  ("P170", "creator"), ("P57", "director"))

#: The role that identifies a work of each form. "Goya, Saturn Devouring His
#: Son" must resolve to the painting's painter, not to whoever scored a
#: documentary about it.
_DEFINING_ROLE = {
    WorkType.FILM: ("P57", "director"),
    WorkType.VISUAL_ART: ("P170", "creator"),
    WorkType.MUSIC: ("P175", "performer"),
    WorkType.LITERATURE: ("P50", "author"),
    WorkType.POETRY: ("P50", "author"),
    WorkType.THEATER: ("P50", "author"),
}

_STOPWORDS = {"the", "a", "an", "of", "in", "on", "and", "to", "for", "why", "how",
              "does", "do", "is", "it", "what", "when", "be", "was", "by"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in _STOPWORDS and len(t) > 1}


#: What the reader asked about, in their own words. A question about "the
#: painting" is not a question about a track that samples it.
_FORM_WORDS = {
    WorkType.VISUAL_ART: ("painting", "picture", "canvas", "portrait", "fresco",
                          "sculpture", "drawing"),
    WorkType.POETRY: ("poem", "poetry", "verse", "sonnet"),
    WorkType.FILM: ("film", "movie", "documentary"),
    WorkType.MUSIC: ("song", "track", "album", "single", "mixtape", "rap", "chorus"),
    WorkType.LITERATURE: ("novel", "book", "story", "essay"),
    WorkType.THEATER: ("play", "opera"),
}


#: A poem is a kind of literature: a question about "the poem" must not demote
#: the Wikidata item that calls it a literary work.
_FORM_FAMILIES = {
    WorkType.POETRY: {WorkType.POETRY, WorkType.LITERATURE},
    WorkType.LITERATURE: {WorkType.LITERATURE, WorkType.POETRY},
}


def _form_of(text: str) -> WorkType | None:
    """The art form the question names, if it names one."""
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    for work_type, vocabulary in _FORM_WORDS.items():
        if words & set(vocabulary):
            return work_type
    return None


def _maker_segment(context: str) -> str:
    """The part of the query before the first comma: "Vermeer, Girl with…".

    A catalogue is written maker-first. When the reader writes it that way, the
    maker is a constraint, not a word to match loosely — Deezer is full of tracks
    whose artist is "The Rap History of Art" and whose *title* is "Girl With a
    Pearl Earring (Vermeer, C.1665)".
    """
    head = re.split(r"[—–]|\?|:\s", context or "")[0]
    maker, comma, _ = head.partition(",")
    return maker if comma else ""


def _score(
    title: str,
    creator: str,
    query: str,
    declared: WorkType,
    context: str = "",
) -> float:
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

    context = context or query
    form = _form_of(context)
    if form is not None and declared not in _FORM_FAMILIES.get(form, {form}):
        # A track about Vermeer's painting is not the painting. Demoted, not
        # deleted: the reader may still pick it, but it cannot win by accident.
        score *= 0.7
    # An artist whose name is the whole query is a repackager, not the work:
    # "Stanley Kubrick — 2001 : A Space Odyssey — The Blue Danube (From The
    # Movie …)" is a waltz off a soundtrack, and it outranked the film for
    # "Kubrick, 2001: A Space Odyssey".
    creator_tokens = _tokens(creator)
    if q and creator_tokens and q <= creator_tokens:
        score *= 0.7

    maker = _tokens(_maker_segment(context))
    if maker and creator:
        if maker & _tokens(creator):
            score = min(1.0, score + 0.05)
        else:
            score *= 0.6
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
    #: The reader's question as written, used to constrain the score (form
    #: words, the maker before the comma) — never sent to the catalogue.
    context: str = ""
    hits: list[Candidate] = field(default_factory=list)

    def add(self, *, key: str, title: str, creator: str, work_type: WorkType,
            query: str, role: str = "author", temporal: Temporal | None = None,
            identifiers: dict[str, str] | None = None) -> None:
        title = (title or "").strip()
        if not title or len(title) < 2:
            return
        score = _score(title, creator, query, work_type, self.context)
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

def _wikidata(query: str, backend: Backend, limit: int, context: str = "") -> list[Candidate]:
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
        "props": "claims|labels|descriptions|sitelinks", "languages": "en|fr",
        "format": "json",
    }))
    entities = (entities_response.json().get("entities") or {})

    builder = _Builder("wikidata", context)
    for row in rows:
        qid = row.get("id")
        entity = entities.get(qid) or {}
        labels = entity.get("labels") or {}
        descriptions = entity.get("descriptions") or {}
        # The search row carries a label in a language the reader can read; the
        # entity may hold only another one (Wikidata often does), and showing
        # "2001, l'Odyssée de l'espace" for a search in English both reads oddly
        # and fails the title match. A Japanese fallback is neither: it is a
        # different record, and it is dropped rather than shown unreadable.
        match = row.get("match") or {}
        matched_in = match.get("language") or ""
        match_text = (match.get("text") or "").strip()
        entity_label = (labels.get("en") or labels.get("fr") or {}).get("value") or ""
        row_label = row.get("label") or ""
        # "mul" is Wikidata's multilingual label: the film Q103474 carries its
        # English title there, and rejecting it left "2001, l'Odyssée de
        # l'espace" as the only name we could score against an English query.
        #
        # The matched text is what the query actually hit. `row["label"]` is a
        # translation into the search language — searching "L'Albatros" returns
        # the poem labelled "The Albatross", which shares not one token with the
        # question and was dropped as a nameless stub while a Joe Dassin single
        # won the query.
        if matched_in in ("en", "fr", "mul"):
            label = (
                (labels.get(matched_in) or {}).get("value")
                or match_text
                or row_label
                or entity_label
            )
        else:
            label = entity_label
        description = (descriptions.get("en") or descriptions.get("fr") or {}).get("value") or row.get("description") or ""

        classes = {
            (claim.get("mainsnak", {}).get("datavalue") or {}).get("value", {}).get("id")
            for claim in (entity.get("claims", {}).get("P31") or [])
        }
        if classes & _NOT_A_WORK:
            continue

        work_type = WorkType.LITERATURE
        for claim in (entity.get("claims", {}).get("P31") or []):
            value = (claim.get("mainsnak", {}).get("datavalue") or {}).get("value") or {}
            if isinstance(value, dict) and value.get("id") in _INSTANCE_OF:
                work_type = _INSTANCE_OF[value["id"]]
                break
        else:
            work_type = _type_from(description, label)

        creator, role = "", "author"
        defining = _DEFINING_ROLE.get(work_type)
        order = ((defining,) if defining else ()) + tuple(
            pair for pair in _CREATOR_PROPS if pair != defining
        )
        for prop, role_name in order:
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

        identifiers = {"wikidata_qid": qid}
        sitelink = _wikipedia_sitelink(entity.get("sitelinks") or {})
        if sitelink is not None:
            identifiers["wikipedia_sitelink"], identifiers["wikipedia_lang"] = sitelink

        # A catalogue record that knows no maker, no date and has no article in
        # a language we serve is not a work we can say anything about — it is a
        # stub that collides with the real item on its title.
        sitelinks = entity.get("sitelinks") or {}
        if not creator and temporal is None and not ({"enwiki", "frwiki"} & set(sitelinks)):
            continue

        builder.add(key=qid, title=label, creator=creator, work_type=work_type,
                    query=query, role=role, temporal=temporal,
                    identifiers=identifiers)
    return builder.hits


#: Wikipedias we would rather read, in order. A work's own article is better
#: evidence than a search hit, and this is the only place that knows which
#: article is its own.
_PREFERRED_WIKIS = ("enwiki", "frwiki", "dewiki", "eswiki", "itwiki")


def _wikipedia_sitelink(sitelinks: dict) -> tuple[str, str] | None:
    """The work's own Wikipedia article, as (title, language), if it has one.

    Sitelinks are exact: this is the article Wikidata says is about this work,
    not the article a search box thought was closest. That distinction is what
    lets the adapter stop searching and just read the right page.
    """
    for key in _PREFERRED_WIKIS:
        link = sitelinks.get(key) or {}
        title = (link.get("title") or "").strip()
        if title:
            return title, key[: -len("wiki")]
    for key, link in sitelinks.items():
        if not key.endswith("wiki") or key.endswith(("commonswiki", "specieswiki", "metawiki")):
            continue
        title = ((link or {}).get("title") or "").strip()
        if title:
            return title, key[: -len("wiki")]
    return None


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


def _wikipedia(query: str, backend: Backend, limit: int, context: str = "") -> list[Candidate]:
    response = backend.get(build_url(WIKIPEDIA_API, {
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": str(limit), "format": "json",
    }))
    rows = (response.json().get("query") or {}).get("search") or []
    builder = _Builder("wikipedia", context)
    for row in rows:
        title = row.get("title") or ""
        snippet = re.sub(r"<[^>]+>", "", row.get("snippet") or "")
        builder.add(
            key=title, title=title, creator="",
            work_type=_type_from(snippet, title), query=query,
            identifiers={"wikipedia_title": title},
        )
    return builder.hits


def _deezer(query: str, backend: Backend, limit: int, context: str = "") -> list[Candidate]:
    response = backend.get(build_url(DEEZER_API, {"q": query, "limit": str(limit)}))
    rows = response.json().get("data") or []
    builder = _Builder("deezer", context)
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

def search(
    query: str,
    backend: Backend | None = None,
    limit: int = 6,
    *,
    subject: str = "",
    context: str = "",
) -> list[Candidate]:
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
    # Wikidata and Wikipedia are searched on the *subject* ("Girl with a Pearl
    # Earring") when the reader wrote "Vermeer, Girl with a Pearl Earring": a
    # catalogue lookup on the maker plus title finds nothing, and nothing is what
    # this pipeline used to get for every painting and poem. Deezer keeps the
    # whole query, because artist+title is how that index works.
    calls = (
        (_deezer, query),
        (_wikidata, subject or query),
        (_wikipedia, query),
    )
    for finder, text in calls:
        try:
            found.extend(finder(text, backend, limit, context or query))
        except (FetchError, ValueError, KeyError, TypeError):
            continue

    # The same work arrives from several catalogues — a song from Deezer and its
    # Wikidata item, a painting from Wikipedia and its QID. Title, form *and*
    # maker is the key. Without the maker, Léo Ferré's "Paname" and Booba's
    # "Paname" collapse into one record and the loser inherits the winner's
    # catalogue ids — a document about one song citing the other's release date.
    best: dict[tuple[str, str, str], Candidate] = {}
    for candidate in found:
        key = (
            normalize(candidate.work.title),
            candidate.work.type.value,
            normalize(candidate.work.primary_creator.name if candidate.work.primary_creator else ""),
        )
        current = best.get(key)
        if current is None:
            best[key] = candidate
        elif _rank(candidate) > _rank(current):
            best[key] = _with_identifiers(candidate, current)
        else:
            best[key] = _with_identifiers(current, candidate)
    return sorted(_merge_unenriched(best.values()), key=_rank, reverse=True)[:limit]


def _merge_unenriched(candidates) -> list[Candidate]:
    """One work, one row: a Wikidata item and the Wikipedia article it owns.

    The same painting arrives twice — once from Wikidata with its creator and
    QID, once from a Wikipedia search with neither. They are not two works, and
    showing both would ask the reader to choose between a painting and the same
    painting. They are merged only when one of them has no maker: two *named*
    makers mean two different works that share a title.
    """
    merged: list[Candidate] = []
    for candidate in sorted(candidates, key=_rank, reverse=True):
        for index, kept in enumerate(merged):
            same_title = normalize(kept.work.title) == normalize(candidate.work.title)
            if not same_title or kept.work.type != candidate.work.type:
                continue
            a = kept.work.primary_creator.name if kept.work.primary_creator else ""
            b = candidate.work.primary_creator.name if candidate.work.primary_creator else ""
            if a and b and normalize(a) != normalize(b):
                continue
            merged[index] = _with_identifiers(kept, candidate)
            break
        else:
            merged.append(candidate)
    return merged


def _with_identifiers(winner: Candidate, other: Candidate) -> Candidate:
    """Keep both catalogues' identifiers when two records are the same work.

    A track found on Deezer and in Wikidata is one work with a release date from
    one and a Wikipedia article from the other; throwing either away loses
    evidence for no reason. The exception is `wikipedia_title`: that key is what
    a *search* returned, which is a guess at the article, and a guess must not
    travel to a record that has a better identity.
    """
    merged = dict(winner.work.identifiers)
    for key, value in other.work.identifiers.items():
        if key == "wikipedia_title" or not value:
            continue
        merged.setdefault(key, value)
    if merged == winner.work.identifiers:
        return winner
    return Candidate(
        work=winner.work.model_copy(update={"identifiers": merged}),
        score=winner.score,
        why=winner.why,
    )


def _rank(candidate: Candidate) -> tuple[float, int, int, int]:
    """Score first, then whoever can say more about the work.

    A QID before a date: a Wikidata item is an identity, a release date is a
    fact about a record. Ranked the other way, three Deezer tracks with release
    dates pushed Guercino's and Poussin's paintings out of the six candidates
    offered for "Et in Arcadia ego".
    """
    work = candidate.work
    return (
        candidate.score,
        int(bool(work.creators)),
        int(bool(work.identifiers.get("wikidata_qid"))),
        int(bool(work.date_created)),
    )
