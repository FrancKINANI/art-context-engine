"""Claim ↔ span verification (§3.6, §3.7, §4.7).

A citation that cannot point at the exact text is decoration. This module is the
place that decides whether a claim is *evidence* or *decoration*, and it has one
rule: the span must resolve to the stored character range, exactly.

It is also where `CITATION_ONLY` lives. Some sources (lyrics, paywalled
articles, link-only archives) may be named but not quoted. Their claims are
still usable — "the album's third verse names X (see Genius)" is a true,
checkable statement — but they carry no span and must never be counted as
having one.
"""

from __future__ import annotations

from src import rights
from src.models import (
    Claim,
    ExtractedClaim,
    Quote,
    Source,
    Span,
    Stance,
)


def verify_span(source: Source, span: Span) -> bool:
    """Does this span resolve to the same text in the stored document?"""
    resolved = span.resolve(source.text or "")
    return resolved is not None and resolved == span.text


def verify_claim(claim: Claim, sources: dict[str, Source]) -> bool:
    """A claim is verified when every span it cites resolves in its source."""
    if claim.stance == Stance.INFERENCE:
        return True
    if claim.stance == Stance.CITATION_ONLY:
        return bool(claim.source_ids)
    if not claim.source_ids or not claim.spans:
        return False
    for source_id in claim.source_ids:
        source = sources.get(source_id)
        if source is None:
            return False
    for span in claim.spans:
        if not any(verify_span(sources[sid], span) for sid in claim.source_ids):
            return False
    return True


def claim_id(extracted: ExtractedClaim) -> str:
    """Deterministic claim id, so a re-assembled document has stable block→claim links."""
    return f"c-{extracted.id}"


def from_extracted(
    extracted: ExtractedClaim,
    source: Source,
    text: str | None = None,
) -> Claim | None:
    """Turn an extracted claim into an answer claim, dropping bad evidence.

    The returned claim quotes nothing: it carries the span so a renderer can
    gate it later. If the source's policy forbids showing that span, the claim
    is still usable but becomes `CITATION_ONLY` with no span at all — which is
    exactly what §3.4 asks for.
    """
    if not verify_span(source, extracted.span):
        return None

    displayable = rights.gate_span(source, extracted.span.start, extracted.span.end)
    if displayable is None:
        return Claim(
            id=claim_id(extracted),
            text=text or extracted.value_text or extracted.predicate,
            source_ids=[source.id],
            claim_ids=[extracted.id],
            spans=[],
            stance=Stance.CITATION_ONLY,
            extractor_confidence=extracted.extractor_confidence,
        )

    return Claim(
        id=claim_id(extracted),
        text=text or extracted.value_text or extracted.predicate,
        source_ids=[source.id],
        claim_ids=[extracted.id],
        spans=[extracted.span],
        stance=Stance.SUPPORTS,
        extractor_confidence=extracted.extractor_confidence,
    )


def inference(text: str, backing: list[Claim]) -> Claim:
    """An inference is allowed, but it is labelled as one (§3.7 rule 4)."""
    return Claim(
        text=text,
        source_ids=[sid for c in backing for sid in c.source_ids],
        claim_ids=[cid for c in backing for cid in c.claim_ids],
        spans=[],
        stance=Stance.INFERENCE,
    )


def build_quote(source: Source, span: Span) -> Quote | None:
    """A quotable span, or None when the licence does not allow it.

    The gate is called here, at assembly time, using the single implementation
    in `src.rights`. There is no second path that copies `source.text` into a
    quote.
    """
    text = rights.gate_span(source, span.start, span.end)
    if not text:
        return None
    return Quote(
        source_id=source.id,
        source_title=source.title,
        author=source.author,
        date_display=source.date_document.display if source.date_document else "undated",
        authority=source.authority,
        text=text,
        span=span,
        url=source.url,
    )


def cites(claim: Claim) -> list[str]:
    return list(dict.fromkeys(claim.source_ids))


# ═══════════════════════════════════════════════════════════════════════════
# PACK → CLAIMS
# ═══════════════════════════════════════════════════════════════════════════

def from_pack(
    pack, sources: dict[str, Source], *, only_verified: bool = True
) -> dict[str, list[Claim]]:
    """Rebuild the answer's claims from the pack, grouped by predicate.

    Deterministic: the same pack produces the same claim ids, so a block built by
    the assembler can be deepened later without carrying claim objects around.
    """
    from src.answer.sentences import sentence_for

    out: dict[str, list[Claim]] = {}
    for extracted in pack.claims:
        source = sources.get(extracted.source_id)
        if source is None:
            continue
        claim = from_extracted(extracted, source, sentence_for(extracted))
        if claim is None:
            continue
        if only_verified and not verify_claim(claim, sources):
            continue
        out.setdefault(extracted.predicate, []).append(claim)
    for claims in out.values():
        claims.sort(key=lambda c: -c.extractor_confidence)
    return out
