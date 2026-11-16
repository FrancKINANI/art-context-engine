"""HTML pages: the product as a reader experiences it (§7)."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.answer.deepen import deepen as build_deepening
from src.answer.render import public_deepening
from src.api import reader, site
from src.api.deps import store
from src.billing import entitlements
from src.models import Feedback, FeedbackKind
from src.service import PipelineResult, answer_question
from src.retrieval.entity_resolver import Resolution

router = APIRouter(include_in_schema=False)


def _recent(limit: int = 12):
    return store().recent(limit)


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    return reader.index_page(_recent())


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
    return reader.page(
        answer,
        pack,
        sources,
        children=store().children(answer_id),
        feedback_sent=request.query_params.get("feedback") == "1",
    )


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
