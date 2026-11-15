"""Private annotations (§3.8, §6).

An annotation is a note on a document that only its author can read. That used to
be a comment in this docstring while the endpoint served every note for a target
id to anyone who asked; now the owner is written with the row and every read
filters on it.

An anonymous browser is still an owner: it has a signed-out but server-issued
``ace_anon`` id, so a visitor can keep notes without an account. Clearing cookies
loses them — that is the honest trade for not asking for an email before the
first note.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.deps import store
from src.billing import entitlements
from src.models import Annotation

router = APIRouter(tags=["annotations"])


class AnnotationRequest(BaseModel):
    target_type: str = Field(pattern="^(source|block|claim|work|answer)$")
    target_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)


def _owner(request: Request) -> str:
    owner = entitlements.identity(request, store())
    if not owner:
        raise HTTPException(status_code=401, detail="no session or visitor cookie")
    return owner


@router.post("/annotations", response_model=Annotation)
def create_annotation(request: Request, body: AnnotationRequest) -> Annotation:
    annotation = Annotation(
        target_type=body.target_type,  # type: ignore[arg-type]
        target_id=body.target_id,
        text=body.text,
    )
    return store().add_annotation(annotation, _owner(request))


@router.get("/annotations", response_model=list[Annotation])
def list_annotations(request: Request, target_id: str) -> list[Annotation]:
    return store().annotations_for(target_id, _owner(request))
