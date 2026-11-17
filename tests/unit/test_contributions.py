"""The contribution check: a fetch and a substring, and nothing more.

These tests pin the two failure modes that would make the margin worthless:
accepting a sentence the page does not contain, and letting a reader turn the
server into a probe of its own network.
"""

from __future__ import annotations

import pytest

from src import contributions
from src.models import Contribution, ContributionStatus
from src.retrieval import http


def _contribution(quote: str = "a" * 60, url: str = "https://example.com/page") -> Contribution:
    return Contribution(answer_id="a1", author="anon_x", body="A note.", quote=quote,
                        source_url=url)


class _Page:
    """A backend that serves one page without touching the network."""

    def __init__(self, body: str, status: int = 200) -> None:
        self.body, self.status = body, status

    def get(self, url: str) -> http.Response:
        return http.Response(url=url, status=self.status, body=self.body,
                             content_type="text/html")


def test_quotation_found_on_the_page_is_accepted(monkeypatch):
    monkeypatch.setattr(contributions, "is_public_url", lambda url: True)
    monkeypatch.setattr(contributions, "get_backend", lambda: _Page(
        "<html><body><p>The album shipped in March 2017, and the tour followed.</p></body></html>"
    ))
    monkeypatch.setattr(contributions.settings, "mode", "live")

    checked = contributions.verify(_contribution("The album shipped in March 2017, and the tour followed."))
    assert checked.status is ContributionStatus.ACCEPTED


def test_quotation_absent_is_rejected_and_not_pending(monkeypatch):
    """"We looked and it is not there" is a different statement from "we could
    not look", and only the first one is about the contributor."""
    monkeypatch.setattr(contributions, "is_public_url", lambda url: True)
    monkeypatch.setattr(contributions, "get_backend", lambda: _Page(
        "<p>Something else entirely, and long enough to look plausible.</p>"
    ))
    monkeypatch.setattr(contributions.settings, "mode", "live")

    checked = contributions.verify(_contribution("The album shipped in March 2017, and the tour followed."))
    assert checked.status is ContributionStatus.REJECTED
    assert checked.status is not ContributionStatus.PENDING


def test_a_404_page_cannot_verify_a_quotation(monkeypatch):
    monkeypatch.setattr(contributions, "is_public_url", lambda url: True)
    monkeypatch.setattr(contributions, "get_backend", lambda: _Page("gone", status=404))
    monkeypatch.setattr(contributions.settings, "mode", "live")

    checked = contributions.verify(_contribution())
    assert checked.status is ContributionStatus.REJECTED


def test_offline_install_leaves_the_note_pending(monkeypatch):
    monkeypatch.setattr(contributions, "is_public_url", lambda url: True)
    monkeypatch.setattr(contributions.settings, "mode", "fixture")

    checked = contributions.verify(_contribution())
    assert checked.status is ContributionStatus.PENDING


def test_a_phrase_is_not_a_quotation(monkeypatch):
    monkeypatch.setattr(contributions, "is_public_url", lambda url: True)
    checked = contributions.verify(_contribution("Paris is beautiful"))
    assert checked.status is ContributionStatus.REJECTED
    assert str(contributions.MIN_QUOTE_CHARS) in checked.note


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/account",
        "http://localhost/x",
        "http://10.0.0.1/a",
        "ftp://example.com/x",
        "https://example.com@127.0.0.1/x",  # host is 127.0.0.1, not example.com
    ],
)
def test_only_public_pages_are_fetched(url):
    assert contributions.is_public_url(url) is False


def test_page_text_drops_scripts_and_keeps_the_prose():
    text = contributions.page_text(
        "<style>p{color:red}</style><script>var x=1</script><p>Hello <b>world</b></p>"
    )
    assert text == "Hello world"


def test_badges_are_increments_of_the_same_two_counts():
    assert contributions.badges(0, 0) == []
    assert contributions.badges(1, 0) == ["First light"]
    assert contributions.badges(5, 10)[-2:] == ["Reader", "Trusted"]
    assert contributions.badges(50, 0)[-1] == "Scholiast"


# ── handles ────────────────────────────────────────────────────────────────
#
# A handle is a citation: it appears under someone's reading, and it must be
# something a reader could type back. It is not a verification, and it cannot
# pass for the site.

def test_a_name_with_the_separators_people_use_is_kept():
    assert contributions.clean_handle("Jean-Luc") == "Jean-Luc"
    assert contributions.clean_handle("Élodie") == "Élodie"
    assert contributions.clean_handle("Marie  L.") == "Marie L."


def test_a_name_that_cannot_be_cited_is_refused():
    for raw in ("", "x", "9lives", "<script>", "a" * 25, "-leading", "a/b"):
        assert contributions.clean_handle(raw) is None, raw


def test_the_site_cannot_be_impersonated_by_a_handle():
    assert contributions.clean_handle("Subtext") is None
    assert contributions.clean_handle("  ADMIN ") is None
    assert contributions.clean_handle("Support") is None
