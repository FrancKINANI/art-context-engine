"""A battery of real questions, read like a reader would read the pages.

Not a benchmark: a hunt for the next bug. Every past round of fixes started
here, with a page that looked plausible and was wrong.
"""

from __future__ import annotations

from src.retrieval.entity_resolver import resolve
from src.service import answer_question, clear_cache

QUERIES = [
    "Kendrick Lamar, To Pimp a Butterfly — what is it about?",
    "Nekfeu, Les étoiles vagabondes — what does the title mean?",
    "Rimbaud, Le Bateau ivre — what does the poem mean?",
    "Baudelaire, L'Albatros — what is the poem saying?",
    "Vermeer, Girl with a Pearl Earring — what does the painting mean?",
    "Goya, Saturn Devouring His Son — what does it show?",
    "Kubrick, 2001: A Space Odyssey — what does the ending mean?",
    "Poussin, Et in Arcadia ego — what does the painting say?",
    "Daft Punk, Veridis Quo — what is the track about?",
    "Aya Nakamura, Djadja — what is she saying?",
]


def main() -> None:
    for query in QUERIES:
        clear_cache()
        found = resolve(query)
        if not found.best:
            print(f"\n=== {query}\n    NO WORK · {found.why}")
            continue
        best = found.best
        result = answer_question(query, candidates=[best])
        answer, pack = result.answer, result.pack
        cited = {c.source_id for c in pack.claims}
        print(f"\n=== {query}")
        print(f"    work   : {best.creator} — {best.title} ({best.confidence:.2f}, {best.matched_on})")
        print(f"    status : {answer.sufficiency.value} · {len(answer.source_ids)} cited · "
              f"{len(pack.sources)} read · {len(pack.claims)} claims")
        print(f"    thesis : {answer.thesis[:150]}")
        for source in pack.sources[:4]:
            mark = "*" if source.id in cited else " "
            print(f"      {mark} {source.authority.value:10s} {source.title[:70]}")


if __name__ == "__main__":
    main()
