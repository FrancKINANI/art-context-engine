"""Works: the curated registry, plus the ones resolved online.

The registry (`data/seed/works.json`) stays the authoritative list: those are the
works with a hand-checked mapping between article, QID, score page and song page.
A work resolved from Wikidata, Wikipedia or Deezer is a *candidate* until the
reader picks it; once picked it is persisted, because an answer that outlives the
only copy of the work it is about cannot be read a month later.

Keeping both lookups in one module means the rest of the codebase never has to
know which kind of work it is holding.
"""

from __future__ import annotations

from src.models import Work
from src.registry import all_works as _curated_works
from src.registry import get_work as _curated_work


def get_work(work_id: str) -> Work | None:
    work = _curated_work(work_id)
    if work is not None:
        return work
    from src.store import get_store

    return get_store().work(work_id)


def all_works() -> list[Work]:
    works = list(_curated_works())
    seen = {w.id for w in works}
    from src.store import get_store

    for work in get_store().works():
        if work.id not in seen:
            works.append(work)
            seen.add(work.id)
    return works


def remember(work: Work) -> Work:
    """Persist a work resolved online so later pages can still name it."""
    from src.store import get_store

    return get_store().save_work(work)
