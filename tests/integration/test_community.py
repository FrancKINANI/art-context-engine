"""The social layer's first honest piece: standing behind a document.

Two rules, both about not inflating a number: one endorsement per browser, and
an endorsement is stored against the document rather than against a model run.
"""

from __future__ import annotations

from src.service import clear_cache

QUESTION = {
    "question": "Why does the second movement sound so plain?",
    "work_id": "beethoven-op111",
}


def _answer(client) -> str:
    clear_cache()
    response = client.post("/api/v1/answers", json=QUESTION)
    assert response.status_code == 200
    return response.json()["answer"]["id"]


def test_endorsement_is_one_per_visitor(client, test_store):
    answer_id = _answer(client)
    client.get(f"/a/{answer_id}")  # mints the visitor cookie

    first = client.post(f"/a/{answer_id}/endorse", follow_redirects=False)
    assert first.status_code == 303
    assert test_store.endorsement_count(answer_id) == 1

    client.post(f"/a/{answer_id}/endorse", follow_redirects=False)
    assert test_store.endorsement_count(answer_id) == 1


def test_document_shows_that_it_is_endorsed(client, test_store):
    answer_id = _answer(client)
    client.post(f"/a/{answer_id}/endorse", follow_redirects=False)

    page = client.get(f"/a/{answer_id}").text
    assert "reader stands behind this document" in page
    assert "You stand behind this document." in page


def test_endorsement_on_a_missing_document_is_404(client):
    assert client.post("/a/nope/endorse", follow_redirects=False).status_code == 404


def test_share_links_are_plain_urls(client):
    """No third-party script, no frame: the CSP is the reason."""
    answer_id = _answer(client)
    page = client.get(f"/a/{answer_id}").text
    assert "twitter.com/intent/tweet" in page
    assert "wa.me/?text=" in page
    assert "mailto:?subject=" in page
