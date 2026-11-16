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
from src.registry import aliases_of
from src.works import all_works, get_work

#: Below this, we ask instead of answering.
AMBIGUITY_THRESHOLD = 0.55
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

def _score_work(work: Work, query: str) -> tuple[float, str]:
    """Registry score, with one correction for works found by search.

    "Paname" is exact-title evidence that *something* called Paname matches, but
    not that the reader meant Booba's track: a bare title is not an identity, and
    every title has namesakes. Once a work was resolved online rather than
    curated, a query that says no more than its title stays below the ambiguity
    line and the reader is asked.
    """
    score, why = _score_work_raw(work, query)
    if not _is_curated(work):
        query_tokens = set(normalize(query).split())
        if query_tokens and query_tokens <= set(normalize(work.title).split()):
            return min(score, AMBIGUITY_THRESHOLD - 0.05), "title only — several works share it"
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
    if q == title_norm:
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
        if score > 0.05:
            ranked.append(_candidate(work, score, why))
            seen.add(work.id)
    ranked.sort(key=lambda c: c.confidence, reverse=True)

    # The registry is a handful of works with hand-checked identifiers. A miss
    # is the normal case, not an error: ask the open sources, but only once the
    # registry has failed to be convincing.
    if allow_open and (not ranked or ranked[0].confidence < CONFIDENT_THRESHOLD):
        for candidate in _open_candidates(query):
            if candidate.work_id in seen:
                continue
            seen.add(candidate.work_id)
            ranked.append(candidate)
        ranked.sort(key=lambda c: c.confidence, reverse=True)
    ranked = _dedupe_same_work(ranked)
    _demote_namesakes(ranked, query)
    return Resolution(candidates=ranked, query=query)


def _dedupe_same_work(ranked: list[WorkCandidate]) -> list[WorkCandidate]:
    """One work, one candidate.

    A track resolved once from a single and once from the album it appears on is
    the same song with two catalogue ids. Left alone, the copy that ties with
    itself makes every resolution ambiguous.
    """
    kept: list[WorkCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in ranked:
        key = (normalize(candidate.title), normalize(candidate.creator))
        if key in seen:
            continue
        seen.add(key)
        kept.append(candidate)
    return kept


def _demote_namesakes(ranked: list[WorkCandidate], query: str) -> None:
    """Two works called "Paname" are one title and two different songs.

    When the reader names the maker — "Booba, Paname" — the other maker's
    Paname is not a competing reading of the same work, it is a namesake. Left
    at the same score it would force a choice the reader already made.
    """
    q = normalize(query)
    if not any(c.creator and c.creator != "unknown" and normalize(c.creator) in q
               for c in ranked):
        return
    for candidate in ranked:
        if candidate.creator and candidate.creator != "unknown":
            if normalize(candidate.creator) in q:
                continue
        candidate.confidence = round(candidate.confidence * 0.72, 3)
        candidate.matched_on = f"{candidate.matched_on} (different maker named)"
    ranked.sort(key=lambda c: c.confidence, reverse=True)


def _focus(query: str) -> str:
    """The part of a question that names the work.

    "why does booba paname sound like that" searches badly on Deezer and
    Wikipedia; "booba paname" searches well. Content words only, and few of them.
    """
    words = [w for w in re.findall(r"[\w'’-]+", query) if normalize(w) not in _STOPWORDS]
    return " ".join(words[:8]) or query.strip()


def _open_candidates(query: str) -> list[WorkCandidate]:
    """Candidates from Wikidata, Wikipedia and Deezer, ranked by their own score.

    They are persisted as they are offered: a candidate the reader clicks must
    still exist when the document about it is rendered, and re-fetching on every
    page view would make reading depend on three networks.
    """
    from src.retrieval import open_works
    from src.works import remember

    try:
        found = open_works.search(_focus(query))
    except Exception:  # a resolver outage must not break the page
        return []

    query_tokens = set(normalize(query).split())
    out: list[WorkCandidate] = []
    for hit in found:
        try:
            remember(hit.work)
        except Exception:
            pass
        score, why = hit.score, hit.why
        # "Paname" is a title, and a hundred works are called that. A bare title
        # carries no identity, so the resolver drops it below the ambiguity line
        # and asks: the alternative is answering about Booba's track when the
        # reader meant someone else's, or the city.
        title_tokens = set(normalize(hit.work.title).split())
        if query_tokens and query_tokens <= title_tokens:
            score = min(score, AMBIGUITY_THRESHOLD - 0.05)
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
