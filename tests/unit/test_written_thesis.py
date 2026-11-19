"""Where the writer meets the document: gated prose replaces the template thesis.

The assembler can still write the whole document on its own. When a synthesizer
is supplied, it is asked for prose over the verified claims and the gate decides
what survives — the same rule as §4.7, checked here on a real recorded corpus
rather than on hand-built claims, because a contract that only holds on fixtures
is a contract that holds nowhere.
"""

from __future__ import annotations

import pytest

from src.answer.assemble import assemble
from src.answer.claims import from_pack
from src.answer.writer import Proposal
from src.service import answer_question, clear_cache

pytestmark = pytest.mark.usefixtures("fixture_mode")


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


def test_gated_prose_becomes_the_thesis_and_an_invented_sentence_does_not():
    result = answer_question(
        "Why does the second movement sound so plain?", work_id="beethoven-op111"
    )
    claims = [c for group in from_pack(result.pack, result.sources).values() for c in group]
    assert claims
    cited = claims[0]
    sentence = cited.spans[0].text.strip()

    def synthesize(available, question):
        return [
            Proposal(text=sentence, claim_ids=(cited.id,)),
            Proposal(text="It was composed in Vienna.", claim_ids=(cited.id,)),
        ]

    answer = assemble(
        result.work, result.interpretation, result.pack, result.sources,
        synthesize=synthesize,
    )

    printed = " ".join(block.text for block in answer.blocks)
    assert sentence in answer.thesis
    assert "Vienna" not in printed
    known = {claim.id for claim in claims}
    for block in answer.blocks:
        assert set(block.claim_ids) <= known, "a block may not cite a claim we dropped"


def test_without_a_synthesizer_the_document_is_the_template_one():
    result = answer_question(
        "Why does the second movement sound so plain?", work_id="beethoven-op111"
    )
    answer = assemble(result.work, result.interpretation, result.pack, result.sources)
    assert answer.model == "deterministic-v1"
    assert answer.blocks
