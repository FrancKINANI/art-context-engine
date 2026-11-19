"""The unit of storage is the work, and a version is never edited.

Three failures this pins, all of which the per-answer model makes invisible:

* two readers correct the same paragraph at once, and the second save silently
  erases the first (a lost update is not a merge);
* a "fact" is added to the dossier with no document behind it, so the page
  states something nobody wrote;
* an interpretation is stored without its author, and six months later nobody
  can tell a contributor's reading from the work's own voice.
"""

from __future__ import annotations

import pytest

from src.documents import (
    DocumentVersion,
    DossierEntry,
    EditRefused,
    Reading,
    WorkDocument,
)
from src.models import Citation, CreatorRef, Work, WorkType
from src.temporal import year


def _work() -> Work:
    return Work(
        id="wd:test",
        title="Clair de lune",
        type=WorkType.MUSIC,
        creators=[CreatorRef(name="Claude Debussy", role="composer")],
        date_created=year(1905),
    )


def _citation(source: str = "wikipedia:1") -> Citation:
    return Citation(source_id=source, span_start=0, span_end=10, text="a document")


def _entry(text: str = "Premiered in Paris.") -> DossierEntry:
    return DossierEntry(predicate="premiere", text=text, citations=[_citation()])


def _version(parent: str | None = None, **overrides) -> DocumentVersion:
    data = dict(
        work=_work(),
        author="julius",
        summary="First reading.",
        parent_version_id=parent,
        dossier=[_entry()],
        readings=[
            Reading(
                text="The piece sets Verlaine's poem without quoting it.",
                author="A critic",
                citations=[_citation("scholar:1")],
            )
        ],
    )
    data.update(overrides)
    return DocumentVersion(**data)


def test_the_first_version_is_the_document():
    document = WorkDocument(work_id="wd:test")
    assert document.current is None
    version = document.add(_version())
    assert document.current is version
    assert document.versions == (version,)


def test_an_edit_that_builds_on_a_stale_version_is_refused():
    """Two editors start from v1; the second must not erase the first."""
    document = WorkDocument(work_id="wd:test")
    first = document.add(_version())
    document.add(_version(parent=first.id, author="reader", summary="Second."))
    stale = _version(parent=first.id, author="other", summary="Also second.")
    with pytest.raises(EditRefused):
        document.add(stale)


def test_a_version_for_another_work_is_refused():
    document = WorkDocument(work_id="wd:test")
    other = _work().model_copy(update={"id": "wd:other"})
    with pytest.raises(EditRefused):
        document.add(_version(work=other))


def test_the_same_version_cannot_be_added_twice():
    document = WorkDocument(work_id="wd:test")
    version = document.add(_version())
    with pytest.raises(EditRefused):
        document.add(version)


def test_versions_are_returned_as_a_copy():
    document = WorkDocument(work_id="wd:test")
    document.add(_version())
    before = document.versions
    document.add(_version(parent=before[0].id))
    assert len(before) == 1


def test_a_dossier_entry_without_a_document_is_refused():
    """A fact with no citation is an assertion about the world, not a record."""
    with pytest.raises(ValueError):
        DossierEntry(predicate="premiere", text="Premiered in Paris.", citations=[])


def test_a_reading_without_an_author_is_refused():
    """The page must never state a reading as the work's own voice (§3.9)."""
    with pytest.raises(ValueError):
        Reading(text="It is about the moon.", author="", citations=[_citation()])
    with pytest.raises(ValueError):
        Reading(text="It is about the moon.", author="Someone", citations=[])


def test_rollback_is_a_version_and_the_old_ones_stay_readable():
    document = WorkDocument(work_id="wd:test")
    first = document.add(_version())
    second = document.add(_version(
        parent=first.id, summary="Second.", readings=[],
    ))
    reverted = document.rollback(first.id, author="julius")

    assert len(document.versions) == 3
    assert reverted.id != first.id and reverted.parent_version_id == second.id
    assert reverted.readings == first.readings
    assert "Reverted" in reverted.summary
    # The version that was rolled back is still there; history is not rewritten.
    assert document.version(second.id) == second


def test_a_reading_is_bounded():
    """An unbounded text field is a database any visitor can fill."""
    with pytest.raises(ValueError):
        Reading(text="x" * 20_000, author="Someone", citations=[_citation()])
    with pytest.raises(ValueError):
        Reading(text="It is about the moon.", author="x" * 200,
                citations=[_citation()])
