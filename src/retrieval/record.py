"""Record the corpus: fetch every adapter for every curated work, keep the bytes.

    python -m src.retrieval.record            # all works
    python -m src.retrieval.record op111      # one

§10.1: fixtures are recorded HTTP responses with their retrieval timestamps, so
the evaluation suite runs offline and deterministically. Re-recording is a
deliberate act: it changes the corpus, so it invalidates yesterday's metrics and
must be followed by a full evaluation run.
"""

from __future__ import annotations

import sys

from src.models import Work
from src.registry import all_works
from src.retrieval.adapters.registry import build_adapters
from src.retrieval.gatherer import _retrieval_query
from src.retrieval.http import RecordingBackend


def record_work(backend: RecordingBackend, work: Work) -> dict:
    result = {"work": work.id, "sources": [], "errors": []}
    for adapter in build_adapters(backend, work.type):
        try:
            # The same query the pipeline sends, so the corpus is the corpus of
            # what production actually asks for.
            hits = adapter.search(_retrieval_query(work), work, limit=2)
            if not hits:
                result["errors"].append(f"{adapter.name}: no hits")
                continue
            for hit in hits[:1]:
                source = adapter.fetch(hit, work)
                if source is None:
                    continue
                result["sources"].append(
                    f"{source.id} ({source.authority.value}, "
                    f"{source.rights.display_policy.value}, {len(source.text)} chars)"
                )
        except Exception as exc:  # recording must not abort halfway
            result["errors"].append(f"{adapter.name}: {type(exc).__name__}: {exc}")
        result["errors"].extend(adapter.errors)
    return result


def main(argv: list[str]) -> int:
    wanted = argv[1] if len(argv) > 1 else None
    backend = RecordingBackend()
    works = [w for w in all_works() if not wanted or wanted in w.id]
    for work in works:
        info = record_work(backend, work)
        print(f"\n== {info['work']}")
        for line in info["sources"]:
            print(f"   + {line}")
        for line in info["errors"]:
            print(f"   ! {line}")
    backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
