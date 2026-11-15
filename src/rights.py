"""
Rights enforcement gate — the single point where every render path passes.

§3.4: "Every render path — document, deepening, export, API — calls
rights.gate(source, span) -> RenderedText | CitationOnly. There is no second
implementation, no template that formats source.text directly, and no 'we'll
fix it at export'."

Design rules encoded here, not documented elsewhere:

* **Fail closed.** A source without a positive rights determination renders as
  metadata + link. `RightsInfo` defaults to `UNKNOWN` / `METADATA_ONLY`, and
  no adapter can widen that after the fact.
* **Policy is a ceiling, not a target.** The gate intersects the stored text
  with the display policy and the excerpt limit; it never "helpfully" stretches.
* **Degrade, never truncate a quote.** A span longer than the excerpt limit
  becomes a citation. Truncating it would silently misquote the author, which
  is worse than showing nothing.
* **Jurisdiction is strict.** When the determination was made somewhere more
  permissive than the reader's jurisdiction, the stricter determination wins.
* **Attribution travels.** `attribution_text` is returned with the text so a
  caller cannot render the words without the credit.

The Right-holders' facts we do not know — whether a 1927 recording is PD in the
EU, whether a poem is still in copyright in France — are not guessed. They are
`UNKNOWN`, which the gate treats as `METADATA_ONLY`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from src.models import (
    AuthorityTier,
    Determination,
    DisplayPolicy,
    RightsInfo,
    RightsStatus,
    Source,
    Temporal,
)

# ── Jurisdiction strictness ─────────────────────────────────────────────────
# Ordered from most permissive to most restrictive. A determination made under a
# permissive jurisdiction may not be used to show text in a stricter one unless
# the source itself is PD/CC in that stricter jurisdiction.
_STRICTNESS = ["US", "EU", "FR", "DE", "UK"]


def _stricter(a: str, b: str) -> str:
    ia = _STRICTNESS.index(a) if a in _STRICTNESS else len(_STRICTNESS)
    ib = _STRICTNESS.index(b) if b in _STRICTNESS else len(_STRICTNESS)
    return a if ia >= ib else b


# ═══════════════════════════════════════════════════════════════════════════
# GATE RESULT TYPES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RenderedText:
    """Text that passed the rights gate and may be shown."""

    text: str
    was_truncated: bool = False
    attribution: str | None = None
    policy: DisplayPolicy = DisplayPolicy.FULL_TEXT

    def __bool__(self) -> bool:
        return bool(self.text)


@dataclass(frozen=True)
class CitationOnly:
    """A source that may be named but not quoted."""

    reason: Literal["policy", "no_storage", "no_text", "redistribution", "jurisdiction"]
    title: str
    author: str
    date: str
    url: str | None
    policy: DisplayPolicy
    attribution: str | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return False


GateResult = RenderedText | CitationOnly


# ═══════════════════════════════════════════════════════════════════════════
# THE SINGLE GATE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def gate(
    source: Source,
    span_start: int | None = None,
    span_end: int | None = None,
    *,
    for_export: bool = False,
    reader_jurisdiction: str | None = None,
) -> GateResult:
    """Decide what of ``source`` may be shown, and return it.

    ``span_start``/``span_end`` select the exact characters a claim relies on.
    Passing a span is what makes a quote verifiable: the gate returns exactly
    that text, or nothing at all.
    """
    rights = source.rights
    date_display = _date_display(source.date_document)

    def _cite(reason: str, detail: str = "") -> CitationOnly:
        return CitationOnly(
            reason=reason,  # type: ignore[arg-type]
            title=source.title,
            author=source.author,
            date=date_display,
            url=source.url,
            policy=rights.display_policy,
            attribution=rights.attribution_text,
            detail=detail,
        )

    # 1. Jurisdiction: take the stricter of determination and reader.
    #    This bites on public domain, which is national, not global: a work that
    #    is PD in the US may still be in copyright in France, and we do not get
    #    to show it there just because we determined it here. Licences (CC BY et
    #    al.) are the same everywhere, so they are not re-litigated per reader.
    status_needs_jurisdiction = rights.status in (
        RightsStatus.PUBLIC_DOMAIN,
        RightsStatus.UNKNOWN,
        RightsStatus.IN_COPYRIGHT_UNLICENSED,
    )
    if reader_jurisdiction and status_needs_jurisdiction:
        strict = _stricter(rights.jurisdiction, reader_jurisdiction)
        if strict != rights.jurisdiction:
            return _cite(
                "jurisdiction",
                f"determination made in {rights.jurisdiction}, reader in {strict}",
            )

    # 2. Storage: if we never stored the text we cannot show it.
    if not rights.storage_allowed:
        return _cite("no_storage", "rights do not permit storing the text")

    # 3. Export: redistribution may be stricter than display.
    if for_export and not rights.redistribution_allowed:
        return _cite("redistribution", "licence does not permit redistribution")

    # 4. Policy ceiling.
    if rights.display_policy in (DisplayPolicy.LINK_ONLY, DisplayPolicy.METADATA_ONLY):
        return _cite("policy", f"policy {rights.display_policy.value}")

    # 5. Text presence.
    if not source.text:
        return _cite("no_text", "no text stored for this source")

    # 6. Apply policy to the stored text (degrade, never truncate a span).
    rendered, truncated = rights.gate_text(source.text, span_start, span_end)
    if not rendered:
        return _cite(
            "policy",
            "requested span exceeds the excerpt limit; claim must degrade to a citation",
        )

    return RenderedText(
        text=rendered,
        was_truncated=truncated,
        attribution=rights.attribution_text,
        policy=rights.display_policy,
    )


def gate_span(
    source: Source,
    span_start: int,
    span_end: int,
    *,
    for_export: bool = False,
    reader_jurisdiction: str | None = None,
) -> str | None:
    """Return the gated span text, or None when it may not be shown."""
    result = gate(
        source,
        span_start,
        span_end,
        for_export=for_export,
        reader_jurisdiction=reader_jurisdiction,
    )
    return result.text if isinstance(result, RenderedText) else None


def gate_full_text(source: Source, *, for_export: bool = False) -> GateResult:
    """Gate the whole stored text ("view full source")."""
    return gate(source, for_export=for_export)


def gate_excerpt(
    source: Source, max_chars: int = 300, *, for_export: bool = False
) -> GateResult:
    """Gate a shortened excerpt of the beginning of a source (source card)."""
    if not source.text:
        return gate(source, for_export=for_export)
    if not source.rights.storage_allowed:
        return gate(source, for_export=for_export)

    limit = source.rights.excerpt_limit_chars or max_chars
    limit = min(limit, max_chars)
    if limit <= 0:
        return gate(source, for_export=for_export)

    narrowed = source.model_copy(
        update={"rights": source.rights.model_copy(update={"excerpt_limit_chars": limit})}
    )
    return gate(narrowed, for_export=for_export)


# ═══════════════════════════════════════════════════════════════════════════
# ATTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════

def attribution_line(source: Source) -> str | None:
    """The credit string the licence demands, or None if none is required.

    Deliberately *only* the credit: the export already prints the title, author,
    date and URL. Repeating them here is how attribution sections become
    unreadable walls that nobody checks.
    """
    rights = source.rights
    if not rights.attribution_required:
        return None
    credit = rights.attribution_text or f"{source.author}, {source.title}"
    if rights.license_id and not _mentions_license(credit, rights.license_id):
        credit = f"{credit} [{rights.license_id}]"
    return credit


def _mentions_license(credit: str, license_id: str) -> bool:
    """"CC BY-SA 4.0" already says CC-BY-SA-4.0; do not say it twice."""
    def squash(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    return squash(license_id) in squash(credit)


# ═══════════════════════════════════════════════════════════════════════════
# INGEST-TIME DETERMINATION (§5 matrix)
# ═══════════════════════════════════════════════════════════════════════════

# Adapters call these; they are the only sanctioned way to claim rights.
# Anything not in this table stays UNKNOWN and is therefore metadata-only.

def wikipedia_rights() -> RightsInfo:
    return RightsInfo.cc_by_sa("4.0")


def wikidata_rights() -> RightsInfo:
    return RightsInfo.cc0()


def imslp_rights(*, public_domain_file: bool) -> RightsInfo:
    if public_domain_file:
        return RightsInfo.public_domain("EU")
    return RightsInfo.link_only(RightsStatus.UNKNOWN, notes="IMSLP file status unknown")


def genius_rights() -> RightsInfo:
    return RightsInfo.link_only(
        RightsStatus.IN_COPYRIGHT_UNLICENSED,
        license_id="GENIUS-TOS",
        notes="Lyrics are licensed and not redistributable; link only, never stored.",
    )


def poetry_foundation_rights() -> RightsInfo:
    return RightsInfo.link_only(
        RightsStatus.IN_COPYRIGHT_UNLICENSED,
        notes="Poetry Foundation texts are copyrighted; link only.",
    )


def poets_org_rights(*, public_domain: bool) -> RightsInfo:
    if public_domain:
        return RightsInfo.public_domain("US")
    return RightsInfo.link_only()


def semantic_scholar_rights(*, has_open_abstract: bool) -> RightsInfo:
    return RightsInfo(
        status=RightsStatus.CC_BY if has_open_abstract else RightsStatus.UNKNOWN,
        license_id="CC-BY-4.0" if has_open_abstract else None,
        jurisdiction="US",
        determination=Determination.API_METADATA,
        display_policy=DisplayPolicy.EXCERPT if has_open_abstract else DisplayPolicy.METADATA_ONLY,
        excerpt_limit_chars=400 if has_open_abstract else None,
        storage_allowed=has_open_abstract,
        redistribution_allowed=has_open_abstract,
        attribution_required=True,
        attribution_text="Semantic Scholar open abstract, CC BY 4.0" if has_open_abstract else None,
    )


def openalex_rights() -> RightsInfo:
    """OpenAlex is CC0 for its dataset; abstracts arrive through Crossref.

    This is the narrowest honest claim we can make: we may store and excerpt the
    abstract, and we credit the dataset and the paper. It is *not* a claim that
    the publisher licensed the text to us for redistribution beyond quotation —
    so the excerpt limit is small and short.
    """
    return RightsInfo(
        status=RightsStatus.CC0,
        license_id="CC0-1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        jurisdiction="US",
        determination=Determination.API_METADATA,
        display_policy=DisplayPolicy.EXCERPT,
        excerpt_limit_chars=400,
        storage_allowed=True,
        redistribution_allowed=True,
        attribution_required=True,
        attribution_text="OpenAlex (CC0), abstract via Crossref",
    )


def archive_org_rights(*, declared_public_domain: bool) -> RightsInfo:
    if declared_public_domain:
        return RightsInfo.public_domain("US")
    return RightsInfo.link_only(RightsStatus.UNKNOWN, notes="Internet Archive item rights unknown")


def unknown_rights(jurisdiction: str = "US") -> RightsInfo:
    return RightsInfo.unknown(jurisdiction)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _date_display(t: Temporal | None) -> str:
    if t is None:
        return "undated"
    return t.display or "undated"


def needs_recheck(rights: RightsInfo, *, today: date | None = None) -> bool:
    """§3.4: a heuristic determination older than 12 months is re-checked."""
    today = today or date.today()
    if rights.determination != Determination.HEURISTIC:
        return False
    age_days = (today - rights.determined_at).days
    return age_days > 365


def tier_weight(tier: AuthorityTier) -> float:
    return {
        AuthorityTier.PRIMARY: 1.0,
        AuthorityTier.SCHOLARLY: 0.8,
        AuthorityTier.REFERENCE: 0.6,
        AuthorityTier.CONTEMPORARY_REVIEW: 0.5,
    }[tier]
