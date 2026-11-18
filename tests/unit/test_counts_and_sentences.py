"""What a source is worth, and how a date is allowed to be worded.

Both rules exist because a real page said something untrue about itself:

* a page listed one source and announced "only 2 independent sources were
  found", because a Wikidata row counted beside the article it describes;
* a page read "The work was written in completed until 1859", because the
  matched words of "was begun in 1842 but not completed until 1859" were put
  inside a sentence template.
"""

from __future__ import annotations

from src.answer.sentences import sentence_from_values
from src.models import (
    AuthorityTier,
    ContextPack,
    ExtractedClaim,
    RightsInfo,
    Source,
    SourceType,
    Span,
)
from src.temporal import day
from src.retrieval.gatherer import independent_source_count


def _source(sid: str, title: str, subtype: str, *, text: str = "a document") -> Source:
    return Source(
        id=sid,
        work_id="test-work",
        type=SourceType.SECONDARY,
        subtype=subtype,
        title=title,
        author="Someone",
        date_document=day(2020, 1, 1, display="2020-01-01"),
        text=text,
        rights=RightsInfo.cc_by(),
        authority=AuthorityTier.REFERENCE,
    )


def _claim(sid: str) -> ExtractedClaim:
    return ExtractedClaim(
        work_id="test-work",
        source_id=sid,
        span=Span(start=0, end=10, text="a document"),
        subject="test-work",
        predicate="composition_date",
        value_text="1859",
    )


def test_a_catalogue_record_is_not_an_independent_source():
    wikipedia = _source("w", "L'albatros (poem)", "encyclopedia_article")
    wikidata = _source("q", "The Albatross (Q3201688)", "structured_data")
    pack = ContextPack(
        work_id="test-work",
        sources=[wikipedia, wikidata],
        claims=[_claim("w"), _claim("q")],
    )
    assert independent_source_count(pack) == 1


def test_a_source_that_answered_no_claim_corroborates_nothing():
    first = _source("a", "Reading the poem", "scholarly_article")
    second = _source("b", "Another reading", "scholarly_article")
    silent = _source("c", "A third paper", "scholarly_article")
    pack = ContextPack(
        work_id="test-work",
        sources=[first, second, silent],
        claims=[_claim("a"), _claim("b")],
    )
    assert independent_source_count(pack) == 2


def test_the_work_itself_counts_when_it_is_quoted():
    wikipedia = _source("w", "L'albatros (poem)", "encyclopedia_article")
    poem = _source("p", "L'albatros", "primary_text", text="Souvent, pour s'amuser…")
    poem = poem.model_copy(update={"type": SourceType.PRIMARY})
    pack = ContextPack(
        work_id="test-work",
        sources=[wikipedia, poem],
        claims=[_claim("w")],
    )
    assert independent_source_count(pack) == 2


def test_matched_words_are_not_wrapped_in_a_template():
    """"completed until 1859" is a fragment; the parsed year is the date."""
    sentence = sentence_from_values(
        "composition_date", {"display": "1859"}, "completed until 1859"
    )
    assert sentence == "The work dates from 1859."


def test_a_date_the_source_spelled_out_keeps_its_words():
    assert sentence_from_values(
        "composition_date", {"display": "1871"}, "written in the summer of 1871"
    ) == "The work was written in the summer of 1871."


def test_a_full_date_takes_on_and_a_year_takes_in():
    assert sentence_from_values(
        "publication_date", {"display": "December 7, 2018"}, "released on December 7, 2018"
    ) == "It was released on December 7, 2018."
    assert sentence_from_values(
        "publication_date", {"display": "1859"}, "published in 1859"
    ) == "It was published in 1859."
