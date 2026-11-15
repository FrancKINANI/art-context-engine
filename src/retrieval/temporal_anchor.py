"""Timeline anchoring (§3.2.1, §4.3).

The rule this module enforces, and the reason it exists at all:

    A letter from 1822 is evidence about 1822. A biography from 1840 describing
    1822 is evidence about 1822 **as told in 1840**.

So an event carries two dates — `when` (the event) and `date_asserted_by_document`
(the evidence) — and both survive into the timeline. Collapsing them is how a
history engine ends up claiming the premiere happened in 1935 because that is
when someone first wrote it down.

Events are merged across sources by (kind, dating overlap, label). Each merged
event keeps every attestation, so "first attested 1840" is a property of the
evidence, not of the event.
"""

from __future__ import annotations

from src.models import (
    Attestation,
    ExtractedClaim,
    Source,
    TimelineEvent,
    Work,
)
from src.temporal import describe, overlaps

#: Which predicates become timeline events, and under what label.
_PREDICATE_EVENTS = {
    "composition_date": ("composed", "composition"),
    "publication_date": ("published", "publication"),
    "premiere_date": ("first performed", "premiere"),
}


def build_timeline(
    work: Work,
    claims: list[ExtractedClaim],
    sources: dict[str, Source],
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []

    # 1. The creator's life, from the curated registry (dates we assert).
    for creator in work.creators:
        if creator.birth_date:
            events.append(TimelineEvent(
                when=creator.birth_date,
                title=f"{creator.name} born",
                description=f"Born {describe(creator.birth_date)}.",
                event_type="life",
                work_id=work.id,
            ))
        if creator.death_date:
            events.append(TimelineEvent(
                when=creator.death_date,
                title=f"{creator.name} dies",
                description=f"Died {describe(creator.death_date)}.",
                event_type="life",
                work_id=work.id,
            ))

    # 2. The work's own milestones, from extracted claims.
    #    Only claims confident enough to survive the conflict threshold are
    #    allowed to place an event on the timeline: a maybe-date is not a date.
    for claim in claims:
        mapping = _PREDICATE_EVENTS.get(claim.predicate)
        if not mapping or claim.about is None:
            continue
        if not claim.is_usable(0.80):
            continue
        label, event_type = mapping
        source = sources.get(claim.source_id)
        asserted = source.date_document if source else None
        attestation = Attestation(
            source_id=claim.source_id,
            span=claim.span,
            asserted=asserted,
        )
        existing = _find_event(events, event_type, claim.about)
        if existing:
            existing.attestations.append(attestation)
            existing.when = _merge_envelope([existing.when, claim.about])
            continue
        events.append(TimelineEvent(
            when=claim.about,
            date_asserted_by_document=asserted,
            title=f"Work {label}",
            description=_description(label, claim, source),
            event_type=event_type,
            attestations=[attestation],
            location=work.location_created,
            work_id=work.id,
        ))

    return sort_events(events)


def _find_event(events: list[TimelineEvent], event_type: str, when) -> TimelineEvent | None:
    for event in events:
        if event.event_type != event_type:
            continue
        if overlaps(event.when, when):
            return event
    return None


def _merge_envelope(datings):
    """Widen an event's dating rather than picking one source's version."""
    from src.temporal import disputed

    earliest = min((d.earliest for d in datings if d.earliest), default=None)
    latest = max((d.latest for d in datings if d.latest), default=None)
    if earliest and latest:
        display = (
            str(earliest.year) if earliest.year == latest.year
            else f"{earliest.year}–{latest.year}"
        )
    else:
        display = " / ".join(d for d in (x.display for x in datings) if d)
    return disputed(datings, display=display)


def _description(label: str, claim: ExtractedClaim, source: Source | None) -> str:
    if source is None:
        return ""
    # §3.2.1: the event date (rendered by the timeline) and the date of the
    # evidence are shown together, because "first attested 1840" is a property
    # of the source, not of the event.
    asserted = source.date_document.display if source.date_document else "undated"
    return f"attested by {source.author} ({asserted})."


def sort_events(events: list[TimelineEvent]) -> list[TimelineEvent]:
    return sorted(events, key=lambda e: e.sort_key)


def undated(events: list[TimelineEvent]) -> list[TimelineEvent]:
    """The separate *undated* bucket (§3.2 rule 4) — never placed by guess."""
    return [e for e in events if e.is_undated]
