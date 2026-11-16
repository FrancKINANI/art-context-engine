"""Source → ExtractedClaim[] with spans (§4.4).

Conflicts cannot be detected over prose, so this module turns prose into typed
claims. The MVP extractor is **rule-based, not LLM-based**, for three reasons:

* it is deterministic, so the conflict fixtures have a stable input;
* its failures are inspectable (a rule that fires wrongly is a line of regex,
  not a temperature);
* it gives the LLM extractor a baseline it must beat, exactly like §4.7's
  assembler.

Every claim carries the **sentence** it came from as its span. That is the unit
a reader can check: enough context to see whether we read the source correctly,
small enough to fit the excerpt policy. A claim whose span does not match the
stored text is dropped, never repaired (§4.4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from src.config import SEED_DIR
from src.models import (
    ExtractedClaim,
    Polarity,
    Source,
    Span,
    Temporal,
    Work,
)
from src.registry import get_work
from src.temporal import day, month, parse_human, year

CONFIDENCE_EXPLICIT = 0.92   # "written between 1821 and 1822"
CONFIDENCE_IMPLIED = 0.78    # a looser phrasing; excluded from conflicts by §4.5.2
MIN_STORED_TEXT = 80         # shorter than this is furniture, not a document


@lru_cache(maxsize=1)
def vocabulary() -> dict:
    path = SEED_DIR / "vocabulary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"predicates": {}}


def predicate_spec(predicate: str) -> dict:
    return vocabulary().get("predicates", {}).get(predicate, {})


# ═══════════════════════════════════════════════════════════════════════════
# SENTENCES WITH OFFSETS
# ═══════════════════════════════════════════════════════════════════════════

#: A sentence ends at terminal punctuation followed by whitespace and a capital,
#: *or* by a capital with no space at all — French and Dutch abstracts routinely
#: arrive as "…cette étude.En effet…", and splitting only on whitespace turns the
#: whole abstract into one 2,700-character "sentence".
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])(?:\s+(?=[A-Z0-9“\"'])|(?=[A-ZÀ-Þ«“\"]))|\n{2,}"
)


@dataclass
class Sentence:
    text: str
    start: int

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def sentences(text: str) -> list[Sentence]:
    """Split into sentences while keeping exact character offsets."""
    out: list[Sentence] = []
    cursor = 0
    for match in _SENTENCE_SPLIT.finditer(text):
        chunk = text[cursor:match.start()]
        if chunk.strip():
            out.append(Sentence(chunk.strip(), cursor + (len(chunk) - len(chunk.lstrip()))))
        cursor = match.end()
    tail = text[cursor:]
    if tail.strip():
        out.append(Sentence(tail.strip(), cursor + (len(tail) - len(tail.lstrip()))))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# RULES
# ═══════════════════════════════════════════════════════════════════════════

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "hundred": 100, "a": 1,
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _int(value: str) -> int | None:
    value = value.strip().lower().replace(",", "")
    if value in _NUMBER_WORDS:
        return _NUMBER_WORDS[value]
    if value.isdigit():
        return int(value)
    # "one-hundred", "a hundred", "twenty-five"
    parts = re.split(r"[-\s]+", value)
    if len(parts) > 1 and parts[-1] in _NUMBER_WORDS:
        total = _NUMBER_WORDS[parts[-1]]
        head = parts[0]
        factor = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                  "five": 5, "twenty": 20}.get(head)
        if factor is not None:
            return factor * total if total >= 100 else factor + total
    return None


def _temporal_value(spec: dict) -> dict:
    return {
        "display": spec.get("display", ""),
        "earliest": spec.get("earliest"),
        "latest": spec.get("latest"),
        "precision": spec.get("precision"),
        "qualifier": spec.get("qualifier"),
    }


def _as_temporal_value(t: Temporal) -> dict:
    return _temporal_value(t.model_dump(mode="json"))


@dataclass
class Rule:
    predicate: str
    pattern: re.Pattern
    build: Callable[[re.Match], dict | None]
    subject_suffix: str = ""      # "" → work-level subject
    confidence: float = CONFIDENCE_EXPLICIT
    applies_to: tuple[str, ...] = ()   # authority tiers, empty = all
    label: str = ""
    value_group: str | None = None     # which group is the quoted value
    whole_sentence: bool = False       # readings are the sentence, not the match


def _range_value(m: re.Match) -> dict | None:
    a, b = int(m.group("a")), int(m.group("b"))
    if not (1000 < a < 2100 and 1000 < b < 2100):
        return None
    from src.temporal import from_range

    return _as_temporal_value(from_range(a, b, display=m.group(0)))


def _single_year_value(m: re.Match) -> dict | None:
    y = int(m.group("a"))
    if not (1000 < y < 2100):
        return None
    return _as_temporal_value(year(y, display=str(y)))


def _full_date_value(m: re.Match) -> dict | None:
    month_no = _MONTHS.get(m.group("month").lower())
    if not month_no:
        return None
    d = day(int(m.group("year")), month_no, int(m.group("day")),
            display=f"{m.group('month')} {m.group('day')}, {m.group('year')}")
    return _as_temporal_value(d)


def _month_year_value(m: re.Match) -> dict | None:
    month_no = _MONTHS.get(m.group("month").lower())
    if not month_no:
        return None
    return _as_temporal_value(month(int(m.group("year")), month_no,
                                    display=m.group(0).strip()))


def _count_value(m: re.Match) -> dict | None:
    n = _int(m.group("n"))
    return {"count": n} if n is not None else None


def _tempo_value(m: re.Match) -> dict | None:
    bpm = int(m.group("bpm"))
    if not (20 <= bpm <= 300):
        return None
    return {"bpm": bpm, "beat_unit": "quarter"}


def _text_value(key: str) -> Callable[[re.Match], dict | None]:
    def build(m: re.Match) -> dict | None:
        raw = m.group(key).strip(" ,.;:\"'")
        return {"text": raw} if raw else None

    return build


def _key_value(m: re.Match) -> dict | None:
    raw = m.group("key").strip()
    raw = raw.replace("#", "♯").replace("b ", "♭ ")
    return {"text": raw}


def _sentence_value(m: re.Match) -> dict | None:
    return {"text": m.group(0).strip()}   # replaced with the full sentence later


def _dedication_value(m: re.Match) -> dict | None:
    """'his friend, pupil, and patron, Archduke Rudolf' → 'Archduke Rudolf'.

    The appositive list is the author's courtesy, not a second dedicatee; the
    name is the last comma-separated segment.
    """
    raw = m.group("who").strip(" ,.;")
    if "," in raw:
        raw = raw.rsplit(",", 1)[-1].strip()
    raw = raw.lstrip("and ").strip()
    return {"text": raw} if raw else None


RULES: list[Rule] = [
    # ── composition date ────────────────────────────────────────────────
    Rule("composition_date",
         re.compile(r"written between (?P<a>\d{4}) and (?P<b>\d{4})", re.I),
         _range_value, label="written between"),
    Rule("composition_date",
         # 'conceived', 'outlined' and 'begun' are deliberately excluded: they
         # describe the plan, not the composition, and reading them as the
         # composition date is exactly the false conflict §4.5.4 warns about.
         re.compile(r"(?:composed|written)[^.]{0,60}?\b(?:in|by|during)\s+(?:the\s+\w+\s+of\s+)?(?P<a>\d{4})", re.I),
         _single_year_value, confidence=0.85, label="composed in"),
    Rule("recording_date",
         re.compile(r"recorded (?:between )?(?P<a>\d{4})\s*(?:and|–|-|to)\s*(?P<b>\d{4})", re.I),
         _range_value, label="recorded between"),
    Rule("recording_date",
         re.compile(r"recorded (?:in|during|between)\s+(?:the\s+\w+\s+of\s+)?(?P<a>\d{4})", re.I),
         _single_year_value, confidence=0.85, label="recorded in"),
    Rule("composition_date",
         re.compile(r"(?:completed|finished)\b[^.]{0,40}?\b(?P<a>\d{4})", re.I),
         _single_year_value, confidence=CONFIDENCE_IMPLIED, label="completed"),

    # ── publication / release ───────────────────────────────────────────
    Rule("publication_date",
         re.compile(r"released on (?P<month>[A-Z][a-z]+) (?P<day>\d{1,2}), (?P<year>\d{4})"),
         _full_date_value, label="released on"),
    Rule("publication_date",
         re.compile(r"published (?:in|on) (?P<month>[A-Z][a-z]+) (?P<year>\d{4})"),
         _month_year_value, label="published month year"),
    Rule("publication_date",
         re.compile(r"(?:published|released)\s+(?:in\s+)?(?P<a>\d{4})", re.I),
         _single_year_value, confidence=CONFIDENCE_IMPLIED, label="published in"),

    # ── premiere ────────────────────────────────────────────────────────
    Rule("premiere_date",
         re.compile(r"first (?:performed|performance|heard)\b[^.]{0,80}?\b(?P<a>\d{4})", re.I),
         _single_year_value, label="first performed"),
    Rule("premiere_date",
         re.compile(r"\bpremiere[ds]?\b[^.]{0,80}?\b(?P<a>\d{4})", re.I),
         _single_year_value, confidence=CONFIDENCE_IMPLIED, label="premiere"),

    # ── dedication ──────────────────────────────────────────────────────
    Rule("dedication",
         re.compile(r"dedicated to (?:his |her |the )?(?P<who>[A-Z][^.;]{2,90})"),
         _dedication_value, label="dedicated to"),

    # ── form / counts ───────────────────────────────────────────────────
    Rule("movement_count",
         re.compile(r"\b(?P<n>one|two|three|four|five|six|seven|\d+)\s+(?:[a-z]+\s+)?movements\b", re.I),
         _count_value, label="movements"),
    Rule("variation_count",
         re.compile(r"\b(?P<n>one|two|three|four|five|six|seven|eight|\d+)\s+variations\b", re.I),
         _count_value, label="variations"),
    Rule("metronome_mark",
         re.compile(r"(?:♩|quarter[- ]note)\s*=\s*(?P<bpm>\d{2,3})"),
         _tempo_value, subject_suffix="mov2.metronome", label="metronome"),
    Rule("metronome_mark",
         re.compile(r"metronome[^.]{0,40}?(?P<bpm>\d{2,3})\s*(?:per minute|to the quarter)?", re.I),
         _tempo_value, subject_suffix="mov2.metronome", label="metronome worded"),

    # ── key / form ──────────────────────────────────────────────────────
    Rule("key",
         re.compile(r"(?:sonata|work|piece|poem)\b[^.]{0,50}?\bin (?P<key>[A-G](?:♯|♭|#|b)? (?:major|minor))", re.I),
         _key_value, label="in key", value_group="key"),
    Rule("genre",
         re.compile(r"(?:primarily|largely)\s+an?\s+(?P<g>[a-z][a-z\- ]{2,30}?)\s+album", re.I),
         _text_value("g"), value_group="g", label="primarily a"),
    Rule("line_count",
         re.compile(r"\b(?P<n>[a-z\-]+|\d{1,4})\s+lines\b", re.I),
         _count_value, value_group="n", label="lines"),
    Rule("stanza_count",
         re.compile(r"\b(?P<n>[a-z\-]+|\d{1,4})\s+quatrains\b", re.I),
         _count_value, value_group="n", label="quatrains"),
    Rule("sales_copies",
         re.compile(r"sold (?:over |more than )?(?P<n>[\d,]+) copies", re.I),
         _count_value, value_group="n", label="sold copies"),
    Rule("subject_matter",
         re.compile(r"\bdescribes (?P<what>[^.]{12,140})", re.I),
         _text_value("what"), value_group="what", label="describes",
         confidence=0.85),
    Rule("stylistic_elements",
         re.compile(r"\bincorporates (?P<what>[^.;]{5,160})", re.I),
         _text_value("what"), value_group="what", label="incorporates",
         confidence=0.85),
]

#: Reading claims: not facts, so they become INTERPRETIVE (§4.5.1) and are
#: shown as "readings differ", never as an error.
READING_RULES: list[Rule] = [
    Rule("reception",
         re.compile(r"(?:ignored by contemporaries|entered the repertoire|critical reception|was received|acclaim|divided critics|topped .{0,40}poll|ranked (?:it )?(?:as )?(?:the )?best album|named .{0,40}best album)", re.I),
         _sentence_value,
         applies_to=("REFERENCE", "SCHOLARLY", "CONTEMPORARY_REVIEW"),
         label="reception", whole_sentence=True),
    Rule("interpretation",
         re.compile(r"(?:is (?:often )?(?:considered|regarded|read as)|critics (?:have )?(?:argued|suggested)|has been (?:read|interpreted) as|is seen as)", re.I),
         _sentence_value,
         applies_to=("SCHOLARLY", "CONTEMPORARY_REVIEW"),
         label="interpretation", whole_sentence=True),
]


# ═══════════════════════════════════════════════════════════════════════════
# EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

def extract(source: Source, work: Work) -> list[ExtractedClaim]:
    """Extract claims from one source. Returns [] for unreadable sources."""
    if not source.text or len(source.text) < MIN_STORED_TEXT:
        return []
    if not source.rights.storage_allowed:
        return []

    claims: list[ExtractedClaim] = []
    if source.subtype == "structured_data":
        claims.extend(_extract_structured(source, work))
    else:
        claims.extend(_extract_prose(source, work))
        if not claims and source.authority.value == "SCHOLARLY":
            fallback = _scholarly_fallback(source, work)
            if fallback is not None:
                claims.append(fallback)
    return claims


def _scholarly_fallback(source: Source, work: Work) -> ExtractedClaim | None:
    """A scholarly source always says *something*, even if none of our rules match.

    Its opening sentence is the paper's own claim, quoted verbatim. Recording it
    as an INTERPRETIVE claim is what lets §10.6's "grounded in criticism" test
    pass honestly: we are citing what the critic argues, not what we inferred.
    """
    candidates = [s for s in sentences(source.text) if len(s.text) > 60]
    # A sentence that names the *work* is evidence about it; one that only names
    # the maker is circumstantial ("this study looks at 47 tracks, including
    # Booba's"). Prefer the first, keep the second only as a last resort.
    first = next(
        (s for s in candidates if work.title and work.title.casefold() in s.text.casefold()),
        None,
    ) or next((s for s in candidates if _names_work(s.text, work)), None)
    if first is None:
        return None
    return ExtractedClaim(
        work_id=work.id,
        source_id=source.id,
        span=Span(start=first.start, end=first.end,
                  text=source.text[first.start:first.end]),
        subject=_subject(work, ""),
        predicate="interpretation",
        value={"text": first.text.strip()},
        value_text=first.text.strip(),
        qualifiers={"rule": "scholarly-opening-sentence"},
        extractor_confidence=0.8,
    )


def _names_work(text: str, work: Work) -> bool:
    """Does this sentence actually name the work, or the person who made it?

    A paper that mentions a track once in its bibliography is not criticism of
    that track. Without this, the fallback rule would quote a paper about the
    word "peuple" as an interpretation of a Booba song, which is exactly the
    confidently-wrong failure the resolver exists to prevent.
    """
    haystack = text.casefold()
    if work.title and work.title.casefold() in haystack:
        return True
    return any(creator.name and creator.name.casefold() in haystack
               for creator in work.creators)


def _subject(work: Work, suffix: str) -> str:
    return f"{work.id}.{suffix}" if suffix else work.id


def _extract_prose(source: Source, work: Work) -> list[ExtractedClaim]:
    out: list[ExtractedClaim] = []
    seen: set[tuple[str, str]] = set()

    for sentence in sentences(source.text):
        for rule in RULES + READING_RULES:
            if rule.applies_to and source.authority.value not in rule.applies_to:
                continue
            for match in rule.pattern.finditer(sentence.text):
                value = rule.build(match)
                if value is None:
                    continue
                span = Span(
                    start=sentence.start,
                    end=sentence.end,
                    text=source.text[sentence.start:sentence.end],
                )
                about = None
                if "earliest" in value or "precision" in value:
                    about = _temporal_from_value(value)
                subject = _subject(work, rule.subject_suffix)
                key = (subject, rule.predicate)
                if key in seen and predicate_spec(rule.predicate).get("value_type") != "temporal":
                    continue
                if rule.whole_sentence:
                    value = {"text": sentence.text.strip()}
                    value_text = sentence.text.strip()
                else:
                    value_text = (
                        match.group(rule.value_group).strip()
                        if rule.value_group and match.groupdict().get(rule.value_group)
                        else match.group(0).strip()
                    )
                claim = ExtractedClaim(
                    work_id=work.id,
                    source_id=source.id,
                    span=span,
                    subject=subject,
                    predicate=rule.predicate,
                    value=value,
                    value_text=value_text,
                    about=about,
                    qualifiers={"rule": rule.label},
                    extractor_confidence=rule.confidence,
                )
                out.append(claim)
                seen.add(key)
    return out


def _extract_structured(source: Source, work: Work) -> list[ExtractedClaim]:
    """Wikidata statements: the serialized line is the span."""
    out: list[ExtractedClaim] = []
    property_map = {
        "P571": "composition_date",
        "P577": "publication_date",
        "P1191": "premiere_date",
    }
    for statement in source.metadata.get("statements", []):
        predicate = property_map.get(statement.get("property", ""))
        if not predicate:
            continue
        line = statement["line"]
        start = source.text.find(line)
        if start < 0:
            continue
        temporal = parse_human(statement.get("value_text", ""))
        if temporal.earliest is None and statement.get("time_raw"):
            from src.temporal import parse_wikidata_time

            temporal = parse_wikidata_time(statement["time_raw"], statement.get("precision"))
        out.append(ExtractedClaim(
            work_id=work.id,
            source_id=source.id,
            span=Span(start=start, end=start + len(line), text=line),
            subject=_subject(work, ""),
            predicate=predicate,
            value=_as_temporal_value(temporal),
            value_text=statement.get("value_text", ""),
            about=temporal,
            qualifiers={"dataset": "wikidata", "property": statement.get("property", "")},
            extractor_confidence=0.9,
            extractor="wikidata-v1",
        ))
    return out


def _temporal_from_value(value: dict) -> Temporal | None:
    from datetime import date as date_cls

    from src.models import Precision, Qualifier

    try:
        earliest = date_cls.fromisoformat(value["earliest"]) if value.get("earliest") else None
        latest = date_cls.fromisoformat(value["latest"]) if value.get("latest") else None
        return Temporal(
            earliest=earliest,
            latest=latest,
            precision=Precision(value.get("precision") or "UNKNOWN"),
            qualifier=Qualifier(value.get("qualifier") or "UNKNOWN"),
            display=value.get("display", ""),
        )
    except (ValueError, KeyError):
        return None


#: Keys that pin a work to a specific article, score page or catalogue entry.
_IDENTIFIERS = ("wikipedia_title", "wikidata_qid", "genius_url", "imslp_id")


def extract_pack(sources: list[Source], work: Work) -> list[ExtractedClaim]:
    claims: list[ExtractedClaim] = []
    for source in sources:
        claims.extend(extract(source, work))
    if any(work.identifiers.get(key) for key in _IDENTIFIERS):
        return claims
    # A work resolved by search has no identifier tying a sentence to it. The
    # article about La Fouine mentions Booba once, in passing, and its album
    # sale figures are not evidence about a Booba track. For an open work, a
    # sentence that never names the work or its maker is not about the work.
    return [c for c in claims if _names_work(c.span.text, work)]
