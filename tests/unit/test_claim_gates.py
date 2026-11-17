"""Which sentences may be evidence about a work, and which are accidents of name.

Three real failures, from three real documents, are pinned here:

* the La Fouine album figures printed under a Booba track, because a nearby
  article mentions Booba once;
* "enracinés dans Paname" — a film review using the slang for Paris — printed as
  an interpretation of a Slïmane single whose title is that one word;
* a film starring "Slimane Dazi" mistaken for the singer, because a surname
  contains the artist's name.

The rule they produce: a document whose title is about the work contributes
anything; every other document contributes only a sentence that names the work,
and a one-word title needs the maker beside it.
"""

from __future__ import annotations

from datetime import date

from src.models import (
    AuthorityTier,
    CreatorRef,
    RightsInfo,
    Source,
    SourceType,
    Work,
    WorkType,
)
from src.retrieval.claim_extractor import extract_pack
from src.temporal import day


def _work(title: str, maker: str, **identifiers: str) -> Work:
    return Work(
        id="test-work",
        title=title,
        type=WorkType.MUSIC,
        creators=[CreatorRef(name=maker, role="performer")],
        identifiers=identifiers,
    )


def _source(title: str, text: str, *, scholarly: bool = True, sid: str = "s1") -> Source:
    return Source(
        id=sid,
        work_id="test-work",
        type=SourceType.SECONDARY,
        subtype="article",
        title=title,
        author="Someone",
        date_document=day(2020, 1, 1, display="2020-01-01"),
        url="https://example.com/page",
        text=text,
        rights=RightsInfo.cc_by(),
        authority=AuthorityTier.SCHOLARLY if scholarly else AuthorityTier.REFERENCE,
    )


PAPER = _source(
    "Rap en français : dire « peuple » rend-il « populaire » ?",
    "Les réalisateurs filment ce monde urbain, fièrement enracinés dans Paname. "
    "Le mot « peuple » revient soixante-six fois dans ce corpus de textes de rap. ",
)

SONG_ARTICLE = _source(
    "Paname (chanson de Slimane)",
    "The single was published in May 2016 and stayed on the francophone charts "
    "all summer, which made it the most played track of the album. ",
    scholarly=False,
    sid="s2",
)


def test_a_word_that_is_also_a_place_name_is_not_enough():
    work = _work("Paname", "Slimane")
    assert extract_pack([PAPER], work) == []


def test_the_same_sentence_names_a_maker_too_so_it_counts():
    work = _work("Paname", "Slimane")
    # The sentence still names the work and the maker — the test is about that,
    # not about the wording. It is phrased so that it also reads as a reading,
    # which is now required: a sentence that only mentions the work is not
    # evidence of what the work means.
    paper = _source(
        "Rap en français : dire « peuple » rend-il « populaire » ?",
        "Slimane place Paname au centre de son disque : le titre évoque à la fois "
        "la ville et un personnage. ",
    )
    claims = extract_pack([paper], work)
    assert claims, "the sentence names both the work and its maker"


def test_an_article_titled_after_the_work_needs_no_mention_in_every_sentence():
    work = _work("Paname", "Slimane")
    claims = extract_pack([SONG_ARTICLE], work)
    assert claims, "the whole page is about the song"


def test_a_maker_name_inside_another_name_is_not_evidence():
    """"Slimane Dazi" is not Slimane: a surname that contains the artist's name
    says nothing about the song."""
    work = _work("Paname", "Slimane")
    review = _source(
        "My French Film Festival",
        "Après deux ans de prison, Nas (Reda Kateb) retrouve son frère Slimane Dazi. ",
    )
    assert extract_pack([review], work) == []


def test_a_multiword_title_is_its_own_identity():
    work = _work("Le Bateau ivre", "Arthur Rimbaud")
    paper = _source(
        "Poétique du naufrage chez Rimbaud",
        "Le Bateau ivre évoque la dérive et la perte de contrôle, "
        "et le poème a été composé en 1871 avant le départ de Rimbaud. ",
    )
    assert extract_pack([paper], work)


def test_a_paper_whose_own_title_names_the_work_is_about_it():
    """The catalogue title and the article title rarely match word for word:
    "Piano Sonata No. 32" against "Piano Sonata in C Minor, Op. 111"."""
    work = _work("Piano Sonata No. 32 in C minor, Op. 111", "Beethoven")
    paper = _source(
        "Piano Sonata in C Minor, Op. 111: Beethoven's Last Piano Sonatas",
        "The sonata was completed in 1822, and its second movement is a set of "
        "variations that begins with a single C major chord. ",
    )
    assert extract_pack([paper], work)


def test_a_date_claim_from_the_catalogue_is_not_dropped():
    """Structured claims carry no prose, so the gate cannot read a title in them.
    The catalogue record is the work's own; it must survive."""
    work = _work("Paname", "Slimane", wikidata_qid="Q1")
    line = "P577 publication_date: 2016-05-20 (precision day)"
    body = line + "\nPaname — single by Slimane; label: Columbia; language: French."
    record = _source("Paname (Q1)", body, scholarly=False, sid="wd").model_copy(
        update={
            "subtype": "structured_data",
            "metadata": {"statements": [
                {"property": "P577", "line": line, "value_text": "2016-05-20"},
            ]},
        }
    )
    assert extract_pack([record], work)


def test_a_measurement_is_not_a_reading():
    """The open literature on Vermeer's painting is mostly pigment mapping.

    Quoting "this dataset contains the XRF mapping of painting reconstruction"
    as the painting's meaning is true to the source and false to the reader, so
    a scholarly source contributes only a sentence that reads like a reading.
    """
    work = Work(
        id="test-work", title="Girl with a Pearl Earring", type=WorkType.VISUAL_ART,
        creators=[CreatorRef(name="Johannes Vermeer", role="creator")],
    )
    technical = _source(
        "Mapping the pigment distribution of Vermeer's Girl with a Pearl Earring",
        "As part of the 2018 Girl in the Spotlight project, reflectance imaging "
        "spectroscopy and X-ray fluorescence scanning were performed on Johannes "
        "Vermeer's Girl with a Pearl Earring with the goal of understanding the "
        "distribution of pigments. Prior analysis of cross-sections in the 1990s "
        "established the layer structure, and the present dataset extends it. ",
    )
    assert extract_pack([technical], work) == []

    criticism = _source(
        "Girl with a Pearl Earring: a history of its readings",
        "Vermeer's Girl with a Pearl Earring has been widely read as a study of "
        "the gaze rather than a portrait, and the pearl itself has been argued to "
        "be polished tin. Critics have suggested that the tronie format frees her "
        "from any name the sitter would have carried. ",
        sid="s2",
    )
    claims = extract_pack([criticism], work)
    assert claims and claims[0].predicate == "interpretation"
