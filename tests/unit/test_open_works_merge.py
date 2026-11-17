"""What a search result is allowed to claim about a work.

Two mistakes are easy here and both are silent: taking a Wikipedia *search hit*
for the work's own article, and merging two different works because they share a
title. A wrong merge does not look wrong on the page — it puts another song's
release date under this one.
"""

from __future__ import annotations

from src.models import CreatorRef, Work, WorkType
from src.retrieval import open_works as ow


def _candidate(title: str, maker: str, **identifiers: str) -> ow.Candidate:
    work = Work(
        id=next(iter(identifiers.values())),
        title=title,
        type=WorkType.MUSIC,
        creators=[CreatorRef(name=maker, role="performer")] if maker else [],
        identifiers=identifiers,
    )
    return ow.Candidate(work=work, score=0.9, why="test")


def test_sitelinks_are_the_pinned_article_in_the_best_language():
    links = {
        "commonswiki": {"title": "Category:Paname"},
        "frwiki": {"title": "Paname (chanson)"},
        "dewiki": {"title": "Paname (Lied)"},
    }
    assert ow._wikipedia_sitelink(links) == ("Paname (chanson)", "fr")
    links["enwiki"] = {"title": "Paname (song)"}
    assert ow._wikipedia_sitelink(links) == ("Paname (song)", "en")


def test_a_work_without_an_article_has_no_sitelink():
    assert ow._wikipedia_sitelink({"commonswiki": {"title": "Category:X"}}) is None
    assert ow._wikipedia_sitelink({}) is None


def test_merging_two_catalogues_keeps_the_ids_but_not_a_search_guess():
    """Deezer knows the date, Wikidata knows the article. A Wikipedia search hit
    knows neither: it is a guess at the article, and it must not travel."""
    deezer = _candidate("Paname", "Booba", deezer_id="143859868")
    wikidata = _candidate(
        "Paname", "Booba", wikidata_qid="Q1",
        wikipedia_sitelink="Paname (chanson)", wikipedia_lang="fr",
    )
    search_hit = _candidate("Paname", "Booba", wikipedia_title="Paris")

    merged = ow._with_identifiers(deezer, wikidata).work.identifiers
    assert merged["deezer_id"] == "143859868"
    assert merged["wikipedia_sitelink"] == "Paname (chanson)"
    assert merged["wikipedia_lang"] == "fr"

    merged = ow._with_identifiers(deezer, search_hit).work.identifiers
    assert "wikipedia_title" not in merged
    assert merged["deezer_id"] == "143859868"
