"""The stored object is one document per work, grown version by version (§3.9).

An answer is a *view*: it is what one reader asked, answered from the work's
document as it stood that day. The document is the thing that accumulates — the
identity of the work, the dossier of facts, and the readings people have
published about it.

Three registers, three rules, and this module is where the rules are enforced
rather than remembered:

* the work is identity — a version carries the work record it was built from, so
  a later catalogue rename does not silently rewrite history;
* the dossier is facts — an entry with no citation is refused by the model, not
  by a reviewer;
* the readings are opinions — a reading with no author is refused, because an
  unattributed reading is exactly the failure this product exists to prevent.

Versions are append-only. `add` refuses an edit whose parent is not the current
version (two editors starting from the same paragraph is a lost update, not a
merge) and refuses an id that already exists. A rollback is a new version.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from src.models import Citation, Work, ulid


class EditRefused(Exception):
    """The edit cannot enter the document as it stands. The caller must re-read."""


class ReadingStatus(str, Enum):
    RECORDED = "RECORDED"        # someone published it; the page reports it
    CONTESTED = "CONTESTED"      # another reading disagrees with it
    ACCEPTED = "ACCEPTED"        # passed the evidence gates and the review (§10.6)


class Reading(BaseModel):
    """An interpretation, attributed to a person or a publication.

    Never "the meaning": the page may order readings by standing, and it must
    say when they disagree, but it does not speak in their voice.
    """
    id: str = Field(default_factory=ulid)
    text: str = Field(min_length=1, max_length=8_000)
    author: str = Field(min_length=1, max_length=64)   # handle, critic, publication
    citations: list[Citation] = Field(min_length=1, max_length=20)
    status: ReadingStatus = ReadingStatus.RECORDED


class DossierEntry(BaseModel):
    """A fact about the work, with the document and span that record it.

    Biography feeds the dossier; it never explains the work. "His mother died in
    1836" is context, not a reading of the poem.
    """
    predicate: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4_000)
    citations: list[Citation] = Field(min_length=1, max_length=20)
    date_display: str = ""


class DocumentVersion(BaseModel):
    """One state of the document. Never edited once added; superseded instead."""
    id: str = Field(default_factory=ulid)
    parent_version_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    author: str = "system"                 # handle, account, or "system"
    summary: str = ""                      # what changed, in the author's words
    citations: list[Citation] = Field(default_factory=list)
    work: Work                             # identity as it stood at this version
    dossier: list[DossierEntry] = Field(default_factory=list)
    readings: list[Reading] = Field(default_factory=list)


class WorkDocument(BaseModel):
    """Every version of one work's document, oldest first."""
    work_id: str
    versions: tuple[DocumentVersion, ...] = ()

    @property
    def current(self) -> DocumentVersion | None:
        return self.versions[-1] if self.versions else None

    def version(self, version_id: str) -> DocumentVersion | None:
        return next((v for v in self.versions if v.id == version_id), None)

    def add(self, version: DocumentVersion) -> DocumentVersion:
        """Append a version, or refuse the edit. The document only grows."""
        if version.work.id != self.work_id:
            raise EditRefused(
                f"version is about {version.work.id}, document is about {self.work_id}"
            )
        if self.version(version.id) is not None:
            raise EditRefused(f"version {version.id} is already in the document")
        head = self.current
        expected = head.id if head else None
        if version.parent_version_id != expected:
            raise EditRefused(
                "the document changed since this edit started"
                f" (expected parent {expected}, got {version.parent_version_id})"
            )
        self.versions = (*self.versions, version)
        return version

    def rollback(self, version_id: str, *, author: str, summary: str = "") -> DocumentVersion:
        """Restore an earlier version as a new version; nothing is deleted."""
        target = self.version(version_id)
        if target is None:
            raise EditRefused(f"no version {version_id} in this document")
        restored = target.model_copy(update={
            "id": ulid(),
            "parent_version_id": self.versions[-1].id,
            "created_at": datetime.now(),
            "author": author,
            "summary": summary or (
                f"Reverted to the version of {target.created_at:%Y-%m-%d}"
                f" by {target.author}."
            ),
        })
        return self.add(restored)
