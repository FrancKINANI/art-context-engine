"""A model may propose sentences; only spans decide which ones are printed.

The LLM is allowed to write. It is not allowed to *know*: every sentence it
proposes must point at claims we already read, and every fact inside that
sentence — a year, a number, a quotation — must come from the span it cites.

Each test below is a way a fluent model produces a false sentence, and the gate
is the only reason the reader never sees it. The model is a fake throughout: the
gate must hold without a key, so it can be tested offline and audited later.
"""

from __future__ import annotations

from src.answer.writer import Dropped, Proposal, gate, write
from src.models import (
    AuthorityTier,
    Claim,
    RightsInfo,
    Source,
    SourceType,
    Span,
    Sufficiency,
)
from src.temporal import day

FIRST = "Debussy wrote the suite in 1905."
SECOND = (
    "The third movement sets Verlaine's poem \"Clair de lune\", though the poet "
    "is never named in the score."
)
TEXT = f"{FIRST} {SECOND}"


def _source(source_id: str = "wiki") -> Source:
    return Source(
        id=source_id,
        work_id="w",
        type=SourceType.SECONDARY,
        subtype="encyclopedia_article",
        title="Clair de lune",
        author="Wikipedia contributors",
        date_document=day(2024, 1, 1, display="2024-01-01"),
        text=TEXT,
        rights=RightsInfo.cc_by_sa(),
        authority=AuthorityTier.REFERENCE,
    )


def _span_on(fragment: str) -> Span:
    start = TEXT.index(fragment)
    return Span(start=start, end=start + len(fragment), text=fragment)


def _claim(claim_id: str = "c-1", *, on: str = FIRST, **overrides) -> Claim:
    data = dict(
        id=claim_id,
        text=on,
        source_ids=["wiki"],
        claim_ids=["x-1"],
        spans=[_span_on(on)],
    )
    data.update(overrides)
    return Claim(**data)


def test_a_sentence_with_no_claim_is_dropped():
    kept, dropped = gate([Proposal(text="Jazz was born in New Orleans.", claim_ids=())],
                         [_claim()], {"wiki": _source()})
    assert not kept
    assert dropped[0].reason == "cites no claim"


def test_a_sentence_citing_a_claim_that_is_not_here_is_dropped():
    kept, dropped = gate([Proposal(text="It is about the moon.", claim_ids=("c-999",))],
                         [_claim()], {"wiki": _source()})
    assert not kept
    assert dropped[0].reason == "cites a claim that is not in this document"


def test_a_sentence_whose_span_does_not_resolve_is_dropped():
    """The span was extracted from a source we no longer hold, or was edited."""
    broken = _claim(spans=[Span(start=0, end=5, text="Debussy")])
    kept, dropped = gate([Proposal(text="Debussy wrote it.", claim_ids=("c-1",))],
                         [broken], {"wiki": _source()})
    assert not kept
    assert dropped[0].reason == "the span it cites does not resolve in its source"


def test_a_sentence_inventing_a_year_is_dropped():
    """The span is about Verlaine; it never says when the suite was written."""
    claim = _claim(on=SECOND)
    kept, dropped = gate(
        [Proposal(text="The suite was written in 1905.", claim_ids=("c-1",))],
        [claim], {"wiki": _source()},
    )
    assert not kept
    assert dropped[0].reason == "states a number no cited span contains"

    kept, _ = gate(
        [Proposal(text="The third movement sets Verlaine's poem.", claim_ids=("c-1",))],
        [claim], {"wiki": _source()},
    )
    assert kept


def test_a_sentence_that_rewrites_a_quotation_is_dropped():
    """§3.7 rule 3: a quotation is copied, never paraphrased into quotes."""
    claim = _claim(on=SECOND)
    kept, dropped = gate(
        [Proposal(text='It sets "Clair de Lune" by Verlaine.', claim_ids=("c-1",))],
        [claim], {"wiki": _source()},
    )
    assert not kept
    assert dropped[0].reason == "quotes text no cited span contains"


def test_a_verbatim_quotation_from_the_cited_span_is_kept():
    claim = _claim(on=SECOND)
    kept, _ = gate(
        [Proposal(text='It sets "Clair de lune", though Verlaine is not named.',
                  claim_ids=("c-1",))],
        [claim], {"wiki": _source()},
    )
    assert kept


def test_a_paraphrase_without_quotation_marks_survives():
    """Silence about the poet is a reading of the score, not a quotation."""
    claim = _claim(on=SECOND)
    kept, dropped = gate(
        [Proposal(text="The movement sets Verlaine, who is never named in the score.",
                  claim_ids=("c-1",))],
        [claim], {"wiki": _source()},
    )
    assert kept and not dropped


def test_a_supported_sentence_is_kept_with_every_claim_it_cites():
    proposal = Proposal(text=FIRST, claim_ids=("c-1",))
    kept, dropped = gate([proposal], [_claim()], {"wiki": _source()})
    assert kept == [proposal]
    assert not dropped


def test_a_sentence_may_repeat_the_work_record():
    """The model may name the work, its maker and its date: catalogue facts."""
    claim = _claim(on=SECOND)
    kept, _ = gate(
        [Proposal(text="Clair de lune by Claude Debussy was written in 1905.",
                  claim_ids=("c-1",))],
        [claim], {"wiki": _source()},
        known_values=("Clair de lune", "Claude Debussy", "1905"),
    )
    assert kept


def test_the_same_sentence_is_not_printed_twice():
    proposal = Proposal(text=FIRST, claim_ids=("c-1",))
    kept, _ = gate([proposal, proposal], [_claim()], {"wiki": _source()})
    assert len(kept) == 1


def test_a_refusal_never_calls_the_model():
    """No evidence means no call: unattributed prose is what we refuse."""
    calls: list[str] = []

    def synthesize(claims, question):
        calls.append(question)
        return [Proposal(text="It is about the moon.", claim_ids=("c-1",))]

    kept, dropped = write(
        question="What does it mean?",
        claims=[_claim()],
        sources={"wiki": _source()},
        synthesize=synthesize,
        sufficiency=Sufficiency.INSUFFICIENT,
    )
    assert kept == [] and dropped == [] and calls == []


def test_the_model_proposes_and_the_gate_disposes():
    def synthesize(claims, question):
        return [
            Proposal(text=FIRST, claim_ids=("c-1",)),
            Proposal(text="It was composed in Vienna.", claim_ids=("c-1",)),
        ]

    kept, dropped = write(
        question="When was it written?",
        claims=[_claim()],
        sources={"wiki": _source()},
        synthesize=synthesize,
        sufficiency=Sufficiency.SUFFICIENT,
    )
    assert [p.text for p in kept] == [FIRST]
    assert isinstance(dropped[0], Dropped)
    assert dropped[0].reason == "does not draw on the span it cites"


def test_an_enormous_sentence_is_refused_rather_than_analysed():
    """A synthesizer is untrusted input too: it must not be able to hand the
    gate a megabyte to scan."""
    proposal = Proposal(text="Debussy " * 2000 + "wrote the suite in 1905.",
                        claim_ids=("c-1",))
    kept, dropped = gate([proposal], [_claim()], {"wiki": _source()})
    assert not kept
    assert "sentence" in dropped[0].reason
