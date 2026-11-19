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


# ═══════════════════════════════════════════════════════════════════════════
# HOW THE READER SPELLED IT, AND HOW FAMOUS THE WORK IS
# ═══════════════════════════════════════════════════════════════════════════

def test_an_article_is_not_part_of_a_title():
    """"le monalisa" is two words, one of them an article, and the catalogue
    writes the title as two other words. Both are the same single thing."""
    assert ow._says_only_title("monalisa", "Mona Lisa")
    assert ow._says_only_title("le monalisa", "Mona Lisa")
    assert ow._says_only_title("the Mona Lisa", "Mona Lisa")
    # A pronoun is not an article: "with it" says more than the title "It".
    assert not ow._says_only_title("with it", "It")
    # Naming a place is naming something else as well.
    assert not ow._says_only_title("Mona Lisa Louvre", "Mona Lisa")
    assert not ow._says_only_title("", "Mona Lisa")


def test_notoriety_is_measured_in_languages():
    assert ow._prominence(0) == 0.0
    assert ow._prominence(1) < ow._prominence(10) < ow._prominence(60) < ow._prominence(250)
    assert ow._prominence(250) == 1.0
    # The bar is about sixty Wikipedias: a painting clears it, a 2022 single
    # with an article in three languages does not.
    assert ow._prominence(60) < ow._FAMOUS <= ow._prominence(64)


def test_a_bare_title_ranks_but_never_concludes():
    from src.retrieval.entity_resolver import AMBIGUITY_THRESHOLD, _capped

    scores = [_capped(s) for s in (0.0, 0.4, 0.7, 0.95, 1.0)]
    assert scores == sorted(scores)
    assert len(set(scores)) > 1, "flattening the demotion loses the ordering"
    assert max(scores) < AMBIGUITY_THRESHOLD


def test_a_famous_work_is_answered_and_an_obscure_one_is_asked_about():
    """A bare title is not an identity — unless one work is known everywhere."""
    from src.models import CreatorRef, Work
    from src.retrieval.entity_resolver import _score_work

    def work(prominence: float) -> Work:
        return Work(
            id="wikidata:Q12418", title="Mona Lisa", type=WorkType.VISUAL_ART,
            creators=[CreatorRef(name="Leonardo da Vinci", role="author")],
            identifiers={"wikidata_qid": "Q12418"},
            metadata={"prominence": prominence},
        )

    famous = _score_work(work(1.0), "Mona Lisa")
    obscure = _score_work(work(0.2), "Mona Lisa")
    assert famous[0] > obscure[0]
    assert "title only" in obscure[1]


def test_a_search_hit_for_the_same_article_is_not_a_second_choice():
    """Wikidata's item and Wikipedia's article are one painting, not two."""
    from src.retrieval.entity_resolver import _dedupe_same_work

    item = WorkCandidate(
        work_id="wikidata:Q12418", title="Mona Lisa", creator="Leonardo da Vinci",
        work_type=WorkType.VISUAL_ART, confidence=0.98, matched_on="wikidata match",
        identifiers={"wikidata_qid": "Q12418", "wikipedia_sitelink": "Mona Lisa"},
    )
    article = WorkCandidate(
        work_id="wikipedia:Mona Lisa", title="Mona Lisa", creator="unknown",
        work_type=WorkType.VISUAL_ART, confidence=0.95, matched_on="wikipedia match",
        identifiers={"wikipedia_title": "Mona Lisa"},
    )
    kept = _dedupe_same_work([item, article])
    assert [c.work_id for c in kept] == ["wikidata:Q12418"]

    # A different article that merely shares the title is a different work.
    song = WorkCandidate(
        work_id="wikipedia:Mona Lisa (song)", title="Mona Lisa (song)",
        creator="Nat King Cole", work_type=WorkType.MUSIC, confidence=0.9,
        matched_on="wikipedia match", identifiers={"wikipedia_title": "Mona Lisa (song)"},
    )
    assert len(_dedupe_same_work([item, song])) == 2


def test_an_exact_title_still_has_to_answer_to_the_maker():
    """"Debussy, Clair de Lune" is two pieces and one short story. The title is
    exact for all of them; only the maker says which one the reader meant."""
    context = "Debussy, Clair de Lune — what is it about?"
    music = ow._score("Clair de lune", "Claude Debussy", "Clair de lune",
                      WorkType.MUSIC, context)
    story = ow._score("Clair de lune", "Guy de Maupassant", "Clair de lune",
                      WorkType.LITERATURE, context)
    assert music > story


def test_a_wikipedia_hit_sends_us_to_wikidata_for_the_identity(monkeypatch):
    """Wikidata's search does not forgive a spelling; Wikipedia's does. When
    the item is missing, the article is the lead — not the answer."""
    from src.config import settings
    from src.models import CreatorRef, Work

    asked: list[str] = []

    def wikipedia(query, backend, limit, context=""):
        return [ow.Candidate(
            work=Work(id="wikipedia:Mona Lisa", title="Mona Lisa",
                      type=WorkType.VISUAL_ART),
            score=0.95, why="wikipedia match",
        )]

    def wikidata(query, backend, limit, context=""):
        asked.append(query)
        if query.lower().replace(" ", "") == "monalisa" and query != "Mona Lisa":
            # Wikidata does not forgive the spelling: the search finds nothing.
            return []
        return [ow.Candidate(
            work=Work(id="wikidata:Q12418", title="Mona Lisa",
                      type=WorkType.VISUAL_ART,
                      creators=[CreatorRef(name="Leonardo da Vinci", role="author")],
                      identifiers={"wikidata_qid": "Q12418"},
                      metadata={"prominence": 1.0}),
            score=0.98, why="wikidata match",
        )]

    monkeypatch.setattr(settings, "mode", "live")
    monkeypatch.setattr(ow, "_deezer", lambda *a, **k: [])
    monkeypatch.setattr(ow, "_wikipedia", wikipedia)
    monkeypatch.setattr(ow, "_wikidata", wikidata)

    found = ow.search("monalisa", backend=object(), limit=6)
    # The first lookup is the reader's spelling and finds nothing; the second is
    # the title Wikipedia just gave us.
    assert asked == ["monalisa", "Mona Lisa"]
    assert found[0].work.id == "wikidata:Q12418"
    assert found[0].work.primary_creator.name == "Leonardo da Vinci"

    # An item that is already famous is not what the search fumbled: one lookup,
    # no second attempt.
    asked.clear()
    ow.search("Mona Lisa", backend=object(), limit=6)
    assert asked == ["Mona Lisa"]


def test_a_question_around_a_title_is_not_part_of_the_title():
    """“what does monalisa mean” searched catalogues for “monalisa mean” and
    offered two songs that happened to contain the word “mean”."""
    from src.retrieval.entity_resolver import _focus, _subject, _title_like

    assert _title_like("what does monalisa mean") == "monalisa"
    assert _title_like("monalisa meaning") == "monalisa"
    assert _title_like("Debussy, Clair de Lune — what is it about?") == "Debussy, Clair de Lune"
    assert _subject("what does monalisa mean") == "monalisa"
    assert _focus("what does monalisa mean") == "monalisa"
    # A title that begins with a question word is a title, and a colon is part
    # of the name.
    assert _title_like("What's Going On") == "What's Going On"
    assert _title_like("2001: A Space Odyssey") == "2001: A Space Odyssey"


def test_a_track_that_repeats_the_query_words_does_not_cancel_the_maker():
    """A Deezer track called “HOKUSAI ~The Great Wave~” contains every content
    word of “Hokusai, The Great Wave”, so the bare-title guard fired and the
    namesake was never demoted — the painting lost its lead."""
    ranked = [
        _candidate("The Great Wave off Kanagawa", "Katsushika Hokusai", 0.96),
        _candidate("The Great Wave, Sete", "unknown", 0.92),
        _candidate("HOKUSAI ~The Great Wave~", "Charlie's Music Factory", 0.52),
    ]
    _demote_namesakes(ranked, "Hokusai, The Great Wave — what does it show?")
    assert ranked[0].title == "The Great Wave off Kanagawa"
    assert ranked[1].confidence < 0.92
