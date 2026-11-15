"""Answer endpoints (§6).

`POST /api/v1/answers` is synchronous in the MVP: one question, one document, one
response. Ambiguity is a 200 with candidates rather than a 4xx, because "which
work did you mean?" is a normal step in understanding a question, not a client
error.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.answer.deepen import deepen as build_deepening
from src.answer.render import public_deepening
from src.api.deps import store
from src.billing import entitlements
from src.models import Answer, Feedback, FeedbackKind, QuestionType, WorkCandidate
from src.service import PipelineResult, answer_question, find_work
from src.retrieval.entity_resolver import Resolution

router = APIRouter(tags=["answers"])


class AnswerRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    work_id: str | None = None
    url: str | None = None
    parent_id: str | None = None
    question_type: QuestionType | None = None
    candidates: list[WorkCandidate] | None = None


class AnswerResponse(BaseModel):
    status: str                     # "ok" | "ambiguous" | "not_found"
    answer: Answer | None = None
    candidates: list[WorkCandidate] = Field(default_factory=list)
    message: str | None = None
    errors: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    block_id: str | None = None
    kind: FeedbackKind
    note: str = ""


class QuotaResponse(BaseModel):
    error: str = "quota_exceeded"
    message: str
    entitlement: dict
    upgrade_url: str = "/pricing"


@router.post("/answers", response_model=AnswerResponse)
def create_answer(request: AnswerRequest, http_request: Request) -> AnswerResponse:
    # The quota is checked before any retrieval: a refused request should cost
    # the reader nothing and cost us nothing either.
    user_id = entitlements.identity(http_request, store())
    entitlement = entitlements.check(user_id, store())
    if not entitlement.allowed:
        raise HTTPException(
            status_code=402,
            detail=QuotaResponse(
                message=(
                    "You have used your free answers this month. Pro removes the limit."
                    if entitlement.reason == "quota_exhausted"
                    else "The free trial is over for this browser. Sign in, or go Pro."
                ),
                entitlement=entitlement.as_dict(),
            ).model_dump(),
        )

    result = answer_question(
        request.question,
        work_id=request.work_id,
        url=request.url,
        parent_id=request.parent_id,
        question_type=request.question_type,
        candidates=request.candidates,
    )
    if isinstance(result, Resolution):
        if not result.candidates:
            return AnswerResponse(
                status="not_found",
                message="No work matched that question. Name a work, or paste a link to it.",
            )
        return AnswerResponse(
            status="ambiguous",
            candidates=result.candidates,
            message="Which work did you mean?",
        )

    assert isinstance(result, PipelineResult)
    store().save(result.answer, result.pack, result.sources)
    # Consumed only after the document exists: a crash mid-assembly must not
    # cost a reader an answer.
    entitlements.consume(user_id, store())
    return AnswerResponse(status="ok", answer=result.answer, errors=result.errors)


@router.get("/answers/{answer_id}", response_model=Answer)
def get_answer(answer_id: str) -> Answer:
    answer = store().answer(answer_id)
    if answer is None:
        raise HTTPException(status_code=404, detail="answer not found")
    return answer


@router.get("/answers/{answer_id}/deepening")
def get_deepening(answer_id: str, block_id: str):
    answer = store().answer(answer_id)
    pack = store().pack(answer_id)
    sources = store().sources(answer_id)
    if answer is None or pack is None:
        raise HTTPException(status_code=404, detail="answer not found")
    try:
        result = build_deepening(answer, block_id, pack, sources)
    except KeyError:
        raise HTTPException(status_code=404, detail="block not found") from None
    return public_deepening(result, sources)


@router.post("/answers/{answer_id}/feedback", response_model=Feedback)
def add_feedback(answer_id: str, request: FeedbackRequest) -> Feedback:
    if store().answer(answer_id) is None:
        raise HTTPException(status_code=404, detail="answer not found")
    feedback = Feedback(
        answer_id=answer_id,
        block_id=request.block_id,
        kind=request.kind,
        note=request.note,
    )
    return store().add_feedback(feedback)


@router.get("/answers/{answer_id}/feedback", response_model=list[Feedback])
def list_feedback(answer_id: str) -> list[Feedback]:
    return store().feedback_for(answer_id)
