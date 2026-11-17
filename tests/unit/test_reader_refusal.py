"""A refusal is still a document: it has to show that we looked.

The failure this pins is the dead end. A page that says "we can't answer this
yet" and cites nothing gives the reader no way to tell a thin corpus from a bug,
and no way to follow the trail themselves.
"""

from __future__ import annotations

from src.api import reader
from src.models import (
    Answer,
    AuthorityTier,
    ContextPack,
    RightsInfo,
    Source,
    SourceType,
    Sufficiency,
)
from src.temporal import day


def _source(**overrides) -> Source:
    data = dict(
        id="wikipedia:1",
        work_id="w",
        type=SourceType.TERTIARY,
        subtype="encyclopedia_article",
        title="Paname (chanson de Slimane)",
        author="Wikipedia contributors",
        date_document=day(2024, 3, 11, display="2024-03-11"),
        url="https://fr.wikipedia.org/?curid=1",
        text="Le single est publié en 2016.",
        rights=RightsInfo.cc_by_sa(),
        authority=AuthorityTier.REFERENCE,
    )
    data.update(overrides)
    return Source(**data)


def _answer(sufficiency: Sufficiency) -> Answer:
    return Answer(
        work_id="w",
        question="What does Paname mean?",
        thesis="We can't answer this well yet.",
        sufficiency=sufficiency,
    )


def test_a_refusal_lists_what_was_read():
    pack = ContextPack(work_id="w", sources=[_source()])
    html = reader._consulted_html(_answer(Sufficiency.INSUFFICIENT), pack)
    assert "Read, and not enough" in html
    assert "Paname (chanson de Slimane)" in html
    assert "https://fr.wikipedia.org/?curid=1" in html
    # A source that earned no claim contributes no text either.
    assert "Le single est publié en 2016." not in html


def test_an_answered_document_does_not_show_the_consulted_list():
    pack = ContextPack(work_id="w", sources=[_source()])
    assert reader._consulted_html(_answer(Sufficiency.SUFFICIENT), pack) == ""


def test_a_refusal_with_nothing_read_has_no_section():
    assert reader._consulted_html(_answer(Sufficiency.INSUFFICIENT), ContextPack(work_id="w")) == ""


def test_the_header_does_not_say_zero_sources_when_it_read_three():
    """"0 sources" reads as "we found nothing"; three were read and none was usable."""
    pack = ContextPack(work_id="w", sources=[_source(), _source(id="b"), _source(id="c")])
    assert reader._source_count(_answer(Sufficiency.INSUFFICIENT), pack) == "3 read, none usable"
    assert reader._source_count(_answer(Sufficiency.SUFFICIENT), pack) == "0 sources"
