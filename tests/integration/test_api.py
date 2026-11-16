"""API surface (§6): the contract a client (and later the app) depends on."""

from __future__ import annotations

import json

from src.service import clear_cache

QUESTION = {
    "question": "Why does the second movement sound so plain?",
    "work_id": "beethoven-op111",
}


def _create(client) -> dict:
    clear_cache()
    response = client.post("/api/v1/answers", json=QUESTION)
    assert response.status_code == 200
    return response.json()


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_create_answer(client):
    payload = _create(client)
    assert payload["status"] == "ok"
    answer = payload["answer"]
    assert answer["work_id"] == "beethoven-op111"
    assert answer["sufficiency"] in ("SUFFICIENT", "THIN")
    assert answer["blocks"]


def test_get_answer_by_permalink(client):
    answer_id = _create(client)["answer"]["id"]
    fetched = client.get(f"/api/v1/answers/{answer_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == answer_id


def test_unknown_answer_is_404(client):
    assert client.get("/api/v1/answers/does-not-exist").status_code == 404


def test_ambiguous_question_returns_candidates(client):
    clear_cache()
    payload = client.post(
        "/api/v1/answers", json={"question": "the sad piano piece from that film"}
    ).json()
    assert payload["status"] in ("ambiguous", "not_found")
    if payload["status"] == "ambiguous":
        assert payload["candidates"]


def test_deepening_is_rights_gated(client):
    answer = _create(client)["answer"]
    block = next(b for b in answer["blocks"] if b["kind"] == "CONTEXT")
    response = client.get(
        f"/api/v1/answers/{answer['id']}/deepening", params={"block_id": block["id"]}
    )
    assert response.status_code == 200
    deepening = response.json()
    assert deepening["block_id"] == block["id"]
    for quote in deepening["quotes"]:
        assert quote["text"], "a quote with no text should not be returned"


def test_export_markdown_has_attribution(client):
    answer_id = _create(client)["answer"]["id"]
    response = client.get(f"/api/v1/export/{answer_id}", params={"format": "markdown"})
    assert response.status_code == 200
    assert "## Attribution" in response.text
    assert "text/markdown" in response.headers["content-type"]


def test_export_json_hides_source_text(client):
    answer_id = _create(client)["answer"]["id"]
    payload = json.loads(
        client.get(f"/api/v1/export/{answer_id}", params={"format": "json"}).text
    )
    assert payload["sources"]
    assert all(source["text_included"] is False for source in payload["sources"])
    assert "rights" in payload["sources"][0]


def test_work_endpoints(client):
    _create(client)
    for path in ("context", "timeline", "sources", "conflicts", "conflict-candidates"):
        response = client.get(f"/api/v1/works/beethoven-op111/{path}")
        assert response.status_code == 200, path

    sources = client.get("/api/v1/works/beethoven-op111/sources").json()
    for source in sources:
        if not source["rights"]["display_policy"] in ("FULL_TEXT", "EXCERPT", "QUOTE_SPAN"):
            assert source["text"] == ""


def test_search_returns_ranked_candidates(client):
    candidates = client.get("/api/v1/works/search", params={"q": "op 111"}).json()
    assert candidates[0]["work_id"] == "beethoven-op111"
    assert candidates[0]["confidence"] > 0.8


def test_feedback_is_recorded(client):
    answer_id = _create(client)["answer"]["id"]
    block_id = client.get(f"/api/v1/answers/{answer_id}").json()["blocks"][0]["id"]
    feedback = client.post(
        f"/api/v1/answers/{answer_id}/feedback",
        json={"block_id": block_id, "kind": "THIS_IS_WRONG", "note": "The count is six."},
    )
    assert feedback.status_code == 200
    listed = client.get(f"/api/v1/answers/{answer_id}/feedback").json()
    assert listed[0]["kind"] == "THIS_IS_WRONG"


def test_annotation_is_private_and_stored(client):
    answer_id = _create(client)["answer"]["id"]
    created = client.post(
        "/api/v1/annotations",
        json={"target_type": "answer", "target_id": answer_id, "text": "Check the metronome."},
    )
    assert created.status_code == 200
    listed = client.get("/api/v1/annotations", params={"target_id": answer_id}).json()
    assert listed[0]["text"] == "Check the metronome."


def test_reader_page_renders_document(client):
    answer_id = _create(client)["answer"]["id"]
    html = client.get(f"/a/{answer_id}")
    assert html.status_code == 200
    assert "Attribution" in html.text
    assert "Timeline" in html.text


def test_index_page_loads(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Subtext" in page.text
    assert "and who said so" in page.text
    # The landing page is the product's claim about itself: every art form is in
    # scope, not just the concert hall.
    for form in ("Rap", "Classical", "Poetry", "Painting", "Film"):
        assert form in page.text
