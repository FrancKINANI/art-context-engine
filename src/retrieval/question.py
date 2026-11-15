"""Question interpretation (§4.0).

The user asks a question, not a keyword string, and the question decides what
gets retrieved and what the answer may leave out. Two outputs matter:

* **QuestionType** — a MEANING question leads with the work's own material; a
  HISTORY question leads with the timeline and reception.
* **Aspect** — the part of the work the question is about. "Why does the Arietta
  sound plain?" is about movement II's simplicity, not about Beethoven's deafness
  or Vienna in 1822. Without an aspect, a context engine retrieves everything it
  can and calls the result depth.

The classifier is rule-based and deliberately dumb. It is also the piece most
worth replacing with a model, which is why it exposes a plain function and no
state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models import QuestionType

#: Aspect → predicates it makes relevant, and the words that signal it.
ASPECTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "simplicity": (
        ("variation_count", "interpretation"),
        ("plain", "simple", "simplicity", "bare", "repetitive", "monotonous",
         "understated", "arietta", "theme"),
    ),
    "tempo": (
        ("metronome_mark", "duration_minutes"),
        ("tempo", "speed", "slow", "fast", "metronome", "bpm", "beat"),
    ),
    "structure": (
        ("movement_count", "variation_count", "key"),
        ("structure", "form", "movement", "movements", "variations", "key",
         "sonata form", "fugue", "stanza", "quatrain"),
    ),
    "dating": (
        ("composition_date", "publication_date", "recording_date"),
        ("when", "written", "composed", "date", "year", "published", "released",
         "recorded"),
    ),
    "premiere": (
        ("premiere_date",),
        ("premiere", "first performed", "first performance", "performed"),
    ),
    "reception": (
        ("reception",),
        ("reception", "receive", "review", "critics", "contemporaries", "audience",
         "acclaim", "scandal", "ignored"),
    ),
    "dedication": (
        ("dedication",),
        ("dedication", "dedicated", "patron"),
    ),
    "meaning": (
        ("interpretation", "reception"),
        ("meaning", "mean", "about", "express", "symbol", "interpret", "why"),
    ),
}

_TYPE_SIGNALS = [
    (QuestionType.MEANING, ("why", "mean", "meaning", "express", "feel", "symbol",
                            "interpret", "say about")),
    (QuestionType.TECHNIQUE, ("how does", "how is", "technique", "harmony", "tempo",
                              "metre", "meter", "rhythm", "chord", "counterpoint",
                              "instrument", "verse", "stanza", "syntax")),
    (QuestionType.HISTORY, ("when", "history", "premiere", "reception", "context",
                            "period", "era", "political", "war")),
    (QuestionType.COMPARISON, ("compare", "compared", "difference", "unlike",
                               "similar", " versus ", " vs ")),
]


@dataclass
class Interpretation:
    question: str
    question_type: QuestionType
    aspect: str
    predicates: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)

    @property
    def is_underspecified(self) -> bool:
        return self.aspect == "general" and not self.matched_terms


def interpret(question: str) -> Interpretation:
    text = f" {question.lower().strip()} "
    qtype = _classify(text)
    aspect, predicates, matched = _aspect(text)
    if aspect == "general" and qtype == QuestionType.HISTORY:
        aspect, predicates = "dating", ["composition_date", "publication_date",
                                        "premiere_date"]
    return Interpretation(
        question=question,
        question_type=qtype,
        aspect=aspect,
        predicates=predicates,
        matched_terms=matched,
    )


def _classify(text: str) -> QuestionType:
    for qtype, signals in _TYPE_SIGNALS:
        if any(signal in text for signal in signals):
            return qtype
    return QuestionType.CONTEXT


#: Specificity priors. A question that says "plain" is about the music's
#: character, not about its movement count, even though both words appear. These
#: weights encode that judgement in one place, so it can be tuned against the
#: golden set instead of being buried in a prompt.
ASPECT_WEIGHTS = {
    "simplicity": 1.5,
    "tempo": 1.4,
    "reception": 1.2,
    "dedication": 1.1,
    "premiere": 1.1,
    "dating": 1.0,
    "structure": 0.8,
    "meaning": 0.7,
}


def _aspect(text: str) -> tuple[str, list[str], list[str]]:
    best: tuple[float, str, list[str], list[str]] | None = None
    for aspect, (predicates, words) in ASPECTS.items():
        matched = [w for w in words if w in text]
        if not matched:
            continue
        # Longest matched phrase × the aspect's specificity prior.
        score = max(len(w) for w in matched) * ASPECT_WEIGHTS.get(aspect, 1.0)
        if best is None or score > best[0]:
            best = (score, aspect, list(predicates), matched)
    if best is None:
        return "general", [], []
    _, aspect, predicates, matched = best
    return aspect, predicates, matched


def relevance_terms(interp: Interpretation) -> set[str]:
    """Words the ranker should look for, derived from the question."""
    terms = set(re.findall(r"[a-zà-ÿ']{4,}", interp.question.lower()))
    for word in ASPECTS.get(interp.aspect, ((), ()))[1]:
        terms.update(word.split())
    return terms
