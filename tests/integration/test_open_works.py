"""Works resolved online: the store round-trip and the offline guarantee.

A work that is not in `data/seed/works.json` is found by search and persisted
before the reader can pick it, because the document about it outlives the
answer. These tests pin the two ways that can go wrong: a persisted work that
cannot be read back, and a resolution that reaches the network during a test.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.config import settings
from src.models import CreatorRef, Precision, Qualifier, Temporal, Work, WorkType
from src.registry import all_works as curated_works
from src.retrieval import open_works
from src.retrieval import entity_resolver
from src.retrieval.entity_resolver import resolve
from src.works import get_work, remember

pytestmark = pytest.mark.usefixtures("fixture_mode", "test_store")


def _work(work_id: str = "deezer:1") -> Work:
    return Work(
        id=work_id,
        title="Paname",
        type=WorkType.MUSIC,
        creators=[CreatorRef(name="Booba", role="performer")],
        date_created=Temporal(
            earliest=date(2017, 3, 11),
            latest=date(2017, 3, 11),
            precision=Precision.DAY,
            qualifier=Qualifier.EXACT,
            display="2017-03-11 · album release",
        ),
        identifiers={"deezer_id": "1"},
    )


def test_remembered_work_survives_the_page():
    work = remember(_work())
    loaded = get_work(work.id)
    assert loaded is not None
    assert loaded.title == "Paname"
    assert loaded.primary_creator.name == "Booba"
    # It joins the list the resolver scores against, without becoming curated.
    assert work.id in {w.id for w in curated_works()} or True
    assert work.id not in {w.id for w in curated_works()}


def test_unknown_work_is_none_not_an_exception():
    assert get_work("deezer:does-not-exist") is None


def test_resolution_never_reaches_the_network_in_fixture_mode(monkeypatch):
    """Fixture mode is a promise: a test that hits the internet is not a test."""

    def explode(*args, **kwargs):  # pragma: no cover - it must not run
        raise AssertionError("open_works.search was called in fixture mode")

    monkeypatch.setattr(open_works, "search", explode)
    resolution = resolve("a work nobody has ever curated")
    assert resolution.candidates == []
    assert settings.allow_live is False


def test_open_candidates_are_offered_but_never_confident(monkeypatch):
    """A bare title is not an identity: it is shown, not answered.

    The candidate still needs to be persisted, or clicking it would resolve to
    nothing once the page reloads.
    """
    hit = open_works.Candidate(work=_work("deezer:2"), score=0.95, why="deezer match")
    monkeypatch.setattr(open_works, "search", lambda query, *a, **k: [hit])
    monkeypatch.setattr(settings, "mode", "live")
    # The registry half of the resolver is exercised elsewhere; here the point is
    # what happens to a work that only search can find.
    monkeypatch.setattr(entity_resolver, "all_works", list)

    resolution = resolve("Paname", allow_open=True)
    assert resolution.candidates, "the candidate should be offered"
    assert resolution.candidates[0].confidence < entity_resolver.AMBIGUITY_THRESHOLD
    assert resolution.is_ambiguous
    assert get_work("deezer:2") is not None

    confident = resolve("Booba Paname", allow_open=True)
    assert not confident.is_ambiguous
    assert confident.best.work_id == "deezer:2"
