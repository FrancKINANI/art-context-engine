"""§3.4: the gate is the product's legal surface, and it fails closed."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src import rights
from src.models import (
    AuthorityTier,
    Determination,
    DisplayPolicy,
    RightsInfo,
    RightsStatus,
    Source,
    SourceType,
)
from src.temporal import year

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rights" / "adversarial.json"


def make_source(rights_info: RightsInfo, text: str = "x" * 300, **kwargs) -> Source:
    return Source(
        id=kwargs.pop("id", "src"),
        work_id="w",
        type=kwargs.pop("type", SourceType.SECONDARY),
        subtype="article",
        title=kwargs.pop("title", "A Source"),
        author=kwargs.pop("author", "An Author"),
        date_document=year(1822),
        text=text,
        rights=rights_info,
        authority=kwargs.pop("authority", AuthorityTier.SCHOLARLY),
    )


def test_unknown_rights_are_metadata_only():
    info = RightsInfo.unknown()
    assert info.display_policy == DisplayPolicy.METADATA_ONLY
    assert info.storage_allowed is False
    result = rights.gate(make_source(info))
    assert isinstance(result, rights.CitationOnly)


def test_span_longer_than_excerpt_degrades_to_citation():
    info = RightsInfo.public_domain("US").model_copy(
        update={"display_policy": DisplayPolicy.EXCERPT, "excerpt_limit_chars": 100}
    )
    source = make_source(info, "y" * 1000)
    assert isinstance(rights.gate(source, 0, 500), rights.CitationOnly)
    assert isinstance(rights.gate(source, 0, 50), rights.RenderedText)


def test_full_text_with_span_returns_only_the_span():
    info = RightsInfo.public_domain("US")
    source = make_source(info, "0123456789" * 10)
    result = rights.gate(source, 5, 10)
    assert isinstance(result, rights.RenderedText)
    assert result.text == "56789"


def test_link_only_never_returns_text_even_when_stored():
    info = RightsInfo.link_only()
    source = make_source(info, "lyrics we should not show")
    result = rights.gate(source, 0, 10)
    assert isinstance(result, rights.CitationOnly)
    assert result.url is None or "text" not in result.title


def test_export_is_stricter_than_display():
    info = RightsInfo.quote_span(limit=200, jurisdiction="US").model_copy(
        update={"redistribution_allowed": False}
    )
    source = make_source(info, "z" * 600)
    assert isinstance(rights.gate(source, 0, 100), rights.RenderedText)
    assert isinstance(rights.gate(source, 0, 100, for_export=True), rights.CitationOnly)


def test_licences_do_not_vary_by_reader_jurisdiction():
    """CC BY 4.0 says the same thing in Paris as in Boston."""
    source = make_source(RightsInfo.cc_by("4.0", jurisdiction="US"))
    assert isinstance(rights.gate(source, 0, 50), rights.RenderedText)
    assert isinstance(
        rights.gate(source, 0, 50, reader_jurisdiction="FR"), rights.RenderedText
    )


def test_public_domain_is_downgraded_for_a_stricter_reader():
    """PD-US is a claim about the US. A French reader gets a citation."""
    source = make_source(RightsInfo.public_domain("US"))
    assert isinstance(rights.gate(source, 0, 50), rights.RenderedText)
    result = rights.gate(source, 0, 50, reader_jurisdiction="FR")
    assert isinstance(result, rights.CitationOnly)
    assert result.reason == "jurisdiction"


def test_attribution_line_does_not_repeat_the_licence():
    info = RightsInfo.cc_by_sa("4.0")
    line = rights.attribution_line(make_source(info))
    assert line is not None
    assert line.lower().count("cc") >= 1
    assert "[CC-BY-SA-4.0]" not in line  # "CC BY-SA 4.0" already says it


def test_heuristic_determination_older_than_a_year_is_rechecked():
    old = RightsInfo.unknown().model_copy(
        update={"determined_at": date(2000, 1, 1), "determination": Determination.HEURISTIC}
    )
    assert rights.needs_recheck(old) is True
    fresh = old.model_copy(update={"determined_at": date.today()})
    assert rights.needs_recheck(fresh) is False


# ── adversarial fixtures (§10.5) ──────────────────────────────────────────

@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text())["cases"],
                         ids=lambda c: c["id"])
def test_adversarial_rights_fixtures(case):
    spec = case["rights"]
    info = RightsInfo(
        status=RightsStatus(spec["status"]),
        jurisdiction=spec["jurisdiction"],
        determination=Determination(spec["determination"]),
        display_policy=DisplayPolicy(spec["display_policy"]),
        excerpt_limit_chars=spec.get("excerpt_limit_chars"),
        storage_allowed=spec["storage_allowed"],
        redistribution_allowed=spec["redistribution_allowed"],
    )
    source = make_source(info, "q" * case["text_chars"])
    span = case.get("span")
    result = rights.gate(
        source,
        span[0] if span else None,
        span[1] if span else None,
        for_export=case.get("for_export", False),
        reader_jurisdiction=case.get("reader_jurisdiction"),
    )
    assert type(result).__name__ == case["expect"], case["why"]
    if isinstance(result, rights.CitationOnly):
        assert result.title and result.author
