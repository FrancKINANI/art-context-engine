"""Evidence → Answer document (§4.7). Deterministic assembler, first.

Why a template assembler ships before the LLM one: it makes the citation
contract testable offline, and it is the baseline an LLM must beat (Baseline B,
§10.4). It is not here because it writes better prose. It writes *provable*
prose: every sentence is a claim, every claim has a span, and a block whose
claims do not verify is dropped rather than softened.

What this assembler refuses to do:

* state a cause nobody wrote down ("because he was deaf");
* average a disagreement into a confident middle;
* hide a gap to make a paragraph flow;
* write a paragraph when the evidence does not support one (§10.6).
"""

from __future__ import annotations

from src.answer.claims import build_quote, from_extracted, from_pack, verify_claim
from src.answer.sentences import sentence_for
from src.models import (
    Answer,
    Attribution,
    Block,
    BlockKind,
    Claim,
    ContextPack,
    ExtractedClaim,
    Gap,
    QuestionType,
    Source,
    Stance,
    Sufficiency,
    Work,
)
from src.retrieval.gatherer import independent_source_count
from src.retrieval.question import Interpretation
from src.temporal import describe

MODEL = "deterministic-v1"
MIN_INDEPENDENT_SOURCES = 3
MIN_QUOTE_CHARS = 120

#: A scholarly source must say enough to be criticism. A 75-character abstract
#: ("This article examines X.") is a pointer, not an argument; grounding an
#: explanation in it would satisfy §10.6 on a technicality.
MIN_SCHOLARLY_QUOTE = 120


# ═══════════════════════════════════════════════════════════════════════════
# SUFFICIENCY (§10.6)
# ═══════════════════════════════════════════════════════════════════════════

def assess_sufficiency(pack: ContextPack, sources: dict[str, Source]) -> tuple[Sufficiency, str]:
    independent = independent_source_count(pack)
    if independent < MIN_INDEPENDENT_SOURCES:
        return (
            Sufficiency.INSUFFICIENT,
            f"Only {independent} independent source(s) were found; the engine needs at "
            f"least {MIN_INDEPENDENT_SOURCES} before it will write an explanation.",
        )

    scholarly_with_span = False
    for claim in pack.claims:
        source = sources.get(claim.source_id)
        if source is None:
            continue
        if source.authority.value != "SCHOLARLY":
            continue
        if len(claim.span.text.strip()) < MIN_SCHOLARLY_QUOTE:
            continue
        if claim.span.resolve(source.text or "") == claim.span.text:
            scholarly_with_span = True
            break
    if not scholarly_with_span:
        return (
            Sufficiency.INSUFFICIENT,
            "No scholarly source with a verifiable quotation was found, so the claim "
            "cannot be grounded in criticism — only in reference works.",
        )
    return Sufficiency.SUFFICIENT, ""


# ═══════════════════════════════════════════════════════════════════════════
# ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

def assemble(
    work: Work,
    interp: Interpretation,
    pack: ContextPack,
    sources: dict[str, Source],
    *,
    parent_id: str | None = None,
) -> Answer:
    sufficiency, note = assess_sufficiency(pack, sources)
    claims: list[Claim] = []
    blocks: list[Block] = []

    if sufficiency == Sufficiency.INSUFFICIENT:
        answer = _refusal(work, interp, pack, sources, note, parent_id)
        return answer

    by_predicate = from_pack(pack, sources)

    # 1. Thesis: the work's identity plus the most relevant supported statement.
    aspect_claims = [c for p in interp.predicates for c in by_predicate.get(p, [])]
    thesis_claim = aspect_claims[0] if aspect_claims else _first_claim(by_predicate)
    if thesis_claim is None:
        # Nothing survives verification: an answer with no claims is not an answer.
        return _refusal(
            work, interp, pack, sources,
            "No claim could be tied to a verified span in the sources.",
            parent_id,
        )
    thesis_text = _identity_sentence(work) + " " + _sentence(thesis_claim)
    claims.append(thesis_claim)
    blocks.append(Block(
        kind=BlockKind.THESIS,
        text=thesis_text,
        claim_ids=[thesis_claim.id],
        source_ids=cites_of(thesis_claim),
    ))

    # 2. Context: when, for whom, to whom. The thesis already said the most
    #    relevant one, so it is not repeated here.
    context_claims = [
        c for c in _context_claims(by_predicate, interp) if c.id != thesis_claim.id
    ]
    context_blocks = _context_blocks(work, context_claims)
    blocks.extend(context_blocks)
    claims.extend(context_claims)

    # 3. Close reading: the work's own material, when the question is about it.
    #    Claims whose predicate is contested are left to the conflict block —
    #    stating "5 variations. 6 variations." as prose would average the dispute
    #    into a paragraph, which §3.7 rule 6 forbids.
    contested_predicates = {c.predicate for c in pack.conflicts}
    close_claims = _dedupe_by_value([
        c for p in ("variation_count", "key", "movement_count", "metronome_mark",
                    "genre", "stylistic_elements", "line_count", "stanza_count",
                    "subject_matter")
        if p not in contested_predicates
        for c in by_predicate.get(p, [])[:1]
    ])
    if close_claims:
        sentence = " ".join(_sentence(c) for c in close_claims[:3])
        blocks.append(Block(
            kind=BlockKind.CLOSE_READING,
            text=sentence,
            claim_ids=[c.id for c in close_claims[:3]],
            source_ids=[sid for c in close_claims[:3] for sid in c.source_ids],
        ))
        claims.extend(close_claims[:3])

    # 4. A verbatim quotation, when a primary text may be quoted.
    quote = _quote_block(sources)
    if quote is not None:
        block, quote_claim, quote_text = quote
        blocks.append(block)
        claims.append(quote_claim)

    # 5. Conflicts: shown, never resolved.
    for conflict in pack.conflicts:
        if conflict.kind.value == "NULL":
            continue
        # The renderer decides the styling: INTERPRETIVE is "readings differ",
        # never a warning icon (§4.5.1).
        lead = (
            "Two defensible readings of "
            if conflict.kind.value == "INTERPRETIVE"
            else "The sources disagree about "
        )
        blocks.append(Block(
            kind=BlockKind.CONFLICT,
            text=f"{lead}{conflict.question}.",
            claim_ids=[],
            conflict_ids=[conflict.id],
        ))

    # 6. Gaps that matter for this question.
    material_gaps = [g for g in pack.gaps if g.severity == "MATERIAL"]
    if material_gaps:
        blocks.append(Block(
            kind=BlockKind.GAP,
            text=" ".join(g.description for g in material_gaps),
            gap_ids=[g.id for g in material_gaps],
        ))

    # 7. Close: a reading, marked as a reading — and only if it answers *this*
    #    question. An off-topic interpretation is context we leave in the pack
    #    (§3.7 rule 7).
    reading = _reading_claim(by_predicate, interp)
    if reading is not None:
        blocks.append(Block(
            kind=BlockKind.CLOSE,
            text=_sentence(reading) + (
                " That is a reading drawn from the sources above, not a fact they state."
                if reading.stance == Stance.INFERENCE else ""
            ),
            claim_ids=[reading.id],
            source_ids=cites_of(reading),
        ))
        claims.append(reading)

    blocks = _drop_unverifiable(blocks, claims, sources)

    # Sources cited through a conflict card are cited as much as sources cited
    # through a sentence: the reader is looking at their evidence.
    cited = {sid for c in claims for sid in c.source_ids}
    for conflict in pack.conflicts:
        cited.update(p.source_id for p in conflict.positions)
    answer = Answer(
        parent_id=parent_id,
        work_id=work.id,
        question=interp.question,
        question_type=interp.question_type,
        thesis=blocks[0].text if blocks else _identity_sentence(work),
        blocks=blocks,
        source_ids=sorted(cited),
        conflict_ids=[c.id for c in pack.conflicts],
        gap_ids=[g.id for g in pack.gaps],
        timeline=pack.timeline,
        attribution=_attribution(pack, cited, sources),
        sufficiency=Sufficiency.THIN if len(cited) < MIN_INDEPENDENT_SOURCES else sufficiency,
        sufficiency_note=(
            f"Answer rests on {len(cited)} cited sources." if len(cited) < MIN_INDEPENDENT_SOURCES else None
        ),
        model=MODEL,
    )
    return answer


# ═══════════════════════════════════════════════════════════════════════════
# REFUSAL (§7.6, §10.6)
# ═══════════════════════════════════════════════════════════════════════════

def _refusal(
    work: Work,
    interp: Interpretation,
    pack: ContextPack,
    sources: dict[str, Source],
    note: str,
    parent_id: str | None,
) -> Answer:
    """An honest non-answer: what we found, what is missing, and nothing else."""
    blocks: list[Block] = []
    known = next(
        (c for c in pack.claims if _source_of(c, sources) is not None),
        None,
    )
    if known is not None:
        source = sources[known.source_id]
        claim = from_extracted(known, source, sentence_for(known))
        if claim is not None:
            blocks.append(Block(
                kind=BlockKind.CONTEXT,
                text="What we do have: " + _sentence(claim),
                claim_ids=[claim.id],
                source_ids=claim.source_ids,
            ))
    if pack.gaps:
        blocks.append(Block(
            kind=BlockKind.GAP,
            text=" ".join(f"{g.description}" for g in pack.gaps),
            gap_ids=[g.id for g in pack.gaps],
        ))
    return Answer(
        parent_id=parent_id,
        work_id=work.id,
        question=interp.question,
        question_type=interp.question_type,
        thesis=f"We can't answer this well yet. {note}",
        blocks=blocks,
        source_ids=sorted({sid for b in blocks for sid in b.source_ids}),
        gap_ids=[g.id for g in pack.gaps],
        timeline=pack.timeline,
        attribution=_attribution(pack, {sid for b in blocks for sid in b.source_ids}, sources),
        sufficiency=Sufficiency.INSUFFICIENT,
        sufficiency_note=note,
        model=MODEL,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CLAIM → SENTENCE
# ═══════════════════════════════════════════════════════════════════════════

def _sentence(claim: Claim) -> str:
    text = claim.text.strip()
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else text + "."


# ═══════════════════════════════════════════════════════════════════════════
# BLOCK BUILDERS
# ═══════════════════════════════════════════════════════════════════════════

def _identity_sentence(work: Work) -> str:
    creator = work.primary_creator.name if work.primary_creator else "an unknown creator"
    when = describe(work.date_created) if work.date_created else "date unknown"
    return f"{work.title}, by {creator} ({when})."


def _dedupe_by_value(claims: list[Claim]) -> list[Claim]:
    """Three sources saying "C minor" is one fact with three citations, not three sentences."""
    seen: dict[str, Claim] = {}
    for claim in claims:
        key = claim.text.lower().replace("#", "♯")
        if key in seen:
            # Merge the citation rather than repeating the sentence.
            merged = seen[key]
            for sid in claim.source_ids:
                if sid not in merged.source_ids:
                    merged.source_ids.append(sid)
            continue
        seen[key] = claim
    return list(seen.values())


def _context_claims(by_predicate: dict[str, list[Claim]], interp: Interpretation) -> list[Claim]:
    """One claim per predicate — the most informative one.

    Two sources dating the composition "1821" and "1821–1822" are not two facts;
    showing both reads as a stutter. The most informative wording is kept (the
    one that names the season, the range, the day) and the citations are merged
    downstream, so nothing is lost but the repetition.
    """
    ordered = ["composition_date", "publication_date", "premiere_date",
               "dedication", "sales_copies", "setting"]
    out: list[Claim] = []
    for predicate in ordered:
        claims = by_predicate.get(predicate, [])
        if not claims:
            continue
        best = max(claims, key=lambda c: (len(c.text), c.extractor_confidence))
        if best not in out:
            out.append(best)
    return out


def _context_blocks(work: Work, claims: list[Claim]) -> list[Block]:
    if not claims:
        return []
    # Same fact from three sources → one sentence with three citations.
    claims = _dedupe_by_value(claims)
    return [Block(
        kind=BlockKind.CONTEXT,
        text=" ".join(_sentence(c) for c in claims),
        claim_ids=[c.id for c in claims],
        source_ids=[sid for c in claims for sid in c.source_ids],
    )]


def _quote_block(sources: dict[str, Source]) -> tuple[Block, Claim, str] | None:
    """The one place the answer shows verbatim primary text, if we may.

    The text is gated before it enters the block, so a source that turns out to
    be link-only produces no quote rather than an error.
    """
    from src.models import Span

    best: tuple[Source, object] | None = None
    for source in sources.values():
        if not source.text or len(source.text) < MIN_QUOTE_CHARS:
            continue
        if not source.rights.storage_allowed or source.type.value != "PRIMARY":
            continue
        start = _first_content_offset(source.text)
        span_end = min(len(source.text), start + 320)
        span = Span(start=start, end=span_end, text=source.text[start:span_end])
        quote = build_quote(source, span)
        if quote is None:
            continue
        best = (source, quote)
        break
    if best is None:
        return None

    source, quote = best
    # A citation-only claim: the licence lets us quote this passage but says
    # nothing about what it means, and neither do we.
    claim = Claim(
        text=f"Verbatim excerpt from {source.title}.",
        source_ids=[source.id],
        spans=[],
        stance=Stance.CITATION_ONLY,
    )
    block = Block(
        kind=BlockKind.QUOTE,
        text=quote.text,
        claim_ids=[claim.id],
        source_ids=[source.id],
    )
    return block, claim, quote.text


_FURNITURE = ("•", "Fichier", "djvu", "Portail", "Pour les autres", "collection")


def _first_content_offset(text: str) -> int:
    """Skip the scan's header furniture so the quote starts at the poem.

    Wikisource pages open with edition metadata (publisher, page range, scan
    link). Quoting that as "the text itself" would be embarrassing and useless,
    so the offset is the first line that reads like verse rather than a colophon.
    """
    offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if (
            len(stripped) >= 25
            and stripped[0].isupper()
            and not any(ch.isdigit() for ch in stripped)   # colophons carry years
            and not any(marker in stripped for marker in _FURNITURE)
        ):
            return offset
        offset += len(line) + 1
    return 0


def _reading_claim(by_predicate: dict[str, list[Claim]], interp: Interpretation) -> Claim | None:
    for predicate in ("interpretation", "reception"):
        for claim in by_predicate.get(predicate, []):
            if claim.stance != Stance.SUPPORTS:
                continue
            if _answers_question(claim, interp):
                return claim
    return None


def _answers_question(claim: Claim, interp: Interpretation) -> bool:
    """Does this passage answer *this* question, or is it merely nearby?

    A single shared word is not relevance: a question about the "second
    movement" must not be closed by a sentence that happens to contain
    "second". So the test is either a shared aspect term ("plain", "tempo",
    "dedication") or at least two shared content words.
    """
    from src.retrieval.question import ASPECTS
    from src.retrieval.ranker import content_terms

    wanted = content_terms(interp.question)
    if not wanted:
        return True
    got = content_terms(claim.text)
    aspect_terms = set()
    for phrase in ASPECTS.get(interp.aspect, ((), ()))[1]:
        aspect_terms |= content_terms(phrase)
    if wanted & aspect_terms & got:
        return True
    return len(wanted & got) >= 2


def _first_claim(by_predicate: dict[str, list[Claim]]) -> Claim | None:
    for predicate in ("composition_date", "publication_date", "dedication", "key"):
        claims = by_predicate.get(predicate)
        if claims:
            return claims[0]
    return None


def _drop_unverifiable(
    blocks: list[Block], claims: list[Claim], sources: dict[str, Source]
) -> list[Block]:
    """§3.7 rule 2, enforced: a block whose claims do not verify does not render."""
    index = {c.id: c for c in claims}
    kept: list[Block] = []
    for block in blocks:
        if block.kind in (BlockKind.CONFLICT, BlockKind.GAP):
            kept.append(block)
            continue
        unresolved = [index[cid] for cid in block.claim_ids if cid in index]
        if not unresolved:
            continue
        if all(verify_claim(c, sources) for c in unresolved):
            kept.append(block)
    return kept


def cites_of(claim: Claim | None) -> list[str]:
    return list(claim.source_ids) if claim else []


def _source_of(extracted: ExtractedClaim, sources: dict[str, Source]) -> Source | None:
    return sources.get(extracted.source_id)


def _attribution(
    pack: ContextPack, cited: set[str], sources: dict[str, Source]
) -> list[Attribution]:
    out: list[Attribution] = []
    from src.rights import attribution_line

    for source in sources.values():
        if source.id not in cited:
            continue
        out.append(Attribution(
            source_id=source.id,
            title=source.title,
            author=source.author,
            date_display=source.date_document.display if source.date_document else "undated",
            license_id=source.rights.license_id,
            license_url=source.rights.license_url,
            url=source.url,
            attribution_text=attribution_line(source),
        ))
    return out
