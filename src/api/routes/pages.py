"""HTML pages: the product as a reader experiences it (§7)."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.answer.deepen import deepen as build_deepening
from src.answer.render import public_deepening
from src.api import reader, site
from src.api.deps import store
from src.billing import entitlements
from src.contributions import clean_handle, verify
from src.models import (
    Contribution,
    ContributionKind,
    Feedback,
    FeedbackKind,
)
from src.service import PipelineResult, answer_question
from src.retrieval.entity_resolver import Resolution

router = APIRouter(include_in_schema=False)


def _recent(limit: int = 12):
    return store().recent(limit)


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    return reader.index_page(_recent(), answered=store().answers_with_readings())


@router.post("/ask")
def ask(
    request: Request,
    question: str = Form(min_length=3, max_length=500),
    work_id: str | None = Form(default=None),
    parent_id: str | None = Form(default=None),
) -> Response:
    user_id = entitlements.identity(request, store())
    entitlement = entitlements.check(user_id, store())
    if not entitlement.allowed:
        return HTMLResponse(
            site.message_page(
                "You have used your free answers this month",
                "Pro removes the monthly limit and keeps every citation, conflict "
                "and gap exactly as they are. Cancel any time.",
                cta="See plans",
            ).replace(
                '<p class="question">',
                '<p class="question"><a href="/pricing">See plans</a> — ',
            ),
            status_code=402,
        )

    result = answer_question(question, work_id=work_id, parent_id=parent_id)
    if isinstance(result, Resolution):
        if not result.candidates:
            return HTMLResponse(
                reader.candidates_page(
                    question,
                    [],
                )
                .replace("<h2>Which work did you mean?</h2>", "<h2>We could not identify that work</h2>"),
                status_code=200,
            )
        return HTMLResponse(reader.candidates_page(question, result.candidates))

    store().save(result.answer, result.pack, result.sources)
    entitlements.consume(user_id, store())
    return RedirectResponse(url=f"/a/{result.answer.id}", status_code=303)


@router.get("/a/{answer_id}", response_class=HTMLResponse)
def answer_page(answer_id: str, request: Request) -> str:
    answer = store().answer(answer_id)
    pack = store().pack(answer_id)
    sources = store().sources(answer_id)
    if answer is None or pack is None:
        raise HTTPException(status_code=404, detail="answer not found")
    visitor = entitlements.identity(request, store())
    return reader.page(
        answer,
        pack,
        sources,
        children=store().children(answer_id),
        feedback_sent=request.query_params.get("feedback") == "1",
        endorsements=store().endorsement_count(answer_id),
        endorsed=store().has_endorsed(answer_id, visitor),
        contributions=store().contributions_for(answer_id),
        contributed=request.query_params.get("contributed", ""),
        handles=store().handles(
            [c.author for c in store().contributions_for(answer_id)]
        ),
    )


@router.post("/a/{answer_id}/contribute")
def contribute(
    answer_id: str,
    request: Request,
    body: str = Form(min_length=8, max_length=700),
    quote: str = Form(min_length=8, max_length=700),
    source_url: str = Form(min_length=8, max_length=500),
    kind: str = Form(default="context"),
) -> Response:
    """A reader's addition is checked before it is shown.

    The check is a fetch and a substring: the quoted sentence must be on the
    cited page. That is the only thing a machine can honestly settle here, and
    it is enough to keep the margin sourced.
    """
    if store().answer(answer_id) is None:
        raise HTTPException(status_code=404, detail="answer not found")
    visitor = entitlements.identity(request, store())
    if not visitor:
        raise HTTPException(status_code=401, detail="no session or visitor cookie")

    contribution = Contribution(
        answer_id=answer_id,
        author=visitor,
        kind=ContributionKind(kind.upper())
        if kind.upper() in ContributionKind.__members__ else ContributionKind.CONTEXT,
        body=body.strip(),
        quote=quote.strip(),
        source_url=source_url.strip(),
    )
    checked = verify(contribution)
    store().add_contribution(checked)
    status = checked.status.value.lower()
    return RedirectResponse(url=f"/a/{answer_id}?contributed={status}#margin", status_code=303)


@router.get("/u/{author}", response_class=HTMLResponse)
def profile(author: str, request: Request) -> str:
    contributions = store().contributions_by(author)
    accepted = store().accepted_count(author)
    endorsements = store().endorsements_received(author)
    # The reader needs the question each note hangs off, or a profile is a list
    # of links that say nothing. Fetching it here keeps `reader` free of the
    # store, which is what lets the view be rendered from a fixture in a test.
    questions = {}
    for contribution in contributions:
        answer = store().answer(contribution.answer_id)
        if answer is not None:
            questions[contribution.answer_id] = answer.question
    return reader.profile_page(
        author,
        contributions,
        accepted=accepted,
        endorsements=endorsements,
        questions=questions,
        handle=store().handle_for(author),
        # Ownership is decided here, against the cookie, never from the URL: a
        # profile page is public, and only its subject may name it.
        is_owner=entitlements.identity(request, store()) == author,
        notice=request.query_params.get("handle", ""),
    )


@router.post("/u/handle")
def claim_handle(request: Request, handle: str = Form(default="")) -> Response:
    """Name yourself. The only write here that is not a quotation check.

    A handle is not a credential and does not verify anything: it lets a
    contribution be cited, and it is what badges will attach to. Reserved names
    are refused so that a reader cannot pass for the site itself.
    """
    visitor = entitlements.identity(request, store())
    if not visitor:
        raise HTTPException(status_code=401, detail="no session or visitor cookie")
    name = clean_handle(handle)
    if name is None:
        return RedirectResponse(url=f"/u/{visitor}?handle=invalid#standing", status_code=303)
    if not store().set_handle(visitor, name):
        return RedirectResponse(url=f"/u/{visitor}?handle=taken#standing", status_code=303)
    return RedirectResponse(url=f"/u/{visitor}?handle=saved#standing", status_code=303)


@router.post("/a/{answer_id}/endorse")
def endorse(answer_id: str, request: Request) -> Response:
    """Put your name behind a document.

    One per browser, and it is not a vote on a model: an endorsed document is a
    candidate for the curated registry and for a second reading, and eventually
    for the contributor's reputation. Counting it twice would make the number
    meaningless.
    """
    if store().answer(answer_id) is None:
        raise HTTPException(status_code=404, detail="answer not found")
    visitor = entitlements.identity(request, store())
    if visitor:
        store().endorse(answer_id, visitor)
    return RedirectResponse(url=f"/a/{answer_id}#share", status_code=303)


@router.post("/a/{answer_id}/feedback")
def feedback(
    answer_id: str,
    verdict: str = Form(...),
    note: str = Form(default=""),
) -> Response:
    """One click, stored with the document it is about.

    This is curation input, not model training: a document that readers call
    wrong is a candidate for a second reading, and a work that keeps missing is
    a candidate for the registry. Saying so is more honest than implying the
    votes retrain anything.
    """
    answer = store().answer(answer_id)
    if answer is None:
        raise HTTPException(status_code=404, detail="answer not found")
    kind = {
        "useful": FeedbackKind.USEFUL,
        "partial": FeedbackKind.PARTLY,
        "missed": FeedbackKind.MISSED,
    }.get(verdict.strip().lower())
    if kind is not None:
        store().add_feedback(
            Feedback(answer_id=answer_id, kind=kind, note=note.strip()[:300])
        )
    return RedirectResponse(url=f"/a/{answer_id}?feedback=1#main", status_code=303)


@router.get("/a/{answer_id}/b/{block_id}/deepen", response_class=HTMLResponse)
def deepen_fragment(answer_id: str, block_id: str) -> str:
    answer = store().answer(answer_id)
    pack = store().pack(answer_id)
    sources = store().sources(answer_id)
    if answer is None or pack is None:
        raise HTTPException(status_code=404, detail="answer not found")
    try:
        deepening = build_deepening(answer, block_id, pack, sources)
    except KeyError:
        raise HTTPException(status_code=404, detail="block not found") from None
    return reader.deepening_fragment(public_deepening(deepening, sources), sources)
