"""On-demand block expansion (§7.5).

"Go deeper" does three things and no more: show the verbatim quote the claim
came from, show the sources behind it, and show what is contested or missing.
It never navigates away, never replaces the document, and never lengthens the
page for a reader who did not ask.

Everything here is rights-gated through `build_quote`, which calls the single
gate. If the source is link-only, the deepening shows a citation — no text, no
paraphrase dressed as a quotation.
"""

from __future__ import annotations

from src.answer.claims import build_quote, from_pack
from src.models import (
    Answer,
    Claim,
    ContextPack,
    Deepening,
    Gap,
    Source,
    Stance,
)


def deepen(
    answer: Answer,
    block_id: str,
    pack: ContextPack,
    sources: dict[str, Source],
) -> Deepening:
    block = next((b for b in answer.blocks if b.id == block_id), None)
    if block is None:
        raise KeyError(f"no block {block_id} in answer {answer.id}")

    all_claims = [c for group in from_pack(pack, sources).values() for c in group]
    wanted = set(block.claim_ids)
    claims = [c for c in all_claims if c.id in wanted]

    quotes = []
    for claim in claims:
        if claim.stance == Stance.INFERENCE:
            continue
        for span in claim.spans:
            for source_id in claim.source_ids:
                source = sources.get(source_id)
                if source is None:
                    continue
                quote = build_quote(source, span)
                if quote is not None and quote not in quotes:
                    quotes.append(quote)

    context_ids: list[str] = []
    for claim in claims:
        for source_id in claim.source_ids:
            if source_id in sources and source_id not in context_ids:
                context_ids.append(source_id)
    context = [sources[sid] for sid in context_ids]

    extracted_ids = {cid for c in claims for cid in c.claim_ids}
    conflicts = [
        c for c in pack.conflicts
        if extracted_ids & {p.claim_id for p in c.positions}
    ]
    gaps = [g for g in pack.gaps if _gap_is_relevant(g, block, claims)]

    return Deepening(
        block_id=block_id,
        quotes=quotes,
        context=context,
        conflicts=conflicts,
        gaps=gaps,
        claims=claims,
    )


def _gap_is_relevant(gap: Gap, block, claims: list[Claim]) -> bool:
    if gap.related_subject:
        haystack = " ".join(block.text.split() + [c.text for c in claims])
        return gap.related_subject in haystack
    return gap.severity == "MATERIAL"
