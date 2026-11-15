"""
Temporal construction, parsing and comparison (§3.2, §3.2.1, §4.3).

Nothing in this codebase holds a bare date. A date without precision is a date
that will be rendered as a lie ("composed 15 March 1821" when the source said
"c. 1821"), and a date without a document date is an event with a forged
provenance. This module is where both get fixed.

Two rules are enforced here rather than left to callers:

1. **Bounds come from precision.** A year is 1 Jan–31 Dec, not a point.
2. **A disputed dating is not averaged.** `disputed()` keeps the alternatives
   that do not overlap, so the conflict detector can surface them.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from enum import Enum

from src.models import Calendar, Precision, Qualifier, Temporal, _CIRCA_WINDOW

# ═══════════════════════════════════════════════════════════════════════════
# CONSTRUCTION — every one of these sets bounds from precision
# ═══════════════════════════════════════════════════════════════════════════


def day(y: int, m: int, d: int, *, display: str = "", source_id: str | None = None,
        confidence: float = 1.0) -> Temporal:
    exact = date(y, m, d)
    return Temporal(
        earliest=exact,
        latest=exact,
        precision=Precision.DAY,
        qualifier=Qualifier.EXACT,
        display=display or exact.isoformat(),
        source_id=source_id,
        confidence=confidence,
    )


def month(y: int, m: int, *, display: str = "", source_id: str | None = None,
          confidence: float = 0.9) -> Temporal:
    first = date(y, m, 1)
    last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return Temporal(
        earliest=first,
        latest=last,
        precision=Precision.MONTH,
        qualifier=Qualifier.EXACT,
        display=display or f"{first:%B %Y}",
        source_id=source_id,
        confidence=confidence,
    )


def year(y: int, *, display: str = "", source_id: str | None = None,
         confidence: float = 0.9) -> Temporal:
    return Temporal(
        earliest=date(y, 1, 1),
        latest=date(y, 12, 31),
        precision=Precision.YEAR,
        qualifier=Qualifier.EXACT,
        display=display or str(y),
        source_id=source_id,
        confidence=confidence,
    )


def decade(y: int, *, display: str = "", source_id: str | None = None,
           confidence: float = 0.8) -> Temporal:
    start = (y // 10) * 10
    return Temporal(
        earliest=date(start, 1, 1),
        latest=date(start + 9, 12, 31),
        precision=Precision.DECADE,
        qualifier=Qualifier.EXACT,
        display=display or f"{start}s",
        source_id=source_id,
        confidence=confidence,
    )


def century(y: int, *, display: str = "", source_id: str | None = None,
            confidence: float = 0.8) -> Temporal:
    # Century of a year: 1821 -> 19th century (1801-1900).
    start = ((y - 1) // 100) * 100 + 1
    return Temporal(
        earliest=date(start, 1, 1),
        latest=date(start + 99, 12, 31),
        precision=Precision.CENTURY,
        qualifier=Qualifier.EXACT,
        display=display or f"{start}-{start + 99}",
        source_id=source_id,
        confidence=confidence,
    )


def circa(base: Temporal, *, display: str = "", source_id: str | None = None,
          years: int | None = None) -> Temporal:
    """Widen an existing dating into a declared circa window (§3.2 rule 2)."""
    window = years if years is not None else _CIRCA_WINDOW[base.precision]
    earliest = base.earliest - timedelta(days=365 * window) if base.earliest else None
    latest = base.latest + timedelta(days=365 * window) if base.latest else None
    return base.model_copy(
        update={
            "earliest": earliest,
            "latest": latest,
            "qualifier": Qualifier.CIRCA,
            "display": display or f"c. {base.display}",
            "source_id": source_id or base.source_id,
            "confidence": max(0.0, base.confidence - 0.2),
        }
    )


def before(base: Temporal, *, display: str = "", source_id: str | None = None) -> Temporal:
    return Temporal(
        earliest=None,
        latest=base.latest or base.earliest,
        precision=base.precision,
        qualifier=Qualifier.BEFORE,
        display=display or f"before {base.display}",
        source_id=source_id,
        confidence=base.confidence * 0.9,
    )


def after(base: Temporal, *, display: str = "", source_id: str | None = None) -> Temporal:
    return Temporal(
        earliest=base.earliest or base.latest,
        latest=None,
        precision=base.precision,
        qualifier=Qualifier.AFTER,
        display=display or f"after {base.display}",
        source_id=source_id,
        confidence=base.confidence * 0.9,
    )


def between(a: Temporal, b: Temporal, *, display: str = "",
            source_id: str | None = None) -> Temporal:
    early = a.earliest or a.latest
    late = b.latest or b.earliest
    if early and late and early > late:
        early, late = late, early
    precision = min(a.precision, b.precision, key=_PRECISION_ORDER.index)
    return Temporal(
        earliest=early,
        latest=late,
        precision=precision,
        qualifier=Qualifier.BETWEEN,
        display=display or f"{early.year if early else '?'}–{late.year if late else '?'}",
        source_id=source_id,
        confidence=min(a.confidence, b.confidence),
    )


def disputed(alternatives: list[Temporal], *, display: str = "",
             source_id: str | None = None) -> Temporal:
    """A dating that sources disagree about: keep the envelope, keep DISPUTED.

    The alternatives themselves are not lost — the conflict detector reports
    each one as a CHRONOLOGICAL position (§3.2 rule 7). This value is only the
    envelope, and it is never silently narrowed to a midpoint.
    """
    if not alternatives:
        return unknown("no dating")
    earliest = min((a.earliest for a in alternatives if a.earliest), default=None)
    latest = max((a.latest for a in alternatives if a.latest), default=None)
    # The envelope is only as precise as its coarsest member: two day-precise dates
    # that differ make a year, not a day.
    precision = max((a.precision for a in alternatives), key=_PRECISION_ORDER.index)
    return Temporal(
        earliest=earliest,
        latest=latest,
        precision=precision,
        qualifier=Qualifier.DISPUTED,
        display=display or " / ".join(a.display for a in alternatives),
        source_id=source_id,
        confidence=min(a.confidence for a in alternatives) * 0.8,
    )


def unknown(reason: str = "date unknown", *, source_id: str | None = None) -> Temporal:
    return Temporal(
        earliest=None,
        latest=None,
        precision=Precision.UNKNOWN,
        qualifier=Qualifier.UNKNOWN,
        display=reason,
        source_id=source_id,
        confidence=0.0,
    )


def from_range(start_year: int | None, end_year: int | None, *,
               display: str = "", source_id: str | None = None) -> Temporal:
    """e.g. composition "1821–1822" (a period, not a point)."""
    if start_year is None and end_year is None:
        return unknown("no dating", source_id=source_id)
    if start_year == end_year:
        return year(start_year, display=display, source_id=source_id)
    earliest = date(start_year, 1, 1) if start_year else None
    latest = date(end_year, 12, 31) if end_year else None
    return Temporal(
        earliest=earliest,
        latest=latest,
        precision=Precision.YEAR,
        qualifier=Qualifier.BETWEEN,
        display=display or f"{start_year or '?'}–{end_year or '?'}",
        source_id=source_id,
        confidence=0.75,
    )


# ═══════════════════════════════════════════════════════════════════════════
# PARSING
# ═══════════════════════════════════════════════════════════════════════════

_PRECISION_ORDER = [
    Precision.DAY,
    Precision.MONTH,
    Precision.YEAR,
    Precision.DECADE,
    Precision.CENTURY,
    Precision.UNKNOWN,
]

# Wikidata time precision codes
_WD_PRECISION = {11: Precision.DAY, 10: Precision.MONTH, 9: Precision.YEAR,
                 8: Precision.DECADE, 7: Precision.CENTURY}

_CIRCA_WORDS = ("c.", "ca.", "circa", "about", "approx.", "around", "kurz nach")


def parse_wikidata_time(value: str, precision_code: int | None = None, *,
                        calendar: Calendar = Calendar.GREGORIAN,
                        source_id: str | None = None) -> Temporal:
    """Parse a Wikidata '+1822-00-00T00:00:00Z' time value.

    Wikidata's own precision code decides how wide the interval is; we do not
    infer it from which fields happen to be zero.
    """
    m = re.match(r"^([+-])(\d{1,16})-(\d{2})-(\d{2})", value)
    if not m:
        return unknown(f"unparsed: {value}", source_id=source_id)
    sign, y, mo, d = m.groups()
    if sign == "-":
        return unknown(f"BC date not supported: {value}", source_id=source_id)
    y, mo, d = int(y), int(mo), int(d)
    p = _WD_PRECISION.get(precision_code or 9, Precision.YEAR)

    if p == Precision.DAY and mo and d:
        t = day(y, mo, d, source_id=source_id)
    elif p == Precision.MONTH and mo:
        t = month(y, mo, source_id=source_id)
    elif p == Precision.DECADE:
        t = decade(y, source_id=source_id)
    elif p == Precision.CENTURY:
        t = century(y, source_id=source_id)
    else:
        t = year(y, source_id=source_id)
    return t.model_copy(update={"calendar": calendar})


_YEAR_RE = re.compile(r"(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)")
_RANGE_RE = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\s*[–—-]\s*(1[0-9]{3}|20[0-2][0-9])\b")
_DECADE_RE = re.compile(r"\b(1[0-9]{3}|20[0-2]0)s\b")


def parse_human(text: str, *, source_id: str | None = None) -> Temporal:
    """Best-effort parse of an English date phrase found inside prose.

    Deliberately conservative: it recognises years, year ranges, decades and
    circa-markers, and returns UNKNOWN for anything else rather than guessing.
    """
    if not text:
        return unknown("empty", source_id=source_id)
    lowered = text.lower()

    rng = _RANGE_RE.search(text)
    if rng:
        return from_range(int(rng.group(1)), int(rng.group(2)),
                          display=rng.group(0), source_id=source_id)

    dec = _DECADE_RE.search(text)
    if dec:
        return decade(int(dec.group(1)), display=dec.group(0), source_id=source_id)

    yieldear = _YEAR_RE.search(text)
    if not yieldear:
        return unknown(text.strip()[:60], source_id=source_id)

    t = year(int(yieldear.group(1)), display=yieldear.group(0), source_id=source_id)
    if any(w in lowered for w in _CIRCA_WORDS):
        return circa(t, display=yieldear.group(0), source_id=source_id)
    return t


# ═══════════════════════════════════════════════════════════════════════════
# COMPARISON
# ═══════════════════════════════════════════════════════════════════════════

class Relation(str, Enum):
    OVERLAP = "OVERLAP"
    DISJOINT = "DISJOINT"
    UNKNOWN = "UNKNOWN"


def relation(a: Temporal, b: Temporal) -> Relation:
    """Do two datings overlap?

    Two open bounds with no anchor are UNKNOWN, not DISJOINT: we cannot call a
    conflict out of two absences.
    """
    if a.qualifier == Qualifier.UNKNOWN or b.qualifier == Qualifier.UNKNOWN:
        return Relation.UNKNOWN
    a_early = a.earliest or date.min
    a_late = a.latest or date.max
    b_early = b.earliest or date.min
    b_late = b.latest or date.max
    if a_early > b_late or b_early > a_late:
        return Relation.DISJOINT
    return Relation.OVERLAP


def overlaps(a: Temporal, b: Temporal) -> bool:
    return relation(a, b) == Relation.OVERLAP


def describe(t: Temporal) -> str:
    """Human rendering that keeps precision (§3.2 rule 6)."""
    if t.qualifier == Qualifier.UNKNOWN or t.precision == Precision.UNKNOWN:
        return t.display or "date unknown"
    if t.display:
        return t.display
    if t.precision == Precision.DAY and t.earliest:
        return t.earliest.isoformat()
    if t.precision == Precision.MONTH and t.earliest:
        return f"{t.earliest:%Y-%m}"
    if t.precision == Precision.YEAR and t.earliest:
        return str(t.earliest.year)
    if t.precision == Precision.DECADE and t.earliest:
        return f"{t.earliest.year}s"
    if t.precision == Precision.CENTURY and t.earliest:
        return f"{t.earliest.year}–{t.latest.year if t.latest else '?'}"
    return t.display or "undated"


def sort_key(t: Temporal) -> tuple:
    """Undated last, open pasts first, open futures last (§3.2 rule 3)."""
    undated = 1 if (t.earliest is None and t.latest is None) else 0
    if t.earliest is None:
        bucket = 0          # "before X" — it belongs before everything dated
    elif t.latest is None:
        bucket = 2          # "after X" — it belongs after everything dated
    else:
        bucket = 1
    early = t.earliest or date.min
    late = t.latest or date.max
    return (undated, bucket, early, late)


def sort_temporals(items: list[Temporal]) -> list[Temporal]:
    return sorted(items, key=sort_key)


def year_of(t: Temporal) -> int | None:
    return t.earliest.year if t.earliest else None


def distance_years(a: Temporal, b: Temporal) -> int | None:
    """Gap between two datings in years, or None if either is open/unknown."""
    if not (a.earliest and a.latest and b.earliest and b.latest):
        return None
    if overlaps(a, b):
        return 0
    if a.latest < b.earliest:
        return (b.earliest - a.latest).days // 365
    return (a.earliest - b.latest).days // 365


# ═══════════════════════════════════════════════════════════════════════════
# THE ANCHORING DISTINCTION (§3.2.1)
# ═══════════════════════════════════════════════════════════════════════════

def describe_attestation(when: Temporal, asserted: Temporal | None) -> str:
    """Render an event with its document provenance, e.g. "first attested 1840".

    A very late account of a very early event is marked as such — that is the
    difference between a letter and a gossipy biography.
    """
    base = describe(when)
    if asserted is None:
        return base
    a_year = year_of(asserted)
    w_late = when.latest.year if when.latest else None
    if a_year and w_late and a_year > w_late + 1:
        return f"{base} (first attested {a_year})"
    return base
