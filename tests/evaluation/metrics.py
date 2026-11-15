"""Metric implementations for §10.2.

Every metric here is computed from the answer object and the pack it was built
from — never from a re-run with different settings. Two of them deserve a note
because they are approximations:

* **required-claim recall** uses lexical overlap, not a judge model. It is
  conservative: it will miss a correct paraphrase that shares few words, so the
  score under-reports rather than flatters. When the LLM assembler lands, the
  same contract gets a semantic judge (§10.3) and this stays as the floor.
* **temporal fidelity** checks that a rendered date is consistent with its
  declared precision (no day-precision string on a year-precision dating). It
  cannot know whether the source really said 1821; that is what the span check
  in metric 1 covers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.answer.claims import from_pack, verify_claim
from src.models import (
    Answer,
    BlockKind,
    Conflict,
    ContextPack,
    Precision,
    Source,
    Sufficiency,
)

_STOP = {
    "the", "a", "an", "of", "in", "on", "and", "or", "is", "are", "was", "were",
    "it", "its", "to", "for", "with", "by", "that", "this", "as", "at", "from",
    "has", "have", "had", "be", "been", "not", "no", "but", "which", "who",
}


#: "two movements" and "2 movements" are the same claim; a matcher that cannot
#: see that would report a recall failure for a document that is correct.
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "twenty": "20", "twenty-five": "25",
    "hundred": "100",
}


def words(text: str) -> set[str]:
    lowered = (text or "").lower()
    for word, digits in _NUMBER_WORDS.items():
        lowered = re.sub(rf"\b{re.escape(word)}\b", digits, lowered)
    # Digits are kept at any length: "2 movements" vs "3 movements" is exactly
    # the difference a hallucination check must see.
    return {
        w for w in re.findall(r"[a-zà-ÿ]{2,}|\d+", lowered)
        if w not in _STOP
    }


def overlap(required: str, candidate: str) -> float:
    """Coverage: how much of the required claim the document contains."""
    req = words(required)
    if not req:
        return 1.0
    return len(req & words(candidate)) / len(req)


def jaccard(a: str, b: str) -> float:
    set_a, set_b = words(a), words(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def asserts(statement: str, document: str, threshold: float = 0.66) -> bool:
    """Does the document *assert* this statement?

    Coverage is the wrong test for forbidden claims: "the sonata has four
    movements" would score high against "it has two movements" purely because
    both sentences are about movements. Jaccard against the best-matching
    sentence is stricter — the differing number shrinks the intersection and
    grows the union — which is what a hallucination detector needs.
    """
    sentences = re.split(r"(?<=[.!?])\s+|\n+", document)
    return any(jaccard(statement, sentence) >= threshold for sentence in sentences)


def document_text(answer: Answer, pack: ContextPack) -> str:
    """Everything a reader would see, as one string.

    Blocks only: `Answer.thesis` is a copy of the thesis block, and counting it
    twice would let one sentence satisfy a requirement it shares with itself.
    """
    parts = [block.text for block in answer.blocks] or [answer.thesis]
    for event in answer.timeline:
        parts.append(f"{event.title} {event.when.display or ''} {event.description}")
    for conflict in pack.conflicts:
        parts.append(conflict.question)
        parts.extend(p.value_text for p in conflict.positions)
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# PER-QUESTION RESULT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class QuestionResult:
    id: str
    work: str
    question: str
    sufficiency: str
    blocks: int
    cited: int
    citation_coverage: float = 0.0
    unsupported_rate: float = 0.0
    evidence_recall: float = 0.0
    required_recall: float = 0.0
    hallucinated: list[str] = field(default_factory=list)
    conflicts_found: int = 0
    conflicts_expected: int = 0
    temporal_fidelity: float = 1.0
    rights_violations: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def score_question(
    entry: dict,
    answer: Answer,
    pack: ContextPack,
    sources: dict[str, Source],
    seconds: float,
) -> QuestionResult:
    result = QuestionResult(
        id=entry["id"],
        work=entry["work"],
        question=entry["question"],
        sufficiency=answer.sufficiency.value,
        blocks=len(answer.blocks),
        cited=len(answer.source_ids),
        seconds=round(seconds, 2),
    )

    # 1 + 2. Citation coverage and unsupported claims.
    claims = {c.id: c for group in from_pack(pack, sources, only_verified=False).values() for c in group}
    prose_blocks = [b for b in answer.blocks if b.kind not in (BlockKind.CONFLICT, BlockKind.GAP)]
    used = [c for b in prose_blocks for cid in b.claim_ids if (c := claims.get(cid))]
    verified = [c for c in used if verify_claim(c, sources)]
    result.citation_coverage = (
        len([b for b in prose_blocks if b.claim_ids]) / len(prose_blocks)
        if prose_blocks else 1.0
    )
    result.unsupported_rate = (
        1.0 - len(verified) / len(used) if used else 0.0
    )

    # 3. Evidence recall: must_cite sources that actually appear.
    must = entry.get("must_cite", [])
    if must:
        cited = set(answer.source_ids) | {
            p.source_id for c in pack.conflicts for p in c.positions
        }
        result.evidence_recall = len([m for m in must if m in cited]) / len(must)

    # 4. Required-claim recall (lexical).
    required = entry.get("required_claims", [])
    if required:
        text = document_text(answer, pack)
        hits = [r for r in required if overlap(r, text) >= 0.6]
        result.required_recall = len(hits) / len(required)

    # 5. Hallucination: the document must not *assert* a forbidden claim.
    text = document_text(answer, pack)
    for forbidden in entry.get("must_not_claim", []):
        if asserts(forbidden, text):
            result.hallucinated.append(forbidden)

    # 7. Conflict recall.
    expected = entry.get("expected_conflicts", [])
    result.conflicts_expected = len(expected)
    for exp in expected:
        for conflict in pack.conflicts:
            if conflict.predicate != exp.get("predicate"):
                continue
            if len(conflict.positions) >= exp.get("min_positions", 2):
                result.conflicts_found += 1
                break

    # 8. Temporal fidelity.
    result.temporal_fidelity = temporal_fidelity(answer)

    # 9. Rights violations, checked on everything the document renders.
    result.rights_violations = rights_violations(answer, pack, sources)

    if answer.sufficiency == Sufficiency.INSUFFICIENT:
        result.notes.append(answer.sufficiency_note or "insufficient")
    return result


def temporal_fidelity(answer: Answer) -> float:
    if not answer.timeline:
        return 1.0
    good = 0
    for event in answer.timeline:
        display = (event.when.display or "").strip()
        if event.when.precision == Precision.UNKNOWN:
            good += 0 if re.match(r"^\d{4}-\d{2}-\d{2}$", display) else 1
            continue
        # A full ISO date on a year-precision dating is a lie about precision.
        if event.when.precision in (Precision.YEAR, Precision.DECADE, Precision.CENTURY):
            good += 0 if re.match(r"^\d{4}-\d{2}-\d{2}$", display) else 1
            continue
        good += 1
    return good / len(answer.timeline)


def rights_violations(answer: Answer, pack: ContextPack, sources: dict[str, Source]) -> int:
    from src import rights

    violations = 0
    for block in answer.blocks:
        if block.kind == BlockKind.QUOTE:
            source = next(
                (s for s in sources.values() if s.id in block.source_ids), None
            )
            if source is None:
                violations += 1
                continue
            if not source.rights.shows_text or not source.rights.storage_allowed:
                violations += 1

    for conflict in pack.conflicts:
        for position in conflict.positions:
            source = sources.get(position.source_id)
            if source is None:
                continue
            allowed = rights.gate_span(source, position.span.start, position.span.end)
            if allowed is not None and not source.rights.shows_text:
                violations += 1

    for source in sources.values():
        if not source.rights.storage_allowed and source.text:
            violations += 1
    return violations


# ═══════════════════════════════════════════════════════════════════════════
# AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════

GATES = {
    "citation_coverage": (1.0, "min"),
    "unsupported_rate": (0.0, "max"),
    "hallucinations": (0, "max"),
    "temporal_fidelity": (1.0, "min"),
    "rights_violations": (0, "max"),
    "refusal_misses": (0, "max"),
}

TARGETS = {
    "evidence_recall": 0.70,
    "required_recall": 0.80,
    "conflict_recall": 0.60,
}


def aggregate(results: list[QuestionResult], refusal_misses: int = 0) -> dict:
    n = max(len(results), 1)
    summary = {
        "questions": len(results),
        "citation_coverage": sum(r.citation_coverage for r in results) / n,
        "unsupported_rate": sum(r.unsupported_rate for r in results) / n,
        "evidence_recall": sum(r.evidence_recall for r in results) / n,
        "required_recall": sum(r.required_recall for r in results) / n,
        "hallucinations": sum(len(r.hallucinated) for r in results),
        "temporal_fidelity": sum(r.temporal_fidelity for r in results) / n,
        "rights_violations": sum(r.rights_violations for r in results),
        "conflict_recall": (
            sum(r.conflicts_found for r in results) /
            max(sum(r.conflicts_expected for r in results), 1)
        ),
        "refusal_misses": refusal_misses,
        "mean_seconds": sum(r.seconds for r in results) / n,
        "insufficient": sum(1 for r in results if r.sufficiency == "INSUFFICIENT"),
        "thin": sum(1 for r in results if r.sufficiency == "THIN"),
    }
    summary["gate_failures"] = [
        name for name, (limit, direction) in GATES.items()
        if (summary.get(name, 0) < limit if direction == "min" else summary.get(name, 0) > limit)
    ]
    return summary
