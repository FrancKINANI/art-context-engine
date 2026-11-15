"""Evidence ranking (§4.6).

    score = 0.50 * tier + 0.30 * relevance + 0.10 * recency + 0.10 * displayable

The weights are config, not taste: they are tuned against the golden set (§10).
Two properties of this score are deliberate.

* **Tier dominates.** A primary letter that barely mentions the question still
  outranks an encyclopedia section that answers it neatly. The evidence for a
  claim about 1821 should come from 1821.
* **Displayable is a tiebreaker, not a filter.** A link-only source that is the
  best evidence still ranks highly; it just renders as a citation. Dropping it
  would let rights quietly decide the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.models import AuthorityTier, Source, Work
from src.rights import tier_weight
from src.temporal import distance_years, overlaps

WEIGHTS = {"tier": 0.50, "relevance": 0.30, "recency": 0.10, "displayable": 0.10}

_STOP = {
    "the", "a", "an", "of", "in", "on", "and", "is", "it", "to", "for", "does",
    "why", "how", "what", "that", "this", "with", "from", "by", "be", "so",
}


@dataclass
class RankedSource:
    source: Source
    score: float
    components: dict[str, float]
    matched_terms: list[str]


def rank(
    sources: list[Source],
    work: Work,
    question: str | None = None,
    claims_by_source: dict[str, int] | None = None,
) -> list[RankedSource]:
    terms = _terms(question or "")
    claims_by_source = claims_by_source or {}
    ranked: list[RankedSource] = []

    for source in sources:
        text_terms = _terms(source.title + " " + source.text[:8000])
        matched = sorted(terms & text_terms)
        relevance = _relevance(source, work, terms, matched, claims_by_source)
        components = {
            "tier": tier_weight(source.authority),
            "relevance": relevance,
            "recency": _recency(source, work),
            "displayable": 1.0 if source.rights.shows_text and source.text else 0.0,
        }
        score = sum(WEIGHTS[k] * v for k, v in components.items())
        ranked.append(RankedSource(source, round(score, 4), components, matched))

    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-zà-ÿ']{3,}", text.lower())
    return {w for w in words if w not in _STOP}


#: Public alias: relevance checks elsewhere (e.g. the assembler's "does this
#: reading answer this question?") must use the same notion of a content word.
content_terms = _terms


def _relevance(
    source: Source,
    work: Work,
    terms: set[str],
    matched: list[str],
    claims_by_source: dict[str, int],
) -> float:
    """How much of this document is about *this* question."""
    if not terms:
        # No question yet: relevance is "is this about the work at all".
        return 0.6 if _mentions_work(source, work) else 0.4

    coverage = len(matched) / len(terms)
    # A document whose claims answer the question ranks above one that merely
    # contains the word: claim overlap is the signal that survives paraphrase.
    claim_bonus = min(0.25, 0.08 * claims_by_source.get(source.id, 0))
    base = 0.55 if _mentions_work(source, work) else 0.2
    return min(1.0, 0.55 * coverage + base * 0.45 + claim_bonus)


def _mentions_work(source: Source, work: Work) -> bool:
    haystack = (source.title + " " + source.text[:4000]).lower()
    title_terms = _terms(work.title)
    if not title_terms:
        return True
    hits = sum(1 for t in title_terms if t in haystack)
    return hits >= max(1, len(title_terms) // 2)


def _recency(source: Source, work: Work) -> float:
    """Documents closer in time to the work rank higher (§4.6).

    A 1840 biography of an 1822 sonata beats a 2024 blog post for context, and
    an 1822 letter beats both. Distance is measured against the work's own
    dating, and unknown dates get the neutral 0.5 rather than 0.
    """
    anchor = work.date_created or work.date_published
    if anchor is None or source.date_document is None:
        return 0.5
    distance = distance_years(source.date_document, anchor)
    if distance is None:
        return 0.5 if overlaps(source.date_document, anchor) else 0.4
    return max(0.0, 1.0 - distance / 200.0)


def summarize(ranked: list[RankedSource]) -> str:
    lines = []
    for r in ranked:
        c = r.components
        lines.append(
            f"{r.score:0.3f} {r.source.authority.value:<20} {r.source.id[:40]:<42}"
            f" tier={c['tier']:.2f} rel={c['relevance']:.2f} rec={c['recency']:.2f}"
            f" disp={c['displayable']:.0f}"
        )
    return "\n".join(lines)
