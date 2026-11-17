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


# ── margin notes ───────────────────────────────────────────────────────────
#
# The check is a fetch and a substring, so the test replaces the fetch. What is
# under test is the routing and the rules: what gets stored, what gets printed,
# and what a reader is told when it fails.

QUOTE = "Booba a publié son huitième album studio le 3 mars 2017."


def _contribute(client, answer_id, *, status="ACCEPTED", body="A note.", quote=QUOTE):
    from src.api.routes import pages
    from src.models import ContributionStatus

    def fake_verify(contribution):
        return contribution.model_copy(update={"status": ContributionStatus(status)})

    pages.verify, original = fake_verify, pages.verify
    try:
        return client.post(
            f"/a/{answer_id}/contribute",
            data={
                "kind": "context",
                "body": body,
                "quote": quote,
                "source_url": "https://fr.wikipedia.org/wiki/Booba",
            },
            follow_redirects=False,
        )
    finally:
        pages.verify = original


def test_accepted_note_appears_in_the_margin(client, test_store):
    answer_id = _answer(client)
    client.get(f"/a/{answer_id}")

    response = _contribute(client, answer_id, body="The album shipped in March 2017.")
    assert response.status_code == 303
    assert "contributed=accepted" in response.headers["location"]

    page = client.get(f"/a/{answer_id}").text
    assert "The album shipped in March 2017." in page
    assert QUOTE in page
    assert "Verified on fr.wikipedia.org" in page


def test_refused_note_is_not_printed_on_the_document(client, test_store):
    answer_id = _answer(client)
    client.get(f"/a/{answer_id}")

    response = _contribute(client, answer_id, status="REJECTED", body="An unchecked claim.")
    assert "contributed=rejected" in response.headers["location"]

    page = client.get(response.headers["location"]).text
    assert "An unchecked claim." not in page
    # The contributor is told why, without being told their reading is wrong.
    assert "was not found on that page" in page


def test_pending_note_is_not_printed_either(client, test_store):
    answer_id = _answer(client)
    client.get(f"/a/{answer_id}")

    response = _contribute(client, answer_id, status="PENDING", body="Saved for later.")
    assert "contributed=pending" in response.headers["location"]

    page = client.get(response.headers["location"]).text
    assert "Saved for later." not in page
    assert "Saved, not yet checked." in page


def test_contribution_needs_a_visitor_and_a_document(client):
    answer_id = _answer(client)
    assert client.post("/a/nope/contribute", data={
        "body": "b" * 20, "quote": "q" * 50,
        "source_url": "https://example.com/x",
    }, follow_redirects=False).status_code == 404


def test_profile_lists_accepted_notes_only(client, client_factory, test_store):
    answer_id = _answer(client)
    client.get(f"/a/{answer_id}")
    _contribute(client, answer_id, body="This one passed.")
    _contribute(client, answer_id, status="REJECTED", body="This one did not.")

    author = test_store.contributions_for(answer_id, accepted_only=False)[0].author
    page = client_factory().get(f"/u/{author}").text  # a stranger's view
    assert "This one passed." in page
    assert "This one did not." not in page
    assert "First light" in page          # one accepted note is one badge
    assert "1</b> accepted" in page
    assert "noindex" in page


def test_a_refused_note_does_not_just_vanish(client, client_factory, test_store):
    """The author gets their own text back, with the check's reason and a way to
    fix it. Nobody else ever sees it."""
    answer_id = _answer(client)
    client.get(f"/a/{answer_id}")
    _contribute(client, answer_id, status="REJECTED", body="This one did not.")

    author = test_store.contributions_for(answer_id, accepted_only=False)[0].author
    mine = client.get(f"/u/{author}").text
    assert "Only you see this" in mine
    assert "This one did not." in mine
    assert "Fix it on" in mine

    stranger = client_factory().get(f"/u/{author}").text
    assert "Only you see this" not in stranger
    assert "This one did not." not in stranger


def test_profile_is_noindex_and_still_renders_when_empty(client):
    page = client.get("/u/anon_0000000000000000000000000000000f").text
    assert "noindex,nofollow" in page
    assert "No verified note yet" in page


def test_two_notes_on_one_document_do_not_double_its_endorsements(client, test_store):
    """A document's endorsements are the document's, not a multiple of how many
    notes its contributor happened to write on it."""
    answer_id = _answer(client)
    client.get(f"/a/{answer_id}")
    _contribute(client, answer_id, body="First note.")
    _contribute(client, answer_id, body="Second note.")

    body = test_store.contributions_for(answer_id, accepted_only=False)
    author = body[0].author
    assert test_store.accepted_count(author) == 2

    test_store.endorse(answer_id, "anon_someoneelse")
    assert test_store.endorsements_received(author) == 1


# ── handles ────────────────────────────────────────────────────────────────
#
# A visitor id is a cookie. A handle is a person someone can cite — and the rule
# that matters is ownership: the profile page is public, so only its subject may
# name it.

def _author_of(client, test_store, answer_id) -> str:
    client.get(f"/a/{answer_id}")  # mints the visitor cookie
    _contribute(client, answer_id, body="A note worth citing.")
    return test_store.contributions_for(answer_id, accepted_only=False)[0].author


def test_a_reader_can_name_themselves(client, test_store):
    answer_id = _answer(client)
    author = _author_of(client, test_store, answer_id)

    response = client.post("/u/handle", data={"handle": "Marie L."}, follow_redirects=False)
    assert response.status_code == 303
    assert "handle=saved" in response.headers["location"]
    assert test_store.handle_for(author) == "Marie L."

    assert "Marie L." in client.get(f"/u/{author}").text


def test_the_margin_cites_the_handle_instead_of_the_contributor(client, test_store):
    answer_id = _answer(client)
    author = _author_of(client, test_store, answer_id)
    client.post("/u/handle", data={"handle": "Marie L."}, follow_redirects=False)

    page = client.get(f"/a/{answer_id}").text
    assert f'href="/u/{author}">Marie L.</a>' in page


def test_a_handle_is_unique_case_insensitively(client, client_factory, test_store):
    first = _author_of(client, test_store, _answer(client))
    assert test_store.set_handle(first, "Marie")

    # A different browser, a different reader, the same name.
    other = client_factory()
    other.get("/")
    response = other.post("/u/handle", data={"handle": "marie"}, follow_redirects=False)
    assert "handle=taken" in response.headers["location"]


def test_reserved_names_are_refused(client, test_store):
    answer_id = _answer(client)
    author = _author_of(client, test_store, answer_id)
    for name in ("Subtext", "admin", " support "):
        response = client.post("/u/handle", data={"handle": name}, follow_redirects=False)
        assert "handle=invalid" in response.headers["location"], name
    assert test_store.handle_for(author) is None


def test_a_name_that_cannot_be_cited_is_refused(client, test_store):
    answer_id = _answer(client)
    author = _author_of(client, test_store, answer_id)
    for name in ("x", "9lives", "<script>", "a" * 30):
        response = client.post("/u/handle", data={"handle": name}, follow_redirects=False)
        assert "handle=invalid" in response.headers["location"], name
    assert test_store.handle_for(author) is None


def test_a_handle_cannot_be_claimed_for_someone_else(client, test_store):
    """The route writes to the caller's own author id, whatever the URL says."""
    victim = "anon_0000000000000000000000000000000f"
    client.get("/")
    client.post("/u/handle", data={"handle": "Not the victim"}, follow_redirects=False)
    assert test_store.handle_for(victim) is None
    assert "Not the victim" not in client.get(f"/u/{victim}").text


def test_a_reading_in_the_margin_closes_the_open_question(client, test_store):
    from src.models import Answer, ContextPack, Sufficiency

    answer = Answer(
        work_id="booba-paname",
        question="Booba, Paname — what is he saying about Paris?",
        thesis="We can't answer this well yet.",
        sufficiency=Sufficiency.INSUFFICIENT,
    )
    test_store.save(answer, ContextPack(work_id="booba-paname"), {})

    assert "Nobody has explained these yet." in client.get("/").text
    client.get(f"/a/{answer.id}")  # mints the visitor cookie
    _contribute(client, answer.id, body="A reading with a source behind it.")
    assert "Nobody has explained these yet." not in client.get("/").text
