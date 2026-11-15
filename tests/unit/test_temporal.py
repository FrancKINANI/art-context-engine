"""§3.2: a date without precision is a date that will be rendered as a lie."""

from __future__ import annotations

from datetime import date

import pytest

from src import temporal as t
from src.models import Precision, Qualifier


def test_year_is_an_interval_not_a_point():
    y = t.year(1821)
    assert y.earliest == date(1821, 1, 1)
    assert y.latest == date(1821, 12, 31)
    assert y.precision == Precision.YEAR


def test_day_precision_requires_identical_bounds():
    d = t.day(1822, 1, 15)
    assert d.earliest == d.latest == date(1822, 1, 15)
    with pytest.raises(ValueError):
        t.Temporal(earliest=date(1822, 1, 1), latest=date(1822, 1, 2),
                   precision=Precision.DAY)


def test_circa_widens_by_declared_window():
    y = t.circa(t.year(1821))
    assert y.qualifier == Qualifier.CIRCA
    assert (y.latest - y.earliest).days > 365 * 3   # ±2 years plus the year itself
    assert y.display == "c. 1821"


def test_open_bounds_are_legal_and_sort_last():
    before = t.before(t.year(1820))
    after = t.after(t.year(1820))
    assert before.earliest is None
    assert after.latest is None
    ordered = t.sort_temporals([after, t.year(1850), before, t.unknown()])
    assert ordered[0] is before          # open past first
    assert ordered[-1].qualifier == Qualifier.UNKNOWN   # undated last


def test_unknown_is_not_a_fallback_date():
    u = t.unknown("premiere documented, date not recorded")
    assert u.earliest is None and u.latest is None
    assert t.describe(u) == "premiere documented, date not recorded"


def test_disjoint_years_conflict_but_overlapping_ranges_do_not():
    assert t.relation(t.year(1821), t.from_range(1821, 1822)) == t.Relation.OVERLAP
    assert t.relation(t.year(1820), t.year(1823)) == t.Relation.DISJOINT


def test_two_unknowns_are_not_disjoint():
    """Absence of evidence twice is not evidence of a disagreement."""
    assert t.relation(t.unknown(), t.unknown()) == t.Relation.UNKNOWN


def test_disputed_keeps_the_envelope_and_never_a_midpoint():
    d = t.disputed([t.year(1820), t.year(1824)])
    assert d.qualifier == Qualifier.DISPUTED
    assert d.earliest.year == 1820 and d.latest.year == 1824
    assert d.precision in (Precision.YEAR, Precision.DECADE)


def test_wikidata_precision_code_decides_width():
    year_only = t.parse_wikidata_time("+1822-00-00T00:00:00Z", 9)
    day_level = t.parse_wikidata_time("+1822-03-11T00:00:00Z", 11)
    assert year_only.precision == Precision.YEAR
    assert day_level.precision == Precision.DAY


def test_parse_human_recognises_ranges_and_circa():
    assert t.parse_human("written 1821–1822").earliest == date(1821, 1, 1)
    circa = t.parse_human("composed c. 1821")
    assert circa.qualifier == Qualifier.CIRCA


def test_attestation_distinguishes_event_date_from_document_date():
    text = t.describe_attestation(t.year(1822), t.year(1840))
    assert "1822" in text and "first attested 1840" in text


def test_precision_is_preserved_in_display():
    assert t.describe(t.decade(1820)) == "1820s"
    assert t.describe(t.circa(t.year(1821))) == "c. 1821"
