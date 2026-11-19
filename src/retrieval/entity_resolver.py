"""Query → work candidates (§4.1).

Resolution returns **candidates with confidence**, never a silent guess. The most
expensive failure in this pipeline is answering confidently about the wrong
work, so the resolver is allowed to be unsure and is not allowed to be quiet
about it.

Three signals, in order of trust:

1. exact identifier (``beethoven-op111``, a QID, a Wikipedia URL)
2. curated alias match (``op 111``, ``tpab``)
3. token overlap with title + creator, with a penalty for unmatched tokens

A Wikipedia search is *not* a resolution signal. It suggests an article; it does
not tell us which work the user meant.

When the registry has nothing good, the resolver asks the open sources
(`open_works`) for candidates — a Deezer track, a Wikidata item, a Wikipedia
article. Those are candidates too, scored the same way and offered for a choice;
resolving one persists it so the document written about it stays readable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.models import Work, WorkCandidate
from src.retrieval.open_works import _FAMOUS, _says_only_title
from src.registry import aliases_of
from src.works import all_works, get_work

#: Below this, we ask instead of answering. A curated work found by exact title
#: scores 0.9+ and an open exact-title match 0.88; two shared words score around
#: 0.6, which is what "why does the second movement sound so plain" gets from a
#: cello symphony nobody asked about.
AMBIGUITY_THRESHOLD = 0.7
#: Above this, and with a clear lead, we can proceed without asking.
CONFIDENT_THRESHOLD = 0.85

_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "and", "to", "for", "why", "how",
    "does", "do", "is", "it", "sound", "so", "what", "when", "be", "was",
}


def normalize(text: str) -> str:
    """Casefold, strip accents and punctuation — 'Le Bateau ivre' ≈ 'bateau ivre'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in _STOPWORDS and len(t) > 1}


@dataclass
class Resolution:
    candidates: list[WorkCandidate]
    query: str

    @property
    def best(self) -> WorkCandidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def is_ambiguous(self) -> bool:
        if not self.candidates:
            return False
        if self.candidates[0].confidence < AMBIGUITY_THRESHOLD:
            return True
        if len(self.candidates) > 1:
            lead = self.candidates[0].confidence - self.candidates[1].confidence
            return lead < 0.15
        return False

    @property
    def is_confident(self) -> bool:
        best = self.best
        return bool(best) and best.confidence >= CONFIDENT_THRESHOLD and not self.is_ambiguous

    def work(self) -> Work | None:
        """The resolved work, only when the resolution is not ambiguous."""
        best = self.best
        if best is None or self.is_ambiguous:
            return None
        return get_work(best.work_id)


# ═══════════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════════

def _title_only(work: Work, query: str) -> bool:
    return _says_only_title(_title_like(query), work.title)


def _capped(score: float) -> float:
    """Order-preserving demotion: a bare title may rank, never conclude.

    Flattening every title match to the same 0.65 lost the ordering, so the one
    work the reader almost certainly meant came out level with a homonym.
    """
    return round(min(0.35 + 0.30 * max(0.0, min(1.0, score)), AMBIGUITY_THRESHOLD - 0.05), 3)


def _score_work(work: Work, query: str) -> tuple[float, str]:
    """Registry score, with one correction for works found by search.

    "Paname" is exact-title evidence that *something* called Paname matches, but
    not that the reader meant Booba's track: a bare title is not an identity, and
    every title has namesakes. Once a work was resolved online rather than
    curated, a query that says no more than its title stays below the ambiguity
    line and the reader is asked.
    """
    score, why = _score_work_raw(work, query)
    if not _is_curated(work) and _title_only(work, query):
        prominence = float((work.metadata or {}).get("prominence", 0) or 0)
        if prominence < _FAMOUS:
            return _capped(score), "title only — several works share it"
    return score, why


def _is_curated(work: Work) -> bool:
    from src.registry import get_work as curated_work

    return curated_work(work.id) is not None


def _score_work_raw(work: Work, query: str) -> tuple[float, str]:
    q = normalize(query)
    if not q:
        return 0.0, "empty query"

    # 1. identifier / exact title / exact alias
    if q == normalize(work.id):
        return 1.0, "id"
    title_norm = normalize(work.title)
    # "What does monalisa mean" names the work and asks about it. The question
    # around the title is not part of the identity.
    if q == title_norm or _title_only(work, query):
        return 0.98, "exact title"
    for alias in aliases_of(work):
        if q == normalize(alias):
            return 0.95, f"alias:{alias}"
    for key in ("wikidata_qid", "wikipedia_title", "genius_url"):
        value = work.identifiers.get(key)
        if value and q == normalize(value):
            return 1.0, f"identifier:{key}"

    # 2. containment — "why does beethoven op 111 sound like that"
    if title_norm and title_norm in q:
        return 0.9, "title in query"
    best_alias = None
    for alias in aliases_of(work):
        a = normalize(alias)
        if a and a in q:
            if best_alias is None or len(a) > len(normalize(best_alias)):
                best_alias = alias
    if best_alias:
        coverage = len(normalize(best_alias)) / max(len(q), 1)
        return min(0.9, 0.72 + 0.18 * coverage), f"alias in query:{best_alias}"

    # 3. token overlap
    query_tokens = _tokens(query)
    work_tokens = _tokens(
        " ".join([work.title] + [c.name for c in work.creators] + aliases_of(work))
    )
    if not query_tokens or not work_tokens:
        return 0.0, "no overlap"
    overlap = query_tokens & work_tokens
    if not overlap:
        return 0.0, "no overlap"

    recall = len(overlap) / len(work_tokens)      # how much of the work the query names
    precision = len(overlap) / len(query_tokens)  # how much of the query is about the work
    score = 0.6 * precision + 0.4 * recall
    # A single generic token ("sonata", "poem") is not a resolution.
    if len(overlap) == 1 and len(next(iter(overlap))) <= 4:
        score *= 0.5
    return min(0.84, score), f"tokens:{sorted(overlap)}"


def _order(candidate: WorkCandidate) -> tuple[float, int, int, int]:
    """Equal confidence is not a tie: the richer record goes first.

    When a bare title caps every candidate at the same score, what the reader
    sees is decided by this order — and a Wikidata item with its painter and its
    article is a better thing to offer than a Deezer track that happens to share
    the name.
    """
    return (
        candidate.confidence,
        int(candidate.creator not in ("", "unknown")),
        # A QID before a date: the item is an identity, the date is a fact about
        # a listing. Ordered the other way, a 2021 Deezer recording called
        # "Clair de Lune: I" was offered before Debussy's piece.
        int(bool(candidate.identifiers.get("wikidata_qid"))),
        int(bool(candidate.date_display)),
    )


def _candidate(work: Work, score: float, matched_on: str) -> WorkCandidate:
    creator = work.primary_creator.name if work.primary_creator else "unknown"
    return WorkCandidate(
        work_id=work.id,
        title=work.title,
        creator=creator,
        work_type=work.type,
        date_display=(work.date_created.display if work.date_created else ""),
        confidence=round(max(0.0, min(1.0, score)), 3),
        matched_on=matched_on,
        identifiers=work.identifiers,
    )


def resolve(query: str, *, allow_open: bool = True) -> Resolution:
    ranked: list[WorkCandidate] = []
    seen: set[str] = set()
    for work in all_works():
        score, why = _score_work(work, query)
        # A work persisted by an earlier resolution is a retrieval artefact, not
        # a curated identity. Left in the ranking it competes with fresh search
        # on generic token overlap and wins on the strength of having been shown
        # once — which is how a RocketBass track outranked Goya's painting for
        # every later reader. It may still answer an exact name.
        if not _is_curated(work) and score < 0.98:
            continue
        if score > 0.05:
            ranked.append(_candidate(work, score, why))
            seen.add(work.id)
    ranked.sort(key=_order, reverse=True)

    # The registry is a handful of works with hand-checked identifiers. A miss
    # is the normal case, not an error: ask the open sources, but only once the
    # registry has failed to be convincing.
    if allow_open and (not ranked or ranked[0].confidence < CONFIDENT_THRESHOLD):
        for candidate in _open_candidates(query):
            if candidate.work_id in seen:
                continue
            seen.add(candidate.work_id)
            ranked.append(candidate)
        ranked.sort(key=_order, reverse=True)
    ranked = _drop_parts(_dedupe_same_work(ranked))
    _demote_namesakes(ranked, query)
    return Resolution(candidates=ranked, query=query)


#: Words that mark a *version* of a work rather than another work: "Djadja
#: (feat. Maluma) (Remix)" is the song, not a second song.
_VERSION_WORDS = re.compile(
    r"\s*[\(\[]([^)\]]*\b(?:remix|edit|version|live|remaster(?:ed)?|acoustic|"
    r"demo|extended|radio|feat\.?|with)\b[^)\]]*)[\)\]]",
    re.IGNORECASE,
)


def _base_title(title: str) -> str:
    """The title with its version qualifiers removed, for comparison only."""
    previous = None
    stripped = title
    while previous != stripped:
        previous = stripped
        stripped = _VERSION_WORDS.sub("", stripped).strip()
    return normalize(stripped)


def _dedupe_same_work(ranked: list[WorkCandidate]) -> list[WorkCandidate]:
    """One work, one candidate.

    A track resolved once from a single and once from the album it appears on is
    the same song with two catalogue ids. Left alone, the copy that ties with
    itself makes every resolution ambiguous.
    """
    # An article that is the sitelink of an item we already have is the same
    # work with less information: "Mona Lisa" from a search box beside "Mona
    # Lisa" from Wikidata is one painting, not two choices.
    pinned = {
        normalize(c.identifiers.get("wikipedia_sitelink", ""))
        for c in ranked
        if c.identifiers.get("wikipedia_sitelink")
    }
    ranked = [
        c for c in ranked
        if not (c.identifiers.get("wikipedia_title")
                and normalize(c.identifiers["wikipedia_title"]) in pinned)
    ]

    kept: list[WorkCandidate] = []
    seen: dict[tuple[str, frozenset[str]], int] = {}
    for candidate in ranked:
        # The maker is compared as a bag of name tokens: Deezer files French
        # recitations under "Hugo Victor", and that single, identical poem then
        # tied with itself at 0.95 and made the question ambiguous.
        key = (_base_title(candidate.title), frozenset(normalize(candidate.creator).split()))
        if key in seen:
            index = seen[key]
            if _richer(candidate, kept[index]):
                kept[index] = candidate
            continue
        seen[key] = len(kept)
        kept.append(candidate)
    return kept


#: A title that names a part of another work: "Clair de Lune: I" is the first
#: movement of "Clair de lune", not a second piece by the same composer.
_PART_MARKER = re.compile(
    r"^(?:part|movement|act|volume|vol|no|n°)?\s*(?:[ivxlc]+|\d{1,2})$", re.IGNORECASE
)


def _is_part_of(title: str, whole: str) -> bool:
    a, b = normalize(title), normalize(whole)
    if not b or not a.startswith(b) or a == b:
        return False
    return bool(_PART_MARKER.fullmatch(a[len(b):].strip(" :-–—,.")))


def _drop_parts(ranked: list[WorkCandidate]) -> list[WorkCandidate]:
    """A part of a work is not a second work to choose between."""
    kept: list[WorkCandidate] = []
    for candidate in ranked:
        part_of = any(
            _is_part_of(candidate.title, other.title)
            and frozenset(normalize(candidate.creator).split())
            == frozenset(normalize(other.creator).split())
            for other in ranked
            if other is not candidate
        )
        if not part_of:
            kept.append(candidate)
    return kept


def _richer(candidate: WorkCandidate, current: WorkCandidate) -> bool:
    """Which of two records for the same work to keep.

    A Wikidata item is an identity — it carries the maker, the date and the
    article. A catalogue row is a listing. When both name the same work, the
    item wins; between two listings, the one using the work's own name wins.
    """
    return _weight(candidate) > _weight(current)


def _weight(candidate: WorkCandidate) -> tuple[int, int]:
    return (
        int(bool(candidate.identifiers.get("wikidata_qid"))),
        int(normalize(candidate.title) == _base_title(candidate.title)),
    )


def _names_maker(creator: str, query_tokens: set[str]) -> bool:
    """Does the query name this maker?

    "Virgile" and "Virgil" are the same poet in two languages, and a token
    comparison alone never sees it. Close enough is close enough: no two artists
    share a name that differs by one letter.
    """
    from difflib import SequenceMatcher

    for token in _tokens(creator):
        if token in query_tokens:
            return True
        for wanted in query_tokens:
            if len(token) > 4 and len(wanted) > 4:
                if SequenceMatcher(None, token, wanted).ratio() >= 0.85:
                    return True
    return False


def _demote_namesakes(ranked: list[WorkCandidate], query: str) -> None:
    """Two works called "Paname" are one title and two different songs.

    When the reader names the maker — "Booba, Paname" — the other maker's
    Paname is not a competing reading of the same work, it is a namesake. Left
    at the same score it would force a choice the reader already made.
    """
    query_tokens = _tokens(_title_like(query))
    if not query_tokens:
        return
    # A bare title names no maker. "Djadja" is a song, and the group "Djadja &
    # Dinaz" merely contains the word: reading that as the reader naming a maker
    # demoted the song they asked for to fourth place. A comma is how a
    # catalogue names a maker, so the rule cannot apply when one is present —
    # "Hokusai, The Great Wave" names Hokusai, even though a Deezer track called
    # "HOKUSAI ~The Great Wave~" contains every word of the query.
    head = _title_like(query)
    if "," not in head and any(_says_only_title(head, c.title) for c in ranked):
        return
    if not any(_names_maker(c.creator, query_tokens) for c in ranked if c.creator):
        return
    for candidate in ranked:
        if _names_maker(candidate.creator, query_tokens):
            continue
        candidate.confidence = round(candidate.confidence * 0.72, 3)
        candidate.matched_on = f"{candidate.matched_on} (different maker named)"
    ranked.sort(key=_order, reverse=True)


#: A question around a title: "what does monalisa mean". The interrogative and
#: the aspect it asks for are the reader's words, not the work's. Without this
#: split the resolver searched catalogues for "monalisa mean" and offered two
#: songs that happened to contain the word "mean".
_QUESTION = re.compile(
    r"^\s*(?:what|why|how|who|when|where)\s+"
    r"(?:does|do|did|is|are|was|were|can|could|should|would|means?)\b\s*"
    r"(?P<rest>.+)$",
    re.I,
)

#: Words that name the question, not the work. Stripped only from the end, so a
#: title that begins with one ("About a Boy") survives.
_ASPECT_WORDS = {
    "mean", "means", "meaning", "about", "say", "says", "saying", "show",
    "shows", "showing", "represent", "represents", "symbolise", "symbolize",
    "express", "expresses", "explain", "explains", "interpretation", "analysis",
}


def _title_like(query: str) -> str:
    """The part of a question that could be a title ("what does monalisa mean").

    The remaining text is kept byte for byte — punctuation and articles
    included — because catalogues are a lookup, and "2001: A Space Odyssey" is
    not found under "2001 A Space Odyssey".
    """
    head = re.split(r"[—–?]", query)[0].strip()
    match = _QUESTION.match(head)
    if match:
        head = match.group("rest").strip()
    while True:
        tail = re.search(r"[\s,;:]+([\w'’-]+)\s*$", head)
        if not tail or normalize(tail.group(1)) not in _ASPECT_WORDS:
            return head
        head = head[:tail.start()].strip()


def _focus(query: str) -> str:
    """The part of a question that names the work.

    A catalogue is a lookup, not a conversation: "Booba, Paname — what is he
    saying about Paris?" asks Deezer for a track called "Booba Paname saying
    about Paris" and gets nothing. The work is what comes before the dash; the
    rest is the question, and it belongs to the retriever, not the resolver.

    A colon is left alone: it separates a subtitle, and "2001: A Space Odyssey"
    is the film's name, not an instruction.
    """
    head = _title_like(query)
    words = [w for w in re.findall(r"[\w'’-]+", head) if normalize(w) not in _STOPWORDS]
    return " ".join(words[:8]) or head.strip()


def _subject(query: str) -> str:
    """The work's own title, when the reader wrote "maker, title".

    Wikidata is a catalogue of works, not of conversations: asking it for
    "Goya Saturn Devouring His Son" returns nothing, while "Saturn Devouring His
    Son" returns the painting with its painter attached. The maker does not
    belong in the lookup; it belongs in the choice between the hits.
    """
    head = _title_like(query)
    _, comma, rest = head.partition(",")
    # The title is kept exactly as written, stopwords and punctuation included:
    # Wikidata's search is a lookup, and "Girl with Pearl Earring" — article
    # dropped, colon removed — returns nothing at all for a painting.
    return (rest if comma else head).strip().rstrip(",.")


def _open_candidates(query: str) -> list[WorkCandidate]:
    """Candidates from Wikidata, Wikipedia and Deezer, ranked by their own score.

    They are persisted as they are offered: a candidate the reader clicks must
    still exist when the document about it is rendered, and re-fetching on every
    page view would make reading depend on three networks.
    """
    from src.retrieval import open_works
    from src.works import remember

    try:
        found = open_works.search(
            _focus(query), subject=_subject(query), context=query
        )
    except Exception:  # a resolver outage must not break the page
        return []

    # "Paname, Slimane" is two words and one meaning: the song, and the singer.
    # A search returns the singer's own article as an item called "Slimane", and
    # offering that as a *work* is asking the reader to choose between a song
    # and the person who sang it.
    head = re.split(r"[—–]|\?", query)[0]
    segments = {normalize(part) for part in head.split(",") if part.strip()}
    out: list[WorkCandidate] = []
    for hit in found:
        try:
            remember(hit.work)
        except Exception:
            pass
        if (
            len(segments) > 1
            and normalize(hit.work.title) in segments
            and hit.work.primary_creator is None
        ):
            continue
        score, why = hit.score, hit.why
        # "Paname" is a title, and a hundred works are called that. A bare title
        # carries no identity, so the resolver drops it below the ambiguity line
        # and asks: the alternative is answering about Booba's track when the
        # reader meant someone else's, or the city. The one exception is a work
        # the encyclopedias of the world agree on — "monalisa" is not a question
        # with an interesting answer.
        if _title_only(hit.work, query):
            prominence = float((hit.work.metadata or {}).get("prominence", 0) or 0)
            if prominence < _FAMOUS:
                score = _capped(score)
                why = "title only — several works share it"

        out.append(_candidate(hit.work, score, why))
    return out


def resolve_from_url(url: str) -> Resolution:
    """A pasted Wikipedia / Wikidata / Genius URL is the strongest signal we get."""
    lowered = url.strip().lower()
    qid = re.search(r"wikidata\.org/(?:wiki|entity)/(Q\d+)", lowered)
    if qid:
        return resolve(qid.group(1))
    title = re.search(r"wikipedia\.org/wiki/([^?#]+)", url)
    if title:
        from urllib.parse import unquote

        return resolve(unquote(title.group(1)).replace("_", " ").split(" (")[0])
    return resolve(url)
