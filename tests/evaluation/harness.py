"""Evaluation harness (§10).

    python -m tests.evaluation.harness            # run against recorded fixtures
    python -m tests.evaluation.harness --live     # run against the network (noisy)
    python -m tests.evaluation.harness --json     # machine-readable

Runs the golden set against the fixture corpus, offline and deterministically —
the default, because §10.1 says fixtures are what make the suite reproducible and
a live corpus silently changes the answer to a gate. The report is written to
`tests/evaluation/reports/` (gitignored): a report is a measurement, not a source
file, and committing yesterday's numbers is how a project starts tuning to its
own output.

Hard-gate failures exit non-zero, so CI can treat them as failures. Quality
targets are printed and never block — they are the trend line.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import yaml

from src.config import GOLDEN_DIR, settings
from src.registry import get_work
from src.service import answer_question
from src.retrieval.entity_resolver import Resolution
from tests.evaluation.metrics import QuestionResult, aggregate, score_question

REPORT_DIR = Path(__file__).parent / "reports"
GOLDEN_FILE = GOLDEN_DIR / "questions.yaml"


def load_golden(path: Path | None = None) -> dict:
    return yaml.safe_load((path or GOLDEN_FILE).read_text(encoding="utf-8"))


def run(golden: dict | None = None) -> tuple[list[QuestionResult], dict, int]:
    golden = golden or load_golden()
    results: list[QuestionResult] = []
    refusal_misses = 0
    probes = golden.get("refusal_probes", [])

    for entry in golden.get("questions", []):
        work = get_work(entry["work"])
        if work is None:
            print(f"! unknown work {entry['work']} in {entry['id']}", file=sys.stderr)
            continue
        started = time.perf_counter()
        pipeline = answer_question(entry["question"], work_id=work.id)
        elapsed = time.perf_counter() - started
        if isinstance(pipeline, Resolution):
            print(f"! resolution failed for {entry['id']}", file=sys.stderr)
            continue
        results.append(score_question(
            entry, pipeline.answer, pipeline.pack, pipeline.sources, elapsed,
        ))

    for probe in probes:
        work = get_work(probe["work"])
        if work is None:
            continue
        pipeline = answer_question(probe["question"], work_id=work.id)
        if isinstance(pipeline, Resolution):
            continue
        if pipeline.answer.sufficiency.value != probe["expect"]:
            refusal_misses += 1
            print(
                f"! refusal missed: {probe['id']} produced "
                f"{pipeline.answer.sufficiency.value}, expected {probe['expect']}",
                file=sys.stderr,
            )

    summary = aggregate(results, refusal_misses)
    return results, summary, refusal_misses


# ═══════════════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════════════

def format_report(results: list[QuestionResult], summary: dict) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"Corpus mode: `{settings.mode}` · generated {date.today().isoformat()}",
        "",
        "## Gates",
        "",
        "| Metric | Value | Gate | Status |",
        "|---|---|---|---|",
    ]
    gate_rows = [
        ("Citation coverage", summary["citation_coverage"], "100%", "min"),
        ("Unsupported claims", summary["unsupported_rate"], "0%", "max"),
        ("Hallucinations", summary["hallucinations"], "0", "max"),
        ("Temporal fidelity", summary["temporal_fidelity"], "100%", "min"),
        ("Rights violations", summary["rights_violations"], "0", "max"),
        ("Refusal misses", summary["refusal_misses"], "0", "max"),
    ]
    for name, value, gate, _ in gate_rows:
        shown = f"{value:.0%}" if isinstance(value, float) and value <= 1 else f"{value}"
        failed = name.lower().replace(" ", "_") in summary["gate_failures"] or (
            (name == "Unsupported claims" and summary["unsupported_rate"] > 0)
        )
        lines.append(f"| {name} | {shown} | {gate} | {'FAIL' if failed else 'pass'} |")

    lines += [
        "",
        "## Targets",
        "",
        "| Metric | Value | Target |",
        "|---|---|---|",
        f"| Evidence recall | {summary['evidence_recall']:.0%} | ≥70% |",
        f"| Required-claim recall | {summary['required_recall']:.0%} | ≥80% |",
        f"| Conflict recall | {summary['conflict_recall']:.0%} | ≥60% |",
        "",
        "## Per question",
        "",
        "| Question | Suff. | Cited | Evid. | Req. | Conflicts | s |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.id} | {r.sufficiency[:4]} | {r.cited} | {r.evidence_recall:.0%} "
            f"| {r.required_recall:.0%} | {r.conflicts_found}/{r.conflicts_expected} "
            f"| {r.seconds:.1f} |"
        )

    missed = [r for r in results if r.hallucinated]
    if missed:
        lines += ["", "## Hallucinated claims", ""]
        for r in missed:
            for claim in r.hallucinated:
                lines.append(f"- **{r.id}**: produced “{claim}”")

    lines += [
        "",
        "## Cost / latency",
        "",
        f"Mean wall time per answer: {summary['mean_seconds']:.2f}s "
        f"(fixture corpus, deterministic assembler, no LLM calls).",
        "",
        f"Answers classified INSUFFICIENT: {summary['insufficient']} · "
        f"THIN: {summary['thin']} · total: {summary['questions']}.",
        "",
        "_Human relevance (metric 11) is not automated; it requires two blind "
        "raters per answer (§10.3)._",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set evaluation")
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    parser.add_argument("--save", action="store_true", help="write report to reports/")
    parser.add_argument(
        "--live", action="store_true",
        help="gather from the network instead of the recorded corpus",
    )
    args = parser.parse_args(argv[1:])

    if not args.live:
        from src.retrieval.http import reset_backend

        settings.mode = "fixture"
        reset_backend()

    results, summary, misses = run()
    report = format_report(results, summary)

    if args.save:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = date.today().isoformat()
        (REPORT_DIR / f"report-{stamp}.md").write_text(report, encoding="utf-8")
        (REPORT_DIR / f"report-{stamp}.json").write_text(
            json.dumps({"summary": summary, "results": [r.__dict__ for r in results]},
                       indent=2),
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps({"summary": summary, "results": [r.__dict__ for r in results]}, indent=2))
    else:
        print(report)

    return 1 if summary["gate_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
