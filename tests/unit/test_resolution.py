"""Which work a question is about — the failure that cannot be seen on the page.

A document about the wrong work reads perfectly. It cites dated sources, it
disagrees with itself in the right places, and every sentence is true of
*something*. That is why each rule below is pinned: they were found by asking
real questions and reading the answers, and every one of them was invisible
until the answer was checked against the work it claimed to explain.
"""

from __future__ import annotations

from src.models import WorkType
from src.retrieval import open_works as ow
from src.retrieval.entity_resolver import _demote_namesakes, _focus, _subject
from src.models import WorkCandidate


def test_the_subject_is_the_title_exactly_as_written():
    """"Girl with Pearl Earring" — article dropped — is not a lookup Wikidata
    answers. It returned nothing, so the painting became "unknown"."""
    assert _subject("Vermeer, Girl with a Pearl Earring — what does the painting mean?") \
        == "Girl with a Pearl Earring"
    assert _subject("Kubrick, 2001: A Space Odyssey — what does the ending mean?") \
        == "2001: A Space Odyssey"
    assert _subject("Booba, Paname — what is he saying about Paris?") == "Paname"
    # No comma: the whole head is the best guess at a title. What it must not be
    # is a stopword-stripped version of it — "Et Arcadia ego" finds nothing.
    assert _subject("Et in Arcadia ego") == "Et in Arcadia ego"


def test_a_colon_is_part_of_a_title_not_an_instruction():
    assert _focus("Kubrick, 2001: A Space Odyssey — what does the ending mean?") \
        == "Kubrick 2001 Space Odyssey"


def test_a_track_about_a_painting_is_not_the_painting():
    """Deezer is full of "The Rap History of Art" tracks whose title *is* the
    painting, painter included. The maker decides."""
    context = "Vermeer, Girl with a Pearl Earring — what does the painting mean?"
    painting = ow._score(
        "Girl with a Pearl Earring", "Johannes Vermeer",
        "Vermeer Girl with a Pearl Earring", WorkType.VISUAL_ART, context,
    )
    track = ow._score(
        "Girl With a Pearl Earring (Vermeer, C.1665)", "The Rap History of Art",
        "Vermeer Girl with a Pearl Earring", WorkType.MUSIC, context,
    )
    assert painting > track


def test_the_form_the_question_names_constrains_the_candidate():
    """"What does the poem mean" is not answered by a recording of the poem."""
    context = "Baudelaire, L'Albatros — what is the poem saying?"
    poem = ow._score("L'albatros", "Charles Baudelaire", "Baudelaire L'Albatros",
                     WorkType.POETRY, context)
    recording = ow._score("L'albatros", "Charles Baudelaire", "Baudelaire L'Albatros",
                          WorkType.MUSIC, context)
    assert poem > recording


def test_a_maker_who_is_not_this_work_demotes_it():
    context = "Goya, Saturn Devouring His Son — what does it show?"
    right = ow._score("Saturn Devouring His Son", "Francisco Goya",
                      "Goya Saturn Devouring His Son", WorkType.VISUAL_ART, context)
    wrong = ow._score("Saturn Devouring His Son", "RocketBass",
                      "Goya Saturn Devouring His Son", WorkType.VISUAL_ART, context)
    assert right > wrong


def _candidate(title: str, creator: str, confidence: float) -> WorkCandidate:
    return WorkCandidate(
        work_id=title.lower(), title=title, creator=creator,
        work_type=WorkType.VISUAL_ART, confidence=confidence, matched_on="test",
    )


def test_a_maker_is_named_by_any_of_their_words():
    """"Nicolas Poussin" is named by the query's "Poussin". Requiring the full
    name made the namesake rule never fire, so Guercino tied with Poussin."""
    ranked = [
        _candidate("Et in Arcadia ego", "Nicolas Poussin", 0.95),
        _candidate("Et in Arcadia ego", "Guercino", 0.95),
        _candidate("Et in Arcadia ego", "unknown", 0.95),
    ]
    _demote_namesakes(ranked, "Poussin, Et in Arcadia ego — what does the painting say?")
    assert ranked[0].creator == "Nicolas Poussin"
    assert ranked[1].confidence < 0.95


def test_nobody_named_means_nobody_is_demoted():
    ranked = [_candidate("Paname", "Booba", 0.9), _candidate("Paname", "Léo Ferré", 0.9)]
    _demote_namesakes(ranked, "Paname — what does it mean?")
    assert [c.confidence for c in ranked] == [0.9, 0.9]


def test_a_remix_is_a_version_not_a_second_work():
    from src.retrieval.entity_resolver import _base_title, _dedupe_same_work

    assert _base_title("Djadja (feat. Maluma) (Remix)") == _base_title("Djadja")
    assert _base_title("Veridis Quo (Edit)") == _base_title("Veridis Quo")

    # A qualifier that names a different work is not a version marker.
    assert _base_title("Girl with a Pearl Earring (film)") != _base_title("Girl with a Pearl Earring")

    ranked = [
        _candidate("Djadja (feat. Maluma) (Remix)", "Aya Nakamura", 0.95),
        _candidate("Djadja", "Aya Nakamura", 0.95),
    ]
    kept = _dedupe_same_work(ranked)
    assert len(kept) == 1
    assert kept[0].title == "Djadja"


def test_a_work_is_named_in_its_own_language_not_the_search_box_translation():
    """Searching "L'Albatros" returns the poem labelled "The Albatross".

    The display label is a translation into the search language; it shares no
    token with the question, so the poem scored zero and was dropped as a stub
    while a Joe Dassin single won the query. The matched text is the name the
    work is actually called.
    """
    from urllib.parse import parse_qs, urlsplit

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    poem = {
        "labels": {"fr": {"value": "L'Albatros"}, "en": {"value": "The Albatross"}},
        "descriptions": {"en": {"value": "poem by Charles Baudelaire"}},
        "claims": {
            "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q7725634"}}}}],
            "P50": [{"mainsnak": {"datavalue": {"value": {"id": "Q501"}}}}],
        },
        "sitelinks": {"frwiki": {"title": "L'Albatros"}},
    }

    class Backend:
        def get(self, url):
            params = parse_qs(urlsplit(url).query)
            if params["action"][0] == "wbsearchentities":
                return Response({"search": [{
                    "id": "Q3201688", "label": "The Albatross",
                    "match": {"type": "label", "language": "mul", "text": "L'Albatros"},
                }]})
            if params.get("ids") == ["Q3201688"]:
                return Response({"entities": {"Q3201688": poem}})
            return Response({"entities": {
                "Q501": {"labels": {"en": {"value": "Charles Baudelaire"}}},
            }})

    hits = ow._wikidata("L'Albatros", Backend(), 6,
                        "Baudelaire, L'Albatros — what is the poem saying?")
    assert hits, "the poem was dropped again"
    assert hits[0].work.title == "L'Albatros"
    assert hits[0].work.primary_creator.name == "Charles Baudelaire"
    assert hits[0].work.identifiers["wikidata_qid"] == "Q3201688"


def test_one_poem_read_aloud_is_still_one_work():
    """Deezer files recitations under "Hugo Victor". The same poem then tied
    with itself at 0.95 and the question came back ambiguous."""
    from src.retrieval.entity_resolver import _dedupe_same_work

    poem = WorkCandidate(
        work_id="wikidata:Q3021978", title="Demain dès l'aube", creator="Victor Hugo",
        work_type=WorkType.LITERATURE, confidence=0.95, matched_on="test",
        identifiers={"wikidata_qid": "Q3021978"},
    )
    recital = WorkCandidate(
        work_id="deezer:277262", title="Demain dès l'aube", creator="Hugo Victor",
        work_type=WorkType.MUSIC, confidence=0.95, matched_on="test",
        identifiers={"deezer_id": "277262"},
    )
    kept = _dedupe_same_work([recital, poem])
    assert len(kept) == 1
    assert kept[0].identifiers.get("wikidata_qid") == "Q3021978"
