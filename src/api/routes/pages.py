"""HTML pages: the product as a reader experiences it (§7)."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.answer.deepen import deepen as build_deepening
from src.answer.render import public_deepening
from src.api import reader, site
from src.api.deps import store
from src.billing import entitlements
from src.service import PipelineResult, answer_question
from src.retrieval.entity_resolver import Resolution

router = APIRouter(include_in_schema=False)


def _recent(limit: int = 12):
    # Cheap recent list: the store has no cross-work query for MVP scale, so the
    # index asks each work and merges. Fine for three works, replaced by SQL.
    from src.registry import all_works

    answers = []
    for work in all_works():
        answers.extend(store().by_work(work.id, limit=limit))
    answers.sort(key=lambda a: a.generated_at, reverse=True)
    return answers[:limit]


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
def answer_page(answer_id: str) -> str:
    answer = store().answer(answer_id)
    pack = store().pack(answer_id)
    sources = store().sources(answer_id)
    if answer is None or pack is None:
        raise HTTPException(status_code=404, detail="answer not found")
    return reader.page(answer, pack, sources, children=store().children(answer_id))


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
