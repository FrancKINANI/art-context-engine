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
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.models import Work, WorkCandidate
from src.registry import all_works, aliases_of

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
        from src.registry import get_work

        best = self.best
        if best is None or self.is_ambiguous:
            return None
        return get_work(best.work_id)


# ═══════════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════════

def _score_work(work: Work, query: str) -> tuple[float, str]:
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


def resolve(query: str) -> Resolution:
    ranked = []
    for work in all_works():
        score, why = _score_work(work, query)
        if score > 0.05:
            ranked.append(_candidate(work, score, why))
    ranked.sort(key=lambda c: c.confidence, reverse=True)
    return Resolution(candidates=ranked, query=query)


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
