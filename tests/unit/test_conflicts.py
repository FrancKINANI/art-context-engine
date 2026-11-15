"""§4.5: precision over recall, and the three things that are *not* conflicts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.models import (
    AuthorityTier,
    ExtractedClaim,
    RightsInfo,
    Source,
    SourceType,
    Span,
)
from src.retrieval.conflict_detector import detect, detect_pair
from src.temporal import year

FIXTURES = Path(__file__).parent.parent / "fixtures" / "conflicts" / "adversarial.yaml"
CASES = yaml.safe_load(FIXTURES.read_text())["cases"]


def claim(case: dict, side: str, predicate: str, confidence: float | None = None) -> ExtractedClaim:
    spec = case[side]
    text = spec["text"]
    return ExtractedClaim(
        work_id="w",
        source_id=spec["source"],
        span=Span(start=0, end=len(text), text=text),
        subject="w",
        predicate=predicate,
        value=spec["value"],
        value_text=text,
        extractor_confidence=confidence if confidence is not None else 0.92,
    )


def source_for(claim_obj: ExtractedClaim) -> Source:
    return Source(
        id=claim_obj.source_id,
        work_id="w",
        type=SourceType.SECONDARY,
        subtype="article",
        title=f"Source {claim_obj.source_id}",
        author="Author",
        date_document=year(1840),
        text=claim_obj.span.text,
        rights=RightsInfo.cc_by(),
        authority=AuthorityTier.SCHOLARLY,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_incompatibility_verdict(case):
    predicate = case["predicate"]
    confidence = case.get("confidence")
    a = claim(case, "a", predicate, confidence)
    b = claim(case, "b", predicate, confidence)
    verdict, _ = detect_pair(a, b, predicate)
    expected = case["expected"]
    if expected == "none":
        assert verdict is None, case["why"]
    elif expected == "candidate":
        assert verdict == "candidate", case["why"]
    else:
        assert verdict == "conflict", case["why"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_end_to_end_threshold(case):
    """The same fixtures through the full pipeline, including the gate to readers."""
    predicate = case["predicate"]
    confidence = case.get("confidence")
    a = claim(case, "a", predicate, confidence)
    b = claim(case, "b", predicate, confidence)
    sources = {a.source_id: source_for(a), b.source_id: source_for(b)}
    conflicts, candidates = detect([a, b], sources)

    expected = case["expected"]
    if expected == "conflict":
        assert len(conflicts) == 1, case["why"]
        assert len(conflicts[0].positions) == 2
        assert conflicts[0].resolution.value == "UNRESOLVED"
    elif expected == "candidate":
        assert not conflicts and candidates, case["why"]
    else:
        assert not conflicts and not candidates, case["why"]


def test_conflict_positions_are_ordered_primary_first():
    text_a, text_b = "the autograph gives ♩=72", "Schindler reports ♩=50"
    a = ExtractedClaim(
        work_id="w", source_id="autograph", span=Span(start=0, end=len(text_a), text=text_a),
        subject="w.mov2.metronome", predicate="metronome_mark",
        value={"bpm": 72, "beat_unit": "quarter"}, value_text="72",
    )
    b = ExtractedClaim(
        work_id="w", source_id="schindler", span=Span(start=0, end=len(text_b), text=text_b),
        subject="w.mov2.metronome", predicate="metronome_mark",
        value={"bpm": 50, "beat_unit": "quarter"}, value_text="50",
    )
    sources = {
        "autograph": source_for(a).model_copy(update={"authority": AuthorityTier.PRIMARY}),
        "schindler": source_for(b).model_copy(update={"authority": AuthorityTier.CONTEMPORARY_REVIEW}),
    }
    conflicts, _ = detect([a, b], sources)
    assert conflicts[0].positions[0].authority == AuthorityTier.PRIMARY


def test_unverified_span_never_reaches_a_reader():
    a = ExtractedClaim(
        work_id="w", source_id="a", span=Span(start=0, end=10, text="five varia"),
        subject="w", predicate="variation_count", value={"count": 5}, value_text="5",
    )
    b = ExtractedClaim(
        work_id="w", source_id="b", span=Span(start=0, end=14, text="six variations"),
        subject="w", predicate="variation_count", value={"count": 6}, value_text="6",
    )
    sources = {
        "a": source_for(a).model_copy(update={"text": "completely different text"}),
        "b": source_for(b),
    }
    conflicts, candidates = detect([a, b], sources)
    assert not conflicts
    assert any("span" in c.reason for c in candidates)


def test_chronological_disagreement_uses_the_temporal_relation():
    a_text, b_text = "written in 1820", "written in 1824"
    a = ExtractedClaim(
        work_id="w", source_id="a", span=Span(start=0, end=len(a_text), text=a_text),
        subject="w", predicate="composition_date", value={}, value_text="1820",
        about=year(1820),
    )
    b = ExtractedClaim(
        work_id="w", source_id="b", span=Span(start=0, end=len(b_text), text=b_text),
        subject="w", predicate="composition_date", value={}, value_text="1824",
        about=year(1824),
    )
    sources = {"a": source_for(a), "b": source_for(b)}
    conflicts, _ = detect([a, b], sources)
    assert conflicts and conflicts[0].kind.value == "CHRONOLOGICAL"
