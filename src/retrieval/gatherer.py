"""Retrieval orchestration: work + question → ContextPack (§3.5, §4.2).

This is the only place adapters are called, so it is the only place that has to
be careful about three things:

* **Rights are attached at ingest.** A source without a `RightsInfo` never
  enters the pack. The adapters build it; the gatherer refuses to accept a
  source that skipped it.
* **Claims are extracted after ranking, not before.** Extraction is the
  expensive step, and the ranker's job is partly to decide what is worth reading
  closely.
* **Gaps are computed, not discovered later.** "No source records a premiere" is
  a finding. The pack should know it before the assembler asks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models import (
    ContextPack,
    ExtractedClaim,
    Gap,
    QuestionType,
    Source,
    Work,
)
from src.retrieval.adapters.registry import build_adapters
from src.retrieval.claim_extractor import extract_pack
from src.retrieval.conflict_detector import detect
from src.retrieval.http import Backend, get_backend
from src.retrieval.ranker import RankedSource, rank
from src.retrieval.temporal_anchor import build_timeline

#: Predicates a work of each type is expected to have evidence for. Missing ones
#: become gaps (§3.7 rule 5), not silent omissions.
_EXPECTED = {
    "MUSIC": ["composition_date", "publication_date", "premiere_date"],
    "POETRY": ["composition_date", "publication_date"],
}

#: An album has a recording period and a release date, not a premiere. Asking for
#: a premiere date would manufacture a gap that is really a category error.
_BY_FORM = [
    (("album", "record", "single", "mixtape"), ["recording_date", "publication_date"]),
    (("sonata", "symphony", "quartet", "concerto", "opera"),
     ["composition_date", "publication_date", "premiere_date"]),
    (("poem",), ["composition_date", "publication_date"]),
]


def expected_predicates(work: Work) -> list[str]:
    form = (work.metadata.get("form") or "").lower()
    for markers, predicates in _BY_FORM:
        if any(marker in form for marker in markers):
            return predicates
    return _EXPECTED.get(work.type.value, [])


@dataclass
class GatherResult:
    pack: ContextPack
    ranked: list[RankedSource] = field(default_factory=list)
    sources: dict[str, Source] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def gather(
    work: Work,
    question: str | None = None,
    question_type: QuestionType | None = None,
    *,
    backend: Backend | None = None,
    max_per_adapter: int = 2,
    with_claims: bool = True,
) -> GatherResult:
    backend = backend or get_backend()
    adapters = build_adapters(backend, work.type)
    errors: list[str] = []

    query = _retrieval_query(work)
    open_work = not _pins_an_article(work)
    sources: dict[str, Source] = {}
    for adapter in adapters:
        try:
            hits = adapter.search(query, work, limit=max_per_adapter)
        except Exception as exc:  # an adapter must never break the pack
            errors.append(f"{adapter.name}: search failed: {type(exc).__name__}: {exc}")
            continue
        for hit in hits[:max_per_adapter]:
            try:
                source = adapter.fetch(hit, work)
            except Exception as exc:
                errors.append(f"{adapter.name}: fetch failed: {type(exc).__name__}: {exc}")
                continue
            if source is None:
                continue
            if not _has_rights(source):
                errors.append(f"{adapter.name}: source {source.id} had no rights determination")
                continue
            if open_work and not _mentions(source, work):
                errors.append(
                    f"{adapter.name}: dropped {source.id}; never names the work or its maker"
                )
                continue
            sources[source.id] = source
        errors.extend(adapter.errors)

    claims: list[ExtractedClaim] = []
    ranked: list[RankedSource] = []
    if with_claims:
        claims = extract_pack(list(sources.values()), work)

    claims_by_source: dict[str, int] = {}
    for claim in claims:
        claims_by_source[claim.source_id] = claims_by_source.get(claim.source_id, 0) + 1
    ranked = rank(list(sources.values()), work, question, claims_by_source)

    # Extraction follows ranking: read the best evidence first, and let a
    # source's claim count feed back into its relevance.
    conflicts, candidates = detect(claims, sources)
    timeline = build_timeline(work, claims, sources)
    gaps = compute_gaps(work, sources, claims, timeline)

    pack = ContextPack(
        work_id=work.id,
        timeline=timeline,
        sources=[r.source for r in ranked],
        claims=claims,
        conflicts=conflicts,
        conflict_candidates=candidates,
        gaps=gaps,
        question=question,
        question_type=question_type,
    )
    return GatherResult(pack=pack, ranked=ranked, sources=sources, errors=errors)


def _pins_an_article(work: Work) -> bool:
    """Does the work say *which page* is about it, not merely which work it is?

    A QID or a Deezer id identifies the work; it does not say that the article
    just fetched is about it. Only these keys do, and the difference matters:
    with a pinned article the sources are known to be on topic, so the mention
    gate is unnecessary; without one it is the only thing standing between the
    reader and a nearby article's facts.
    """
    return any(
        work.identifiers.get(key)
        for key in ("wikipedia_title", "wikipedia_sitelink", "genius_url", "imslp_page", "imslp_id")
    )


def _retrieval_query(work: Work) -> str:
    """What to type into a catalogue: a name, never the reader's question.

    Search boxes match names, not questions. "Booba, Paname — what is he saying
    about Paris?" finds Paris Saint-Germain supporters; "Paname Booba" finds the
    artist's discography, which is the thing that can actually date the track.

    The question used to be sent whenever the work had a pinned article, on the
    theory that the page was already known so the search did not matter. It did:
    it was the query handed to every other adapter too, so the scholarly index
    was asked to find a paper titled "what does the painting mean".
    """
    maker = work.primary_creator.name if work.primary_creator else ""
    return " ".join(part for part in (work.title, maker) if part)


def _mentions(source: Source, work: Work) -> bool:
    """Does the source ever say the work's name, or its maker's?

    A work resolved by search has no identifier to pin its evidence, so a source
    that never names it is a search accident — the Wikipedia page for the city
    slang, not the song. Cheap, and it only ever drops evidence that could not
    have supported a claim about this work anyway.
    """
    from src.retrieval.entity_resolver import normalize

    haystack = normalize(f"{source.title} {source.text[:4000]}")
    names = [work.title] + [c.name for c in work.creators]
    tokens = {
        token
        for name in names
        for token in normalize(name).split()
        if len(token) > 2
    }
    return any(token in haystack for token in tokens)


def _has_rights(source: Source) -> bool:
    """A source without a determination is a source we are not allowed to use."""
    return source.rights is not None and source.rights.determined_at is not None


def compute_gaps(
    work: Work,
    sources: dict[str, Source],
    claims: list[ExtractedClaim],
    timeline: list,
) -> list[Gap]:
    gaps: list[Gap] = []
    expected = expected_predicates(work)
    present = {c.predicate for c in claims if c.is_usable(0.0)}

    for predicate in expected:
        if predicate not in present:
            gaps.append(Gap(
                work_id=work.id,
                description=f"No source in this corpus records the {predicate.replace('_', ' ')}.",
                gap_type="no_sources",
                related_subject=predicate,
                severity="MATERIAL",
            ))

    quotable_primary = [
        s for s in sources.values()
        if s.type.value == "PRIMARY" and s.text
    ]
    if not quotable_primary:
        gaps.append(Gap(
            work_id=work.id,
            description=(
                "No primary document can be quoted: the surviving primary sources are "
                "link-only under their licences, or were not retrievable."
            ),
            gap_type="rights_restricted",
            severity="MATERIAL",
        ))

    if len(sources) < 3:
        gaps.append(Gap(
            work_id=work.id,
            description=f"Only {len(sources)} independent sources were found for this work.",
            gap_type="thin_corpus",
            severity="MATERIAL",
        ))

    undated_events = [e for e in timeline if e.is_undated]
    if undated_events:
        gaps.append(Gap(
            work_id=work.id,
            description=(
                f"{len(undated_events)} timeline event(s) have no recorded date and are "
                "shown undated rather than placed by guess."
            ),
            gap_type="undated",
            severity="MINOR",
        ))

    # The world-context layer (wars, laws, institutions) is not connected in the
    # MVP corpus. Saying so is better than implying the silence means nothing
    # happened.
    gaps.append(Gap(
        work_id=work.id,
        description=(
            "The world-context layer (contemporary events outside the work) is not "
            "retrieved yet; this pack contains the work, its creator and its sources only."
        ),
        gap_type="missing_layer",
        severity="MINOR",
    ))
    return gaps


def independent_source_count(pack: ContextPack) -> int:
    """Different documents, not different mirrors of one (§10.6)."""
    seen_mirrors: set[tuple[str, str]] = set()
    for source in pack.sources:
        key = (source.title.lower().strip(), source.author.lower().strip())
        seen_mirrors.add(key)
    return len(seen_mirrors)
