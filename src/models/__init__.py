"""
Core data models for Art Context Engine.

These are the foundation types that everything else builds on.
No loose strings for dates, citations, or rights — every field is typed
and every constraint is enforced at the model level.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════
# ID TYPES
# ═══════════════════════════════════════════════════════════════════════

def ulid() -> str:
    """Generate a ULID-like string (time-sortable, 26 chars)."""
    # Simplified: timestamp + random
    import time
    import secrets
    timestamp = int(time.time() * 1000)
    return f"{timestamp:013x}{secrets.token_hex(6)}"


# ═══════════════════════════════════════════════════════════════════════
# TEMPORAL MODEL (§3.2)
# ═══════════════════════════════════════════════════════════════════════

class Precision(str, Enum):
    """Temporal precision levels."""
    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"
    DECADE = "DECADE"
    CENTURY = "CENTURY"
    UNKNOWN = "UNKNOWN"


class Qualifier(str, Enum):
    """How the date relates to the bounds."""
    EXACT = "EXACT"
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    CIRCA = "CIRCA"
    BETWEEN = "BETWEEN"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"


class Calendar(str, Enum):
    """Calendar system."""
    GREGORIAN = "GREGORIAN"
    JULIAN = "JULIAN"
    HIJRI = "HIJRI"
    OTHER = "OTHER"


# Circa windows per precision (years)
_CIRCA_WINDOW = {
    Precision.DAY: 1,
    Precision.MONTH: 1,
    Precision.YEAR: 2,
    Precision.DECADE: 5,
    Precision.CENTURY: 50,
    Precision.UNKNOWN: 100,
}


class Temporal(BaseModel):
    """
    A bounded interval with declared precision and verbatim original.

    This replaces all bare date strings. Every date in the system is one of these.
    """
    earliest: date | None = Field(
        default=None,
        description="Absolute lower bound; None = open past ('before 1820')"
    )
    latest: date | None = Field(
        default=None,
        description="Absolute upper bound; None = open future ('after 1820')"
    )
    precision: Precision = Field(
        default=Precision.UNKNOWN,
        description="Declared precision of this dating"
    )
    qualifier: Qualifier = Field(
        default=Qualifier.UNKNOWN,
        description="Relationship of the original claim to the bounds"
    )
    display: str = Field(
        default="",
        description="Verbatim original wording: 'Kurz nach 1820', 'c. 1791', '1821?'"
    )
    calendar: Calendar = Field(
        default=Calendar.GREGORIAN,
        description="Calendar system of the original date"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="How sure we are of the bounds themselves"
    )
    source_id: str | None = Field(
        default=None,
        description="The source that provided this dating"
    )

    # ═══════════════════════════════════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════════════════════════════════

    @model_validator(mode="after")
    def validate_bounds(self) -> Temporal:
        if self.earliest and self.latest and self.earliest > self.latest:
            raise ValueError("earliest cannot be after latest")
        return self

    @model_validator(mode="after")
    def validate_precision_consistency(self) -> Temporal:
        """DAY precision requires exact bounds."""
        if self.precision == Precision.DAY:
            if not (self.earliest and self.latest):
                raise ValueError("DAY precision requires both earliest and latest")
            if self.earliest != self.latest:
                raise ValueError("DAY precision requires earliest == latest")
        return self

    # ═══════════════════════════════════════════════════════════════════════
    # FACTORIES
    # ═══════════════════════════════════════════════════════════════════════

    @classmethod
    def exact(cls, d: date, display: str = "", source_id: str | None = None) -> Temporal:
        """Exact day."""
        return cls(
            earliest=d,
            latest=d,
            precision=Precision.DAY,
            qualifier=Qualifier.EXACT,
            display=display or d.isoformat(),
            source_id=source_id,
            confidence=1.0,
        )

    @classmethod
    def circa_year(cls, year: int, display: str = "", source_id: str | None = None) -> Temporal:
        """Circa a specific year (±2 years)."""
        from datetime import date as dt
        window = _CIRCA_WINDOW[Precision.YEAR]
        return cls(
            earliest=dt(year - window, 1, 1),
            latest=dt(year + window, 12, 31),
            precision=Precision.YEAR,
            qualifier=Qualifier.CIRCA,
            display=display or f"c. {year}",
            source_id=source_id,
            confidence=0.7,
        )

    @classmethod
    def decade(cls, decade_start: int, display: str = "", source_id: str | None = None) -> Temporal:
        """A decade (e.g., 1820s)."""
        from datetime import date as dt
        return cls(
            earliest=dt(decade_start, 1, 1),
            latest=dt(decade_start + 9, 12, 31),
            precision=Precision.DECADE,
            qualifier=Qualifier.EXACT,
            display=display or f"{decade_start}s",
            source_id=source_id,
            confidence=0.8,
        )

    @classmethod
    def before(cls, d: date, display: str = "", source_id: str | None = None) -> Temporal:
        """Before a date (open past)."""
        return cls(
            earliest=None,
            latest=d,
            precision=Precision.YEAR,  # conservative
            qualifier=Qualifier.BEFORE,
            display=display or f"before {d.isoformat()}",
            source_id=source_id,
            confidence=0.6,
        )

    @classmethod
    def after(cls, d: date, display: str = "", source_id: str | None = None) -> Temporal:
        """After a date (open future)."""
        return cls(
            earliest=d,
            latest=None,
            precision=Precision.YEAR,
            qualifier=Qualifier.AFTER,
            display=display or f"after {d.isoformat()}",
            source_id=source_id,
            confidence=0.6,
        )

    @classmethod
    def unknown(cls, reason: str = "date unknown", source_id: str | None = None) -> Temporal:
        """Completely unknown date."""
        return cls(
            earliest=None,
            latest=None,
            precision=Precision.UNKNOWN,
            qualifier=Qualifier.UNKNOWN,
            display=reason,
            source_id=source_id,
            confidence=0.0,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════

    def overlaps(self, other: Temporal) -> bool:
        """Check if two temporal intervals overlap."""
        # Handle open bounds
        self_early = self.earliest or date.min
        self_late = self.latest or date.max
        other_early = other.earliest or date.min
        other_late = other.latest or date.max
        return self_early <= other_late and other_early <= self_late

    def contains(self, other: Temporal) -> bool:
        """Check if this interval fully contains another."""
        self_early = self.earliest or date.min
        self_late = self.latest or date.max
        other_early = other.earliest or date.min
        other_late = other.latest or date.max
        return self_early <= other_early and other_late <= self_late

    def sort_key(self) -> tuple:
        """Key for sorting: open pasts first, then by earliest, then open futures last."""
        early = self.earliest or date.min
        late = self.latest or date.max
        is_open_past = self.earliest is None
        is_open_future = self.latest is None
        return (is_open_past, early, is_open_future, late)

    def circa_window_years(self) -> int:
        """Get the circa window in years for this precision."""
        return _CIRCA_WINDOW[self.precision]

    def __str__(self) -> str:
        return self.display or "unknown"


# ════════════════════════════════════════════════════════════════════════
# GEOGRAPHIC REFERENCE
# ════════════════════════════════════════════════════════════════════════

class GeoRef(BaseModel):
    """Geographic reference with optional coordinates."""
    name: str
    wikidata_id: str | None = None  # QID
    lat: float | None = None
    lon: float | None = None
    country: str | None = None
    region: str | None = None


# ════════════════════════════════════════════════════════════════════════
# RIGHTS MODEL (§3.4)
# ════════════════════════════════════════════════════════════════════════

class RightsStatus(str, Enum):
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    CC0 = "CC0"
    CC_BY = "CC_BY"
    CC_BY_SA = "CC_BY_SA"
    CC_BY_NC = "CC_BY_NC"
    CC_BY_NC_SA = "CC_BY_NC_SA"
    PROPRIETARY_LICENSED = "PROPRIETARY_LICENSED"
    IN_COPYRIGHT_UNLICENSED = "IN_COPYRIGHT_UNLICENSED"
    UNKNOWN = "UNKNOWN"


class Determination(str, Enum):
    API_METADATA = "API_METADATA"
    PAGE_DECLARED = "PAGE_DECLARED"
    CURATOR = "CURATOR"
    HEURISTIC = "HEURISTIC"


class DisplayPolicy(str, Enum):
    FULL_TEXT = "FULL_TEXT"
    EXCERPT = "EXCERPT"
    QUOTE_SPAN = "QUOTE_SPAN"
    LINK_ONLY = "LINK_ONLY"
    METADATA_ONLY = "METADATA_ONLY"


class AuthorityTier(str, Enum):
    PRIMARY = "PRIMARY"
    SCHOLARLY = "SCHOLARLY"
    REFERENCE = "REFERENCE"
    CONTEMPORARY_REVIEW = "CONTEMPORARY_REVIEW"


class SourceType(str, Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    TERTIARY = "TERTIARY"
    CONTEMPORARY_REVIEW = "CONTEMPORARY_REVIEW"


class RightsInfo(BaseModel):
    """
    Complete rights determination for a source.

    The enforcement gate (§3.4) uses this to decide what may be stored,
    shown in the document, and included in exports.
    """
    status: RightsStatus = RightsStatus.UNKNOWN
    license_id: str | None = None  # SPDX-style: "CC-BY-4.0", "PD-US", "GENIUS-TOS"
    license_url: str | None = None
    jurisdiction: str = Field(default="US", description="Where determination was made")
    rights_holder: str | None = None
    determined_at: date = Field(default_factory=date.today)
    determination: Determination = Determination.HEURISTIC
    display_policy: DisplayPolicy = DisplayPolicy.METADATA_ONLY
    excerpt_limit_chars: int | None = None
    storage_allowed: bool = False
    redistribution_allowed: bool = False
    attribution_required: bool = True
    attribution_text: str | None = None
    notes: str | None = None

    # ═══════════════════════════════════════════════════════════════════════
    # GATE LOGIC
    # ═══════════════════════════════════════════════════════════════════════

    def gate_text(self, text: str, span_start: int | None = None, span_end: int | None = None) -> tuple[str, bool]:
        """
        Apply display policy to text.

        Returns: (rendered_text, was_truncated)
        """
        if not self.storage_allowed:
            return "", True

        # A requested span is a *selection*, not a truncation: with FULL_TEXT we
        # return exactly those characters rather than the whole document.
        wants_span = span_start is not None and span_end is not None
        if wants_span and not (0 <= span_start < span_end <= len(text)):
            return "", True

        if self.display_policy == DisplayPolicy.FULL_TEXT:
            if wants_span:
                return text[span_start:span_end], False
            return text, False

        if self.display_policy == DisplayPolicy.METADATA_ONLY:
            return "", True

        if self.display_policy == DisplayPolicy.LINK_ONLY:
            return "", True

        if self.display_policy == DisplayPolicy.EXCERPT:
            limit = self.excerpt_limit_chars or 300
            if wants_span:
                excerpt = text[span_start:span_end]
                # §3.4: a span that does not fit degrades to a citation. It is
                # never truncated into a misquote.
                if len(excerpt) > limit:
                    return "", True
                return excerpt, False
            if len(text) > limit:
                return text[:limit] + "…", True
            return text, False

        if self.display_policy == DisplayPolicy.QUOTE_SPAN:
            if not wants_span:
                return "", True
            limit = self.excerpt_limit_chars or 300
            span = text[span_start:span_end]
            if len(span) > limit:
                return "", True
            return span, False

        return "", True

    def gate_span(self, text: str, span_start: int, span_end: int) -> str | None:
        """Get the gated span for a claim, or None if not displayable."""
        rendered, truncated = self.gate_text(text, span_start, span_end)
        if not rendered:
            return None
        return rendered

    @classmethod
    def unknown(cls, jurisdiction: str = "US") -> RightsInfo:
        """Fail-closed default for undetermined rights."""
        return cls(
            status=RightsStatus.UNKNOWN,
            jurisdiction=jurisdiction,
            determination=Determination.HEURISTIC,
            display_policy=DisplayPolicy.METADATA_ONLY,
            storage_allowed=False,
            redistribution_allowed=False,
        )

    @classmethod
    def public_domain(cls, jurisdiction: str = "US", determined_at: date | None = None) -> RightsInfo:
        """Public domain in the given jurisdiction."""
        return cls(
            status=RightsStatus.PUBLIC_DOMAIN,
            license_id="PD",
            jurisdiction=jurisdiction,
            determined_at=determined_at or date.today(),
            determination=Determination.CURATOR,
            display_policy=DisplayPolicy.FULL_TEXT,
            storage_allowed=True,
            redistribution_allowed=True,
            attribution_required=False,
        )

    @classmethod
    def cc_by(cls, version: str = "4.0", jurisdiction: str = "US") -> RightsInfo:
        """Creative Commons Attribution."""
        return cls(
            status=RightsStatus.CC_BY,
            license_id=f"CC-BY-{version}",
            license_url=f"https://creativecommons.org/licenses/by/{version}/",
            jurisdiction=jurisdiction,
            determination=Determination.API_METADATA,
            display_policy=DisplayPolicy.EXCERPT,
            excerpt_limit_chars=500,
            storage_allowed=True,
            redistribution_allowed=True,
            attribution_required=True,
            attribution_text=f"CC BY {version}",
        )

    @classmethod
    def cc_by_sa(cls, version: str = "4.0", jurisdiction: str = "US") -> RightsInfo:
        """Creative Commons Attribution-ShareAlike (Wikipedia)."""
        return cls(
            status=RightsStatus.CC_BY_SA,
            license_id=f"CC-BY-SA-{version}",
            license_url=f"https://creativecommons.org/licenses/by-sa/{version}/",
            jurisdiction=jurisdiction,
            determination=Determination.PAGE_DECLARED,
            display_policy=DisplayPolicy.EXCERPT,
            excerpt_limit_chars=500,
            storage_allowed=True,
            redistribution_allowed=True,
            attribution_required=True,
            attribution_text=f"Wikipedia contributors, CC BY-SA {version}",
        )

    @classmethod
    def cc0(cls, jurisdiction: str = "US") -> RightsInfo:
        """Facts from a CC0 dataset (Wikidata). Metadata only in practice."""
        return cls(
            status=RightsStatus.CC0,
            license_id="CC0-1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            jurisdiction=jurisdiction,
            determination=Determination.API_METADATA,
            display_policy=DisplayPolicy.METADATA_ONLY,
            storage_allowed=True,
            redistribution_allowed=True,
            attribution_required=False,
        )

    @classmethod
    def link_only(
        cls,
        status: RightsStatus = RightsStatus.IN_COPYRIGHT_UNLICENSED,
        jurisdiction: str = "US",
        license_id: str | None = None,
        notes: str | None = None,
    ) -> RightsInfo:
        """Cite and link; never store or show the text (lyrics, paywall)."""
        return cls(
            status=status,
            license_id=license_id,
            jurisdiction=jurisdiction,
            determination=Determination.PAGE_DECLARED,
            display_policy=DisplayPolicy.LINK_ONLY,
            storage_allowed=False,
            redistribution_allowed=False,
            attribution_required=True,
            notes=notes,
        )

    @classmethod
    def quote_span(cls, limit: int = 400, jurisdiction: str = "US") -> RightsInfo:
        """Only a verified span may be shown, never the full text."""
        return cls(
            status=RightsStatus.PROPRIETARY_LICENSED,
            license_id="LICENSED-QUOTE",
            jurisdiction=jurisdiction,
            determination=Determination.CURATOR,
            display_policy=DisplayPolicy.QUOTE_SPAN,
            excerpt_limit_chars=limit,
            storage_allowed=True,
            redistribution_allowed=False,
            attribution_required=True,
        )

    @property
    def shows_text(self) -> bool:
        """Convenience for ranking and UI: may any text be rendered at all?"""
        return self.storage_allowed and self.display_policy in (
            DisplayPolicy.FULL_TEXT,
            DisplayPolicy.EXCERPT,
            DisplayPolicy.QUOTE_SPAN,
        )


# ════════════════════════════════════════════════════════════════════════
# WORK ENTITY (§3.1)
# ════════════════════════════════════════════════════════════════════════

class WorkType(str, Enum):
    MUSIC = "MUSIC"
    POETRY = "POETRY"
    VISUAL_ART = "VISUAL_ART"
    LITERATURE = "LITERATURE"
    FILM = "FILM"
    THEATER = "THEATER"


class CreatorRef(BaseModel):
    """Reference to a creator (composer, poet, artist, etc.)."""
    name: str
    wikidata_id: str | None = None  # QID
    role: str  # "composer", "performer", "poet", "artist", "lyricist", "producer"
    birth_date: Temporal | None = None
    death_date: Temporal | None = None


class Work(BaseModel):
    """A creative work — the central entity we assemble context for."""
    id: str = Field(default_factory=ulid)
    title: str
    type: WorkType
    creators: list[CreatorRef] = Field(default_factory=list)
    date_created: Temporal | None = None
    date_published: Temporal | None = None
    location_created: GeoRef | None = None
    location_premiered: GeoRef | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)  # ISWC, ISRC, OCLC, QID, MBID
    metadata: dict[str, Any] = Field(default_factory=dict)  # form, key, instrumentation, language, medium

    @property
    def primary_creator(self) -> CreatorRef | None:
        return self.creators[0] if self.creators else None


# ════════════════════════════════════════════════════════════════════════
# SOURCE DOCUMENT (§3.3)
# ════════════════════════════════════════════════════════════════════════

class Citation(BaseModel):
    """Internal citation within a source."""
    source_id: str
    span_start: int
    span_end: int
    text: str


class Source(BaseModel):
    """
    A source document with full rights and temporal information.

    This is the atomic unit of evidence.
    """
    id: str = Field(default_factory=ulid)
    work_id: str
    type: SourceType
    subtype: str  # "letter", "diary", "review", "scholarly_article", "sketch", "interview", "liner_notes", "score"
    title: str
    author: str
    date_document: Temporal  # when this document was written/published (§3.2.1)
    location: GeoRef | None = None
    url: str | None = None
    text: str = ""  # stored text, subject to rights.storage_allowed
    language: str = "en"
    rights: RightsInfo = Field(default_factory=lambda: RightsInfo.unknown())
    authority: AuthorityTier = AuthorityTier.REFERENCE
    citations: list[Citation] = Field(default_factory=list)

    # ═══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    def is_primary(self) -> bool:
        return self.type == SourceType.PRIMARY

    def is_scholarly(self) -> bool:
        return self.authority == AuthorityTier.SCHOLARLY

    def can_display_text(self) -> bool:
        return self.rights.display_policy in (DisplayPolicy.FULL_TEXT, DisplayPolicy.EXCERPT, DisplayPolicy.QUOTE_SPAN)

    def get_display_text(self, span_start: int | None = None, span_end: int | None = None) -> str:
        """Get rights-gated text for display."""
        rendered, _ = self.rights.gate_text(self.text, span_start, span_end)
        return rendered


# ════════════════════════════════════════════════════════════════════════
# TIMELINE EVENT
# ════════════════════════════════════════════════════════════════════════

class TimelineEvent(BaseModel):
    """An event on the work's timeline."""
    id: str = Field(default_factory=ulid)
    when: Temporal  # when the event happened
    date_asserted_by_document: Temporal | None = None  # when the evidence said it (§3.2.1)
    title: str
    description: str = ""
    event_type: str = "composition"  # "composition", "premiere", "letter", "review", "life", "world"
    attestations: list[Attestation] = Field(default_factory=list)
    location: GeoRef | None = None
    work_id: str | None = None

    @property
    def attested_by(self) -> list[str]:
        return [a.source_id for a in self.attestations]

    @property
    def sort_key(self) -> tuple:
        return self.when.sort_key()

    @property
    def is_undated(self) -> bool:
        return self.when.qualifier == Qualifier.UNKNOWN and self.when.earliest is None


# ════════════════════════════════════════════════════════════════════════
# GAP
# ════════════════════════════════════════════════════════════════════════

class Gap(BaseModel):
    """A known gap in the evidence."""
    id: str = Field(default_factory=ulid)
    work_id: str
    description: str
    gap_type: str  # "no_sources", "undated", "contradictory", "missing_period"
    related_subject: str | None = None  # e.g., "op111.mov2.metronome"
    severity: str = "MATERIAL"  # MATERIAL, MINOR


# ════════════════════════════════════════════════════════════════════════
# ANNOTATION (PRIVATE)
# ════════════════════════════════════════════════════════════════════════

class Annotation(BaseModel):
    """User's private note on a source or block."""
    id: str = Field(default_factory=ulid)
    target_type: Literal["source", "block", "claim", "work", "answer"]
    target_id: str
    text: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ════════════════════════════════════════════════════════════════════════
# SPAN — the address of evidence inside a document (§4.4)
# ════════════════════════════════════════════════════════════════════════

class Span(BaseModel):
    """Character offsets into a stored document text.

    A claim without a verifiable span is not evidence, so this is a model and
    not a (start, end) tuple: it carries the text it claims to point at, which
    is what `answer.claims.verify_span` checks against the source.
    """
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = ""  # the span as extracted; checked against source.text at ingest

    @model_validator(mode="after")
    def _ordered(self) -> Span:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        return self

    def resolve(self, document: str) -> str | None:
        """Return the document text at these offsets, or None if out of range."""
        if self.start < 0 or self.end > len(document) or self.end <= self.start:
            return None
        return document[self.start:self.end]


# ════════════════════════════════════════════════════════════════════════
# CLAIMS (§3.6, §4.4)
# ════════════════════════════════════════════════════════════════════════

class Stance(str, Enum):
    SUPPORTS = "SUPPORTS"
    DISPUTES = "DISPUTES"
    INFERENCE = "INFERENCE"      # no source states it; renders as inference
    CITATION_ONLY = "CITATION_ONLY"  # source may be named, not quoted (lyrics, paywall)


class Polarity(str, Enum):
    ASSERT = "ASSERT"
    DENY = "DENY"


class ExtractedClaim(BaseModel):
    """A machine-read assertion from one source, with a mandatory span (§4.4).

    Conflicts are computed over these, never over prose.
    """
    id: str = Field(default_factory=ulid)
    work_id: str
    source_id: str
    span: Span
    subject: str                      # controlled vocabulary key: "op111.mov2.metronome"
    predicate: str                    # controlled vocabulary: "metronome_mark"
    value: Any = None                 # normalized JSON value
    value_text: str = ""              # as written: "= 72"
    polarity: Polarity = Polarity.ASSERT
    about: Temporal | None = None     # the period the claim describes
    qualifiers: dict[str, str] = Field(default_factory=dict)
    extractor_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    extractor: str = "rules-v1"

    def is_usable(self, threshold: float = 0.80) -> bool:
        return self.extractor_confidence >= threshold and bool(self.span.text.strip())


class Claim(BaseModel):
    """An assertion in the answer, tied to evidence (§3.6)."""
    id: str = Field(default_factory=ulid)
    text: str
    source_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)   # extracted claims backing this
    spans: list[Span] = Field(default_factory=list)
    stance: Stance = Stance.SUPPORTS
    extractor_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _evidence_required(self) -> Claim:
        if self.stance in (Stance.SUPPORTS, Stance.DISPUTES, Stance.CITATION_ONLY):
            if not self.source_ids:
                raise ValueError(f"claim with stance {self.stance} requires a source")
        return self


# ════════════════════════════════════════════════════════════════════════
# CONFLICTS (§4.5)
# ════════════════════════════════════════════════════════════════════════

class ConflictKind(str, Enum):
    FACTUAL = "FACTUAL"
    CHRONOLOGICAL = "CHRONOLOGICAL"
    ATTRIBUTIONAL = "ATTRIBUTIONAL"
    TEXTUAL = "TEXTUAL"
    INTERPRETIVE = "INTERPRETIVE"   # readings differ — never rendered as a warning
    NULL = "NULL"                   # silence — rendered as a gap


class Resolution(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    TIER_PREFERRED = "TIER_PREFERRED"
    CONSENSUS = "CONSENSUS"


class Severity(str, Enum):
    MATERIAL = "MATERIAL"
    MINOR = "MINOR"


class Position(BaseModel):
    """One side of a conflict: a claim, its source, and its span."""
    claim_id: str
    source_id: str
    source_title: str
    authority: AuthorityTier
    value: Any = None
    value_text: str = ""
    span: Span
    about: Temporal | None = None
    qualifiers: dict[str, str] = Field(default_factory=dict)


class Conflict(BaseModel):
    id: str = Field(default_factory=ulid)
    kind: ConflictKind
    subject: str
    predicate: str
    positions: list[Position] = Field(min_length=2)
    question: str = ""               # reader-facing framing
    resolution: Resolution = Resolution.UNRESOLVED
    preferred_position_id: str | None = None
    severity: Severity = Severity.MATERIAL
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    detected_at: datetime = Field(default_factory=datetime.now)
    detector: str = "rules-v1"


class ConflictCandidate(BaseModel):
    """Below-threshold conflict: curator-visible only (§4.5.2 step 5)."""
    id: str = Field(default_factory=ulid)
    kind: ConflictKind
    subject: str
    predicate: str
    claim_ids: list[str]
    reason: str
    confidence: float = 0.0


# ════════════════════════════════════════════════════════════════════════
# ANSWER DOCUMENT (§3.6)
# ════════════════════════════════════════════════════════════════════════

class QuestionType(str, Enum):
    CONTEXT = "CONTEXT"
    MEANING = "MEANING"
    TECHNIQUE = "TECHNIQUE"
    HISTORY = "HISTORY"
    COMPARISON = "COMPARISON"


class Audience(str, Enum):
    LEARNER = "LEARNER"
    STUDENT = "STUDENT"
    TEACHER = "TEACHER"


class Sufficiency(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    THIN = "THIN"
    INSUFFICIENT = "INSUFFICIENT"


class BlockKind(str, Enum):
    THESIS = "THESIS"
    CLOSE_READING = "CLOSE_READING"
    CONTEXT = "CONTEXT"
    QUOTE = "QUOTE"
    CONFLICT = "CONFLICT"
    GAP = "GAP"
    CLOSE = "CLOSE"


class Attestation(BaseModel):
    """Who asserts an event, and when they said it (§3.2.1)."""
    source_id: str
    span: Span | None = None
    asserted: Temporal | None = None   # when this document said it


class Quote(BaseModel):
    """Verbatim source text, rights-gated at render time."""
    source_id: str
    source_title: str
    author: str
    date_display: str
    authority: AuthorityTier
    text: str
    span: Span
    url: str | None = None
    truncated: bool = False


class Attribution(BaseModel):
    """A licence-mandated attribution line that travels with the document."""
    source_id: str
    title: str
    author: str
    date_display: str
    license_id: str | None = None
    license_url: str | None = None
    url: str | None = None
    attribution_text: str | None = None
    license_url_required: bool = False


class Deepening(BaseModel):
    """What 'go deeper' expands into, inline (§7.5)."""
    block_id: str
    quotes: list[Quote] = Field(default_factory=list)
    context: list[Source] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)


class Block(BaseModel):
    id: str = Field(default_factory=ulid)
    kind: BlockKind
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)
    deepening: Deepening | None = None

    @model_validator(mode="after")
    def _block_resolves(self) -> Block:
        """§3.7 rule 2: a block whose claims are empty fails assembly.

        CONFLICT and GAP blocks are evidence of absence and carry ids, not claims.
        """
        if self.kind in (BlockKind.CONFLICT, BlockKind.GAP):
            if not (self.conflict_ids or self.gap_ids):
                raise ValueError(f"{self.kind} block requires a conflict or gap id")
            return self
        if not self.claim_ids:
            raise ValueError(f"block {self.id} has no claims (§3.7 rule 2)")
        return self


class Answer(BaseModel):
    id: str = Field(default_factory=ulid)
    parent_id: str | None = None
    work_id: str
    question: str
    question_type: QuestionType = QuestionType.CONTEXT
    audience: Audience = Audience.LEARNER
    thesis: str = ""
    blocks: list[Block] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    attribution: list[Attribution] = Field(default_factory=list)
    sufficiency: Sufficiency = Sufficiency.SUFFICIENT
    sufficiency_note: str | None = None
    generated_at: datetime = Field(default_factory=datetime.now)
    model: str = "deterministic-v1"

    @property
    def permalink(self) -> str:
        return f"/a/{self.id}"

    def word_count(self) -> int:
        return sum(len(b.text.split()) for b in self.blocks)


# ════════════════════════════════════════════════════════════════════════
# CONTEXT PACK (evidence layer, §3.5)
# ════════════════════════════════════════════════════════════════════════

class ContextPack(BaseModel):
    work_id: str
    generated_at: datetime = Field(default_factory=datetime.now)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    conflict_candidates: list[ConflictCandidate] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    question: str | None = None
    question_type: QuestionType | None = None


# ════════════════════════════════════════════════════════════════════════
# RESOLUTION + FEEDBACK
# ════════════════════════════════════════════════════════════════════════

class WorkCandidate(BaseModel):
    """A possible match for the user's query — never a silent guess (§4.1)."""
    work_id: str
    title: str
    creator: str
    work_type: WorkType
    date_display: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_on: str = ""
    identifiers: dict[str, str] = Field(default_factory=dict)


class ContributionKind(str, Enum):
    """What a reader is adding to a document.

    CONTEXT adds a sourced fact the document missed; CORRECTION says a sentence
    in it is wrong. Both are claims about the work, so both need a source.
    """
    CONTEXT = "CONTEXT"
    CORRECTION = "CORRECTION"


class ContributionStatus(str, Enum):
    PENDING = "PENDING"      # not checked yet (offline install, fetch failed)
    ACCEPTED = "ACCEPTED"    # the quotation was found on the cited page
    REJECTED = "REJECTED"    # it was not


class Contribution(BaseModel):
    """A reader's addition, and the check that decides whether it counts.

    The check is mechanical on purpose: the contributor must quote a sentence
    from a public page, and we fetch that page and look for the sentence. That
    is the whole verification — no model is asked to agree, so an addition can
    never be accepted because it sounds plausible.
    """
    id: str = Field(default_factory=ulid)
    answer_id: str
    author: str
    kind: ContributionKind = ContributionKind.CONTEXT
    body: str
    quote: str
    source_url: str
    source_title: str = ""
    status: ContributionStatus = ContributionStatus.PENDING
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class FeedbackKind(str, Enum):
    # The three verdicts the document page offers, plus the two an annotator
    # can leave on a single block.
    USEFUL = "USEFUL"
    PARTLY = "PARTLY"
    MISSED = "MISSED"
    ADD_CONTEXT = "ADD_CONTEXT"
    THIS_IS_WRONG = "THIS_IS_WRONG"


class Feedback(BaseModel):
    id: str = Field(default_factory=ulid)
    answer_id: str
    block_id: str | None = None
    kind: FeedbackKind
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


# ════════════════════════════════════════════════════════════════════════
# FORWARD REFERENCES
# ════════════════════════════════════════════════════════════════════════

# TimelineEvent is declared before Attestation; with `from __future__ import
# annotations` the references are strings and need one explicit build pass.
_NAMESPACE = dict(globals())
for _name in ("TimelineEvent", "Block", "Claim", "Answer", "ContextPack", "Deepening"):
    _model = _NAMESPACE.get(_name)
    if isinstance(_model, type) and not _model.__pydantic_complete__:
        _model.model_rebuild(_types_namespace=_NAMESPACE)


# ════════════════════════════════════════════════════════════════════════
# EXPORTS
# ════════════════════════════════════════════════════════════════════════

__all__ = [
    # ID
    "ulid",
    # Temporal
    "Precision", "Qualifier", "Calendar", "Temporal", "_CIRCA_WINDOW",
    # Geo
    "GeoRef",
    # Rights
    "RightsStatus", "Determination", "DisplayPolicy", "AuthorityTier", "SourceType", "RightsInfo",
    # Work
    "WorkType", "CreatorRef", "Work",
    # Source
    "Citation", "Source", "Span",
    # Timeline
    "TimelineEvent", "Attestation",
    # Gap
    "Gap",
    # Annotation
    "Annotation",
    # Claims
    "Stance", "Polarity", "ExtractedClaim", "Claim",
    # Conflicts
    "ConflictKind", "Resolution", "Severity", "Position", "Conflict", "ConflictCandidate",
    # Answer
    "QuestionType", "Audience", "Sufficiency", "BlockKind", "Block", "Answer",
    "Quote", "Deepening", "Attribution",
    # Pack
    "ContextPack",
    # Resolution
    "WorkCandidate", "FeedbackKind", "Feedback",
]