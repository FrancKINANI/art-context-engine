"""Work, evidence and conflict endpoints (§6).

These expose the evidence layer directly. The product's claim is inspectability:
a reader — or an evaluator — can look at the pack the document was built from,
including the sources' rights status, the timeline with precision intact, and
every conflict, plus the candidates that did *not* make the threshold.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.api.deps import store
from src.models import (
    Conflict,
    ConflictCandidate,
    ContextPack,
    Source,
    TimelineEvent,
    Work,
    WorkCandidate,
)
from src.registry import all_works, get_work
from src.retrieval.entity_resolver import resolve
from src.service import answer_question
from src.retrieval.entity_resolver import Resolution

router = APIRouter(tags=["works"])


def _require_work(work_id: str) -> Work:
    work = get_work(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"unknown work {work_id}")
    return work


@router.get("/works", response_model=list[Work])
def list_works() -> list[Work]:
    return all_works()


@router.get("/works/search", response_model=list[WorkCandidate])
def search_works(q: str = Query(min_length=2), limit: int = 5) -> list[WorkCandidate]:
    return resolve(q).candidates[:limit]


def _pack_for(work_id: str, question: str | None = None) -> tuple[ContextPack, dict[str, Source]]:
    """Evidence for a work, from a published answer when one exists.

    Re-running the whole pipeline on a bare GET would make the endpoint slow and
    non-deterministic between calls; using the stored pack keeps what the reader
    sees identical to what the document was built from.
    """
    work = _require_work(work_id)
    recent = store().by_work(work_id, limit=50)
    if recent:
        pack = store().pack(recent[0].id)
        sources = store().sources(recent[0].id)
        if pack is not None:
            return pack, sources

    result = answer_question(question or work.title, work_id=work_id)
    if isinstance(result, Resolution):
        raise HTTPException(status_code=409, detail="work could not be resolved")
    store().save(result.answer, result.pack, result.sources)
    return result.pack, result.sources


@router.get("/works/{work_id}/context", response_model=ContextPack)
def work_context(work_id: str, question: str | None = None) -> ContextPack:
    pack, _ = _pack_for(work_id, question)
    return pack


@router.get("/works/{work_id}/timeline", response_model=list[TimelineEvent])
def work_timeline(work_id: str) -> list[TimelineEvent]:
    pack, _ = _pack_for(work_id)
    return pack.timeline


@router.get("/works/{work_id}/sources", response_model=list[Source])
def work_sources(
    work_id: str,
    type: str | None = None,
    limit: int = 20,
) -> list[Source]:
    pack, sources = _pack_for(work_id)
    items = list(sources.values())
    if type:
        items = [s for s in items if s.type.value.lower() == type.lower()]
    out: list[Source] = []
    for source in items[:limit]:
        # §6: rights travel in the payload; text is omitted when not allowed.
        out.append(source.model_copy(update={"text": "" if not source.rights.shows_text else source.text}))
    return out


@router.get("/works/{work_id}/conflicts", response_model=list[Conflict])
def work_conflicts(work_id: str) -> list[Conflict]:
    pack, _ = _pack_for(work_id)
    return pack.conflicts


@router.get("/works/{work_id}/conflict-candidates", response_model=list[ConflictCandidate])
def work_conflict_candidates(work_id: str) -> list[ConflictCandidate]:
    """Curator queue: below-threshold conflicts, never shown to readers (§4.5.2)."""
    pack, _ = _pack_for(work_id)
    return pack.conflict_candidates
