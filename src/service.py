"""The one pipeline call: question → document (§4).

Everything upstream (adapters, extraction, conflicts, assembly) is a library
function; this is the operation the product performs. Keeping it in one place
means the API, the tests and the evaluation harness all exercise the same path —
a metric measured on a different path than the product uses is not a metric.

Caching is per (work, question): the corpus is the same, so the evidence pack is
the same, and re-running extraction for every request would make the evaluation
harness slow enough to skip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from src.answer.assemble import assemble
from src.models import (
    Answer,
    ContextPack,
    QuestionType,
    Source,
    Work,
    WorkCandidate,
)
from src.works import get_work
from src.retrieval.entity_resolver import Resolution, resolve, resolve_from_url
from src.retrieval.gatherer import gather
from src.retrieval.question import Interpretation, interpret

_PACK_CACHE: dict[tuple[str, str], object] = {}


@dataclass
class PipelineResult:
    answer: Answer | None
    work: Work
    pack: ContextPack
    sources: dict[str, Source]
    interpretation: Interpretation
    errors: list[str] = field(default_factory=list)


def _pack_for(work: Work, question: str, question_type: QuestionType):
    """Evidence gathering, cached per (work, question).

    Unbounded before: every distinct question a stranger typed stayed in memory
    for the life of the process, which is a slow memory leak an anonymous
    visitor can drive deliberately. 256 entries covers a browsing session and
    an evaluation run; eviction just re-gathers.
    """
    return _gather(work.id, question, question_type.value)


@lru_cache(maxsize=256)
def _gather(work_id: str, question: str, question_type: str):
    work = get_work(work_id)
    if work is None:
        raise KeyError(work_id)
    return gather(work, question, QuestionType(question_type))


def find_work(query: str, work_id: str | None = None, url: str | None = None) -> Resolution:
    """Resolve a query to candidates. Never silently picks a work."""
    if work_id:
        work = get_work(work_id)
        if work is None:
            return Resolution(candidates=[], query=query)
        from src.retrieval.entity_resolver import _candidate

        return Resolution(candidates=[_candidate(work, 1.0, "explicit work_id")], query=query)
    if url:
        return resolve_from_url(url)
    return resolve(query)


def answer_question(
    question: str,
    *,
    work_id: str | None = None,
    url: str | None = None,
    parent_id: str | None = None,
    question_type: QuestionType | None = None,
    candidates: list[WorkCandidate] | None = None,
) -> PipelineResult | Resolution:
    """Returns a `Resolution` when the work is ambiguous, otherwise a result.

    Ambiguity is a return value, not an exception: §4.1 says guessing silently is
    the most expensive failure, so the caller gets candidates and a choice to
    make.
    """
    if candidates:
        if len(candidates) > 1:
            return Resolution(candidates=candidates, query=question)
        work_id = candidates[0].work_id

    if work_id is None:
        resolution = find_work(question, work_id, url)
        if resolution.is_ambiguous or resolution.best is None:
            return resolution
        work = get_work(resolution.best.work_id)
        if work is None:
            return resolution
    else:
        work = get_work(work_id)
        if work is None:
            return Resolution(candidates=[], query=question)

    interp = interpret(question)
    if question_type is not None:
        interp = Interpretation(
            question=interp.question,
            question_type=question_type,
            aspect=interp.aspect,
            predicates=interp.predicates,
            matched_terms=interp.matched_terms,
        )

    key = (work.id, question)
    try:
        result = _pack_for(work, question, interp.question_type)
    except KeyError:
        return Resolution(candidates=[], query=question)

    answer = assemble(work, interp, result.pack, result.sources, parent_id=parent_id)
    return PipelineResult(
        answer=answer,
        work=work,
        pack=result.pack,
        sources=result.sources,
        interpretation=interp,
        errors=list(result.errors),
    )


def clear_cache() -> None:
    _gather.cache_clear()
