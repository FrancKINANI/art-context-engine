"""An unanswered work is an invitation, and the page has to say so.

A refusal used to end with the same small "no margin notes yet" line as a
finished document: the reader was told the page was empty and left with no
action. The two states are not the same. A document that answered has nothing
to add; a document that could not answer has everything to add, and the reader
who has read a review or an interview is the only way it stops being a refusal.
"""

from __future__ import annotations

from src.api import reader
from src.models import Answer, Sufficiency
from datetime import datetime


def _answer(sufficiency: Sufficiency, **overrides) -> Answer:
    data = dict(
        work_id="w",
        question="Booba, Paname — what is he saying about Paris?",
        thesis="We can't answer this well yet.",
        sufficiency=sufficiency,
        generated_at=datetime(2026, 11, 17, 9, 0),
    )
    data.update(overrides)
    return Answer(**data)


def test_a_refusal_asks_for_the_first_reading():
    html = reader._margin_html(_answer(Sufficiency.INSUFFICIENT), [], "")
    assert "The first reading" in html
    assert "No one has written this reading yet" in html
    # The form is the page's main action, so it is not hidden behind a click.
    assert "<details class=\"add-note\" open>" in html
    assert "Add a sourced reading" in html


def test_an_answered_document_keeps_the_margin_in_the_margin():
    html = reader._margin_html(_answer(Sufficiency.SUFFICIENT), [], "")
    assert "Margin notes" in html
    assert "No verified margin notes yet" in html
    assert "<details class=\"add-note\" open>" not in html


def test_accepted_notes_replace_the_invitation_on_a_refusal():
    class Note:
        body = "The track is a postcard from a city he left."
        quote = "Paname, c'est ma ville, je l'ai dans la peau."
        source_url = "https://example.com/interview"
        author = "anon_1"

    html = reader._margin_html(_answer(Sufficiency.INSUFFICIENT), [Note()], "")
    assert "postcard from a city he left" in html
    assert "No one has written this reading yet" not in html
    # The heading still frames the page as waiting for more.
    assert "The first reading" in html


def test_open_questions_lists_only_what_was_not_answered():
    recent = [
        _answer(Sufficiency.SUFFICIENT, id="a1", question="Answered"),
        _answer(Sufficiency.INSUFFICIENT, id="a2", question="Unanswered one"),
    ]
    html = reader._open_questions(recent)
    assert "Nobody has explained these yet." in html
    assert "Unanswered one" in html
    assert "Answered" not in html


def test_open_questions_collapses_repeat_asks_of_the_same_work():
    recent = [
        _answer(Sufficiency.INSUFFICIENT, id="a1", question="What does Paname mean?"),
        _answer(Sufficiency.INSUFFICIENT, id="a2", question="what does paname mean?"),
    ]
    html = reader._open_questions(recent)
    assert html.count('class="oq-q"') == 1


def test_no_refusals_means_no_band():
    recent = [_answer(Sufficiency.SUFFICIENT, id="a1", question="Answered")]
    assert reader._open_questions(recent) == ""
    assert reader._open_questions([]) == ""


def test_a_refusal_that_got_a_reading_is_no_longer_open():
    """The band's claim is "nobody has explained these". Once a reader supplies a
    sourced reading, that claim is false even though the engine still cannot
    quote one itself."""
    recent = [_answer(Sufficiency.INSUFFICIENT, id="a1", question="Now read in the margin")]
    assert "Now read in the margin" in reader._open_questions(recent)
    assert reader._open_questions(recent, {"a1"}) == ""
