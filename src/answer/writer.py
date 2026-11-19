"""The LLM's third job: prose over verified spans (§4.7).

The model proposes; this module disposes. A proposal is a sentence and the ids
of the claims it is built from. Five rules get it printed, and each one exists
because a fluent model lies in a specific way:

* **no claim, no sentence** — an unattributed claim is not softened, it is
  dropped (this is the whole product, not a formatting rule);
* **the claim must be in this document** — a model that invents an id is citing
  evidence it does not have;
* **the span must still resolve** — evidence we can no longer point at is not
  evidence;
* **numbers are copied, never composed** — every digit in the sentence must
  appear in a cited span (or in the work's catalogue record: title, maker,
  date);
* **quotations are copied, never rewritten** — a marked quotation must be a
  substring of a cited span, punctuation and language included.

A paraphrase without quotation marks is allowed: "the movement sets Verlaine,
who is never named in the score" is a reading of the span, not a quotation of
it. What the gate cannot check is whether the paraphrase is *apt* — that is the
editor's job, and the reason a version is reviewed rather than published.

Single quotes are deliberately not treated as quotation marks: "Debussy's
setting and Verlaine's poem" contains two apostrophes, and reading between them
would make an ordinary sentence look like a borrowed quotation.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from src.answer.claims import verify_claim
from src.models import Claim, Source, Sufficiency

#: One document, not a book. A synthesizer that keeps going is cut, not obeyed.
MAX_SENTENCES = 12

#: A sentence, not a page. The synthesizer is untrusted input: without a bound,
#: a proposal is an arbitrarily large string this module scans with regexes.
MAX_SENTENCE_CHARS = 600


@dataclass(frozen=True)
class Proposal:
    """A sentence the model wants printed, and the claims it says it used."""
    text: str
    claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dropped:
    """A proposal the gate refused, with the reason a reviewer will ask for."""
    proposal: Proposal
    reason: str


#: An adapter turns claims into proposals. It owns the prompt and the parsing;
#: it does not own the decision to print, which is why it returns proposals and
#: not text.
Synthesizer = Callable[[Sequence[Claim], str], Iterable[Proposal]]

#: Words that carry no content, so a sentence is not "supported" by sharing
#: them. Kept short on purpose: an unknown word counts as content, which makes
#: the coverage rule stricter, never looser.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has",
    "have", "he", "her", "his", "in", "is", "it", "its", "not", "of", "on", "or",
    "she", "that", "the", "their", "them", "they", "this", "to", "was", "were",
    "which", "who", "will", "with", "you", "au", "aux", "avec", "ce", "ces",
    "dans", "de", "des", "du", "elle", "en", "est", "et", "il", "la", "le",
    "les", "ne", "par", "pas", "pour", "que", "qui", "sa", "se", "ses", "son",
    "sur", "un", "une",
}

#: A sentence must share at least a third of its content words with the span it
#: cites. Below that it is not a synthesis of that evidence, it is a new claim
#: wearing its citation. The threshold is low because paraphrase is legitimate;
#: it is non-zero because "It was composed in Vienna" must never print on the
#: strength of a sentence about Debussy's suite.
MIN_COVERAGE = 0.34

_NUMBER = re.compile(r"\d+")
_WORD = re.compile(r"[a-zà-öø-ÿ']{3,}")
_QUOTED = re.compile(r'"([^"]{2,})"|“([^”]{2,})”|«\s?([^»]{2,}?)\s?»')


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _tight(text: str) -> str:
    """Whitespace collapsed, case kept: a quotation is compared as written."""
    return re.sub(r"\s+", " ", text).strip()


def _content(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


def _numbers_are_copied(sentence: str, haystack: str) -> bool:
    for token in _NUMBER.findall(sentence):
        if not re.search(rf"(?<!\d){re.escape(token)}(?!\d)", haystack):
            return False
    return True


def _quotations_are_copied(sentence: str, span_text: str) -> bool:
    haystack = _tight(span_text)
    for match in _QUOTED.finditer(sentence):
        quoted = next(group for group in match.groups() if group)
        if _tight(quoted) not in haystack:
            return False
    return True


def _draws_on_span(sentence: str, haystack: str) -> bool:
    words = _content(sentence)
    if not words:
        return True          # nothing but numbers or names: checked above
    return len(words & _content(haystack)) / len(words) >= MIN_COVERAGE


def gate(
    proposals: Iterable[Proposal],
    claims: Sequence[Claim],
    sources: Mapping[str, Source],
    *,
    known_values: Sequence[str] = (),
) -> tuple[list[Proposal], list[Dropped]]:
    """Keep what the spans support, drop the rest, and say why."""
    by_id = {claim.id: claim for claim in claims}
    index = dict(sources)      # one copy per gate, not one per proposal
    known = _norm(" ".join(known_values))
    kept: list[Proposal] = []
    dropped: list[Dropped] = []
    seen: set[str] = set()

    for proposal in proposals:
        text = " ".join(proposal.text.split())
        if not text:
            continue
        if len(text) > MAX_SENTENCE_CHARS:
            dropped.append(Dropped(proposal=proposal, reason="longer than a sentence"))
            continue

        reason = ""
        cited: list[Claim] = []
        if not proposal.claim_ids:
            reason = "cites no claim"
        else:
            found = [by_id.get(claim_id) for claim_id in proposal.claim_ids]
            if any(claim is None for claim in found):
                reason = "cites a claim that is not in this document"
            else:
                cited = [claim for claim in found if claim is not None]
                if not all(verify_claim(claim, index) for claim in cited):
                    reason = "the span it cites does not resolve in its source"

        if not reason:
            span_text = " ".join(span.text for claim in cited for span in claim.spans)
            haystack = _norm(f"{span_text} {known}")
            if not _numbers_are_copied(text, haystack):
                reason = "states a number no cited span contains"
            elif not _quotations_are_copied(text, span_text):
                reason = "quotes text no cited span contains"
            elif not _draws_on_span(text, f"{span_text} {known}"):
                reason = "does not draw on the span it cites"

        if reason:
            dropped.append(Dropped(proposal=proposal, reason=reason))
            continue

        key = _norm(text)
        if key in seen:
            continue
        seen.add(key)
        kept.append(Proposal(text=text, claim_ids=tuple(proposal.claim_ids)))

    return kept, dropped


def write(
    *,
    question: str,
    claims: Sequence[Claim],
    sources: Mapping[str, Source],
    synthesize: Synthesizer,
    sufficiency: Sufficiency,
    known_values: Sequence[str] = (),
    limit: int = MAX_SENTENCES,
) -> tuple[list[Proposal], list[Dropped]]:
    """Run the synthesizer and gate its output — or don't run it at all.

    A refusal is not prose we have not written yet; it is the statement that no
    evidence supports prose. Calling the model there would spend money to
    produce the one thing §3.7 forbids, so the call does not happen.
    """
    if sufficiency is Sufficiency.INSUFFICIENT:
        return [], []
    window = list(claims)
    if not window:
        return [], []
    proposals = list(synthesize(window, question))[:limit]
    return gate(proposals, window, sources, known_values=known_values)
