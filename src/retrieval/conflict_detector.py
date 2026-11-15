"""ExtractedClaim[] → Conflict[] (§4.5).

Stance: **precision over recall**. A missed conflict is a disappointment; an
invented one is a lie, and a lie costs the reader's trust in every other card.
So everything here is a filter, and every filter has a reason string you can
read in the review queue.

The detection order matters and is not arbitrary:

1. **blocking** — compare only within the same (subject, predicate) block. Two
   true statements about different things are not a conflict.
2. **normalization** — before comparing, make the values comparable (units,
   number words, name forms, date precision). Equal after normalization means
   no conflict, full stop.
3. **incompatibility** — values differ *and* the difference is not explained by
   scope: qualifier ("allegedly"), time ("the fact changed"), or document
   (a source contradicting itself is a review item, not a card).
4. **adjudication** — classify the type, score it.
5. **threshold** — only then does it reach a reader.

§4.5.1's distinction between "conflict" and "not a conflict" is load-bearing:
`INTERPRETIVE` differences render as *readings differ*, and `NULL` (silence)
renders as a gap. Calling either of those an error is the mistake this module is
built to avoid.
"""

from __future__ import annotations

import re
from itertools import combinations

from src.models import (
    AuthorityTier,
    Conflict,
    ConflictCandidate,
    ConflictKind,
    ExtractedClaim,
    Polarity,
    Position,
    Resolution,
    Severity,
    Source,
    Span,
    Temporal,
)
from src.retrieval.claim_extractor import predicate_spec
from src.temporal import relation, Relation

# ── Thresholds (§4.5.2 step 5) ──────────────────────────────────────────────
MIN_CLAIM_CONFIDENCE = 0.80
MIN_ADJUDICATOR_CONFIDENCE = 0.70
#: Below this, an extraction is a guess about a guess and never worth comparing.
#: Between here and MIN_CLAIM_CONFIDENCE, a disagreement becomes a *candidate*
#: with a reason, rather than being silently dropped — curators can see it.
MIN_BLOCK_CONFIDENCE = 0.50
#: Qualifiers that usually mean "two claims scoped differently", not a dispute.
HEDGE_MARKERS = ("allegedly", "according to", "reportedly", "some editions",
                 "in some editions", "it is said", "supposedly")
#: Reading predicates are interpretive by construction.
READING_PREDICATES = {"reception", "interpretation"}


def detect(
    claims: list[ExtractedClaim],
    sources: dict[str, Source],
) -> tuple[list[Conflict], list[ConflictCandidate]]:
    """Return (reader-visible conflicts, curator-only candidates)."""
    conflicts: list[Conflict] = []
    candidates: list[ConflictCandidate] = []

    for (subject, predicate), group in _blocks(claims).items():
        if len(group) < 2:
            continue
        spec = predicate_spec(predicate)
        kind = _kind_from_vocab(spec, predicate)
        involved: list[ExtractedClaim] = []
        reasons: set[str] = set()

        for a, b in combinations(group, 2):
            verdict, reason = _incompatible(a, b, predicate)
            if verdict is None:
                continue
            if verdict == "candidate":
                reasons.add(reason)
                continue
            involved.extend([a, b])

        if not involved:
            if reasons:
                candidates.append(ConflictCandidate(
                    kind=kind, subject=subject, predicate=predicate,
                    claim_ids=[c.id for c in group],
                    reason="; ".join(sorted(reasons)),
                ))
            continue

        unique = _dedupe(involved)
        failure = _threshold_failure(unique, sources)
        if failure:
            candidates.append(ConflictCandidate(
                kind=kind, subject=subject, predicate=predicate,
                claim_ids=[c.id for c in unique], reason=failure,
                confidence=_adjudicator_confidence(unique),
            ))
            continue

        conflicts.append(_build_conflict(subject, predicate, unique, sources, kind, spec))

    return conflicts, candidates


# ═══════════════════════════════════════════════════════════════════════════
# 1. BLOCKING
# ═══════════════════════════════════════════════════════════════════════════

def _blocks(claims: list[ExtractedClaim]) -> dict[tuple[str, str], list[ExtractedClaim]]:
    blocks: dict[tuple[str, str], list[ExtractedClaim]] = {}
    for claim in claims:
        if claim.extractor_confidence < MIN_BLOCK_CONFIDENCE:
            continue
        if claim.polarity == Polarity.DENY:
            # A denial is compared against an assertion of the same fact.
            pass
        blocks.setdefault((claim.subject, claim.predicate), []).append(claim)
    return blocks


def _dedupe(claims: list[ExtractedClaim]) -> list[ExtractedClaim]:
    seen: dict[str, ExtractedClaim] = {}
    for claim in claims:
        seen.setdefault(claim.id, claim)
    # Prefer the highest-authority claim per source so a source appears once.
    by_source: dict[str, ExtractedClaim] = {}
    for claim in seen.values():
        current = by_source.get(claim.source_id)
        if current is None or claim.extractor_confidence > current.extractor_confidence:
            by_source[claim.source_id] = claim
    return list(by_source.values())


# ═══════════════════════════════════════════════════════════════════════════
# 2 & 3. NORMALIZATION + INCOMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════

def _incompatible(a: ExtractedClaim, b: ExtractedClaim, predicate: str) -> tuple[str | None, str]:
    """Return ('conflict'|'candidate'|None, reason)."""
    # A document contradicting itself is real but rare, and usually an
    # extraction error; it goes to review first (§4.5.4).
    if a.source_id == b.source_id:
        return "candidate", "same_document"

    # Two low-confidence readings are not evidence of anything.
    if min(a.extractor_confidence, b.extractor_confidence) < MIN_CLAIM_CONFIDENCE:
        return "candidate", "low extractor confidence"

    if _hedged(a) or _hedged(b):
        return "candidate", "qualifier_scope"

    a_t, b_t = _temporal(a), _temporal(b)
    if a_t and b_t:
        rel = relation(a_t, b_t)
        if rel == Relation.OVERLAP:
            return None, ""
        if rel == Relation.UNKNOWN:
            return None, ""
        return "conflict", "temporal_disjoint"

    if predicate in READING_PREDICATES:
        # Two readings are only a *rendered* pair when a curator says they are
        # readings of the same thing. The MVP detector cannot tell a genuine
        # disagreement from two paragraphs that merely sit in the same block, so
        # it queues them instead of showing an invented opposition.
        if _same_text(a, b):
            return None, ""
        return "candidate", "interpretive_requires_review"

    a_v, b_v = _normalized(a), _normalized(b)
    if a_v == b_v:
        return None, ""
    if a_v is None or b_v is None:
        return None, ""
    # A longer courtesy phrasing is not a different fact: "Archduke Rudolf" and
    # "his friend, pupil, and patron, Archduke Rudolf" name one dedicatee.
    if isinstance(a_v, set) and isinstance(b_v, set) and a_v and b_v:
        if a_v <= b_v or b_v <= a_v:
            return None, ""
    return "conflict", "values_differ"


def _hedged(claim: ExtractedClaim) -> bool:
    haystack = " ".join([claim.value_text, *claim.qualifiers.values()]).lower()
    return any(marker in haystack for marker in HEDGE_MARKERS)


def _temporal(claim: ExtractedClaim) -> Temporal | None:
    if claim.about is not None:
        return claim.about
    value = claim.value or {}
    if not isinstance(value, dict) or "earliest" not in value:
        return None
    from datetime import date as date_cls

    from src.models import Precision, Qualifier

    try:
        return Temporal(
            earliest=date_cls.fromisoformat(value["earliest"]) if value.get("earliest") else None,
            latest=date_cls.fromisoformat(value["latest"]) if value.get("latest") else None,
            precision=Precision(value.get("precision") or "UNKNOWN"),
            qualifier=Qualifier(value.get("qualifier") or "UNKNOWN"),
        )
    except (ValueError, KeyError):
        return None


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _normalized(claim: ExtractedClaim):
    """Comparable value: numbers as numbers, text casefolded and stripped."""
    value = claim.value
    if isinstance(value, dict):
        if "count" in value:
            return int(value["count"])
        if "bpm" in value:
            return (int(value["bpm"]), value.get("beat_unit", "quarter"))
        if "text" in value:
            text = _normalize_text(value["text"])
            # "Archduke Rudolf" vs "his friend, pupil, and patron, Archduke
            # Rudolf" are the same dedication written at different lengths.
            return set(text.split()) if text else None
    return value


_FUNCTION_WORDS = {
    "the", "a", "an", "of", "in", "on", "and", "or", "is", "are", "was",
    "were", "it", "its", "to", "for", "with", "by", "that", "this", "as",
    "at", "from", "has", "have", "had", "be", "been", "not", "no", "but",
}


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    words = [w for w in text.split() if w not in _FUNCTION_WORDS]
    return " ".join(words)


def _same_text(a: ExtractedClaim, b: ExtractedClaim) -> bool:
    """Lenient text equality for readings: share the distinctive words?"""
    a_set = _normalized(a)
    b_set = _normalized(b)
    if not isinstance(a_set, set) or not isinstance(b_set, set):
        return a_set == b_set
    if not a_set or not b_set:
        return False
    overlap = len(a_set & b_set) / min(len(a_set), len(b_set))
    return overlap > 0.6


# ═══════════════════════════════════════════════════════════════════════════
# 4 & 5. ADJUDICATION + THRESHOLD
# ═══════════════════════════════════════════════════════════════════════════

def _kind_from_vocab(spec: dict, predicate: str) -> ConflictKind:
    if predicate in READING_PREDICATES:
        return ConflictKind.INTERPRETIVE
    raw = spec.get("conflict_kind", "FACTUAL")
    try:
        return ConflictKind(raw)
    except ValueError:
        return ConflictKind.FACTUAL


def _adjudicator_confidence(claims: list[ExtractedClaim]) -> float:
    if not claims:
        return 0.0
    return min(c.extractor_confidence for c in claims)


def _threshold_failure(claims: list[ExtractedClaim], sources: dict[str, Source]) -> str | None:
    if len({c.source_id for c in claims}) < 2:
        return "fewer than two independent sources"
    for claim in claims:
        if claim.extractor_confidence < MIN_CLAIM_CONFIDENCE:
            return "low extractor confidence"
        if not claim.span.text.strip():
            return "unverified span"
        source = sources.get(claim.source_id)
        if source is None:
            return "unknown source"
        if claim.span.resolve(source.text) != claim.span.text:
            return "span does not match stored text"
    if _adjudicator_confidence(claims) < MIN_ADJUDICATOR_CONFIDENCE:
        return "adjudicator confidence below threshold"
    return None


def _build_conflict(
    subject: str,
    predicate: str,
    claims: list[ExtractedClaim],
    sources: dict[str, Source],
    kind: ConflictKind,
    spec: dict,
) -> Conflict:
    positions = []
    for claim in sorted(claims, key=lambda c: _authority_rank(sources.get(c.source_id))):
        source = sources[claim.source_id]
        positions.append(Position(
            claim_id=claim.id,
            source_id=claim.source_id,
            source_title=source.title,
            authority=source.authority,
            value=claim.value,
            value_text=claim.value_text,
            span=claim.span,
            about=claim.about,
            qualifiers=claim.qualifiers,
        ))

    return Conflict(
        kind=kind,
        subject=subject,
        predicate=predicate,
        positions=positions,
        question=spec.get("question", f"{predicate.replace('_', ' ')}: {subject}"),
        resolution=Resolution.UNRESOLVED,   # MVP ships unresolved, always
        severity=Severity(spec.get("severity", "MINOR")),
        confidence=_adjudicator_confidence(claims),
        detector="rules-v1",
    )


def _authority_rank(source: Source | None) -> int:
    order = {
        AuthorityTier.PRIMARY: 0,
        AuthorityTier.SCHOLARLY: 1,
        AuthorityTier.CONTEMPORARY_REVIEW: 2,
        AuthorityTier.REFERENCE: 3,
    }
    return order.get(source.authority, 9) if source else 9


# ═══════════════════════════════════════════════════════════════════════════
# FIXTURE ENTRY POINT (§10.5)
# ═══════════════════════════════════════════════════════════════════════════

def detect_pair(a: ExtractedClaim, b: ExtractedClaim, predicate: str) -> tuple[str | None, str]:
    """Exposed for the adversarial fixtures: one pair in, one verdict out."""
    return _incompatible(a, b, predicate)
