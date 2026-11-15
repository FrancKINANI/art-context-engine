"""End-to-end: question → evidence → document → deepening → export.

These run against the recorded corpus, so they are deterministic. They test the
promises a reader can check: every block resolves to a verified span, a
disagreement is shown rather than averaged, a link-only source contributes a
citation and not a quote, and a thin corpus produces a refusal rather than a
paragraph.
"""

from __future__ import annotations

import pytest

from src.answer.claims import from_pack, verify_claim
from src.answer.deepen import deepen
from src.answer.render import to_jsonable, to_markdown
from src.config import settings
from src.models import BlockKind, Sufficiency
from src.service import answer_question, clear_cache

pytestmark = pytest.mark.usefixtures("fixture_mode")


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


def test_op111_document_is_grounded():
    result = answer_question("Why does the second movement sound so plain?",
                             work_id="beethoven-op111")
    answer = result.answer
    assert answer.sufficiency == Sufficiency.SUFFICIENT
    assert answer.blocks

    claims = {
        c.id: c for group in from_pack(result.pack, result.sources).values() for c in group
    }
    for block in answer.blocks:
        if block.kind in (BlockKind.CONFLICT, BlockKind.GAP):
            assert block.conflict_ids or block.gap_ids
            continue
        assert block.claim_ids, "§3.7 rule 2: a block must resolve to a claim"
        for claim_id in block.claim_ids:
            assert verify_claim(claims[claim_id], result.sources)


def test_op111_conflict_is_shown_unresolved():
    result = answer_question("How many variations does the Arietta have?",
                             work_id="beethoven-op111")
    conflicts = result.pack.conflicts
    assert conflicts, "the corpus contains a five-vs-six variations disagreement"
    conflict = conflicts[0]
    assert conflict.resolution.value == "UNRESOLVED"
    assert len(conflict.positions) >= 2
    assert any(b.kind == BlockKind.CONFLICT for b in result.answer.blocks)


def test_gaps_are_reported_not_hidden():
    result = answer_question("When was Op. 111 first performed?",
                             work_id="beethoven-op111")
    assert any(g.gap_type == "no_sources" for g in result.pack.gaps)
    assert any(b.kind == BlockKind.GAP for b in result.answer.blocks)


def test_link_only_sources_contribute_no_text():
    result = answer_question("Why does the second movement sound so plain?",
                             work_id="beethoven-op111")
    for source in result.sources.values():
        if not source.rights.storage_allowed:
            assert source.text == "", f"{source.id} stored text it may not show"

    markdown = to_markdown(result.answer, result.pack, result.sources)
    # IMSLP is link-only: its page text must never appear as a quotation.
    for source in result.sources.values():
        if source.rights.redistribution_allowed is False and source.text:
            assert source.text[:40] not in markdown


def test_poem_is_quoted_from_the_public_domain_text():
    result = answer_question("How does Le Bateau ivre open?",
                             work_id="rimbaud-bateau-ivre")
    quotes = [b for b in result.answer.blocks if b.kind == BlockKind.QUOTE]
    assert quotes, "a public-domain poem should be quotable"
    assert "Fleuves impassibles" in quotes[0].text


def test_thin_corpus_refuses_instead_of_guessing():
    result = answer_question("Why is Humpty Dumpty a political allegory?",
                             work_id="humpty-dumpty")
    answer = result.answer
    assert answer.sufficiency == Sufficiency.INSUFFICIENT
    assert answer.thesis.lower().startswith("we can't answer this well yet")
    assert not any(b.kind == BlockKind.THESIS for b in answer.blocks)


def test_deepening_returns_the_quote_behind_a_claim():
    result = answer_question("Who was Op. 111 dedicated to?",
                             work_id="beethoven-op111")
    block = next(b for b in result.answer.blocks if b.kind == BlockKind.CONTEXT)
    deep = deepen(result.answer, block.id, result.pack, result.sources)
    assert deep.quotes
    quote = deep.quotes[0]
    assert quote.span.text in result.sources[quote.source_id].text


def test_export_carries_attribution_and_no_raw_text():
    result = answer_question("When was To Pimp a Butterfly released?",
                             work_id="kendrick-tpab")
    markdown = to_markdown(result.answer, result.pack, result.sources)
    assert "## Attribution" in markdown
    for entry in result.answer.attribution:
        assert entry.author in markdown

    payload = to_jsonable(result.answer, result.pack, result.sources)
    assert all(not source["text_included"] for source in payload["sources"])
    assert payload["attribution"]


def test_ambiguous_question_returns_candidates_not_an_answer():
    from src.retrieval.entity_resolver import Resolution

    result = answer_question("the sad piano piece")
    assert isinstance(result, Resolution)
    assert result.candidates == [] or result.is_ambiguous


def test_timeline_keeps_document_provenance():
    result = answer_question("When was Op. 111 written?", work_id="beethoven-op111")
    composed = next(e for e in result.answer.timeline if e.event_type == "composition")
    assert composed.attestations
    assert composed.date_asserted_by_document is not None


def test_fixture_mode_is_the_default_under_test():
    assert settings.mode == "fixture"
