"""The golden set is a gate, not a report (§10.2, §10.4).

A change that breaks a hard gate fails CI here. Quality targets are asserted
too, but at their documented floor — they are allowed to improve, not allowed to
silently rot.
"""

from __future__ import annotations

from tests.evaluation.harness import run


def test_hard_gates_and_targets(fixture_mode):
    results, summary, _ = run()

    assert summary["questions"] >= 12, "the golden set must stay at twelve questions"
    assert summary["citation_coverage"] >= 1.0, "every block must cite a verified span"
    assert summary["unsupported_rate"] <= 0.0, "no claim may be unverifiable"
    assert summary["hallucinations"] == 0, "a stated must_not_claim fails the run"
    assert summary["temporal_fidelity"] >= 1.0
    assert summary["rights_violations"] == 0
    assert summary["refusal_misses"] == 0

    assert summary["evidence_recall"] >= 0.70
    assert summary["required_recall"] >= 0.80
    assert summary["conflict_recall"] >= 0.60
    assert summary["gate_failures"] == []


def test_conflict_precision_on_adversarial_fixtures(fixture_mode):
    """Metric 6 lives in the fixtures, because the corpus has few real disputes."""
    import yaml
    from pathlib import Path

    from src.retrieval.conflict_detector import detect_pair
    from tests.unit.test_conflicts import claim

    cases = yaml.safe_load(
        (Path(__file__).parent.parent / "fixtures" / "conflicts" / "adversarial.yaml")
        .read_text()
    )["cases"]

    flagged = 0
    correct = 0
    for case in cases:
        confidence = case.get("confidence")
        a = claim(case, "a", case["predicate"], confidence)
        b = claim(case, "b", case["predicate"], confidence)
        verdict, _ = detect_pair(a, b, case["predicate"])
        predicted_conflict = verdict == "conflict"
        actually_conflict = case["expected"] == "conflict"
        if predicted_conflict:
            flagged += 1
            correct += int(actually_conflict)
        elif actually_conflict:
            pass  # a miss: counted by recall, not precision

    precision = correct / flagged if flagged else 1.0
    assert precision >= 0.90, f"conflict precision {precision:.2f} below gate"
