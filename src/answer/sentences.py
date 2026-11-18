"""Claim → sentence templates for the deterministic assembler.

These sentences are deliberately plain and slightly repetitive: each one is the
smallest honest rendering of a single claim, and the reader can check it against
the citation beside it. Prose quality is the LLM assembler's job (Baseline B,
§10.4); provability is this one's.

Note what is *not* here: no connective that asserts causation ("because",
"therefore", "which is why"), no evaluative adjective that no source used. The
only joins available are "and", a full stop, and whatever the sources said.
"""

from __future__ import annotations

import re

from src.models import ExtractedClaim

#: "December 7, 2018" takes "on"; "1859" and "spring 1859" take "in".
_DAY_FIRST = re.compile(r"\b\d{1,2},?\s+\d{4}\s*$")


def _on_or_in(value: dict, value_text: str) -> str:
    display = (value or {}).get("display") or value_text
    return f"on {display}" if _DAY_FIRST.search(display) else f"in {display}"


def sentence_for(extracted: ExtractedClaim) -> str:
    return sentence_from_values(extracted.predicate, extracted.value, extracted.value_text)


def sentence_from_values(predicate: str, value: dict | None, value_text: str) -> str:
    value = value or {}
    vtext = (value_text or "").strip().rstrip(".")

    # A date sentence keeps the source's own words when the match begins with
    # the verb ("written in the summer of 1871"); otherwise it is built from the
    # parsed value. The words alone are not a sentence: "was begun in 1842 but
    # not completed until 1859" leaves "completed until 1859" in the match, and
    # a template around it read "written in completed until 1859".
    if predicate == "composition_date":
        if vtext.lower().startswith(("written", "composed")):
            return f"The work was {vtext}."
        return f"The work dates from {value.get('display') or vtext}."

    if predicate == "publication_date":
        if vtext.lower().startswith(("published", "released")):
            return f"It was {vtext}."
        return f"It was published {_on_or_in(value, vtext)}."

    if predicate == "recording_date":
        if vtext.lower().startswith(("recorded", "cut")):
            return f"It was {vtext}."
        return f"It was recorded {_on_or_in(value, vtext)}."

    if predicate == "premiere_date":
        if vtext.lower().startswith("first"):
            return f"It was {vtext}."
        return f"The premiere was {_on_or_in(value, vtext)}."

    if predicate == "dedication":
        return f"It is dedicated to {value.get('text', vtext)}."

    if predicate == "movement_count":
        return f"It has {value.get('count')} movements."

    if predicate == "variation_count":
        return f"The variation movement has {value.get('count')} variations."

    if predicate == "key":
        return f"Its tonal centre is {value.get('text', vtext)}."

    if predicate == "genre":
        return f"It is primarily a {value.get('text', vtext)} album."

    if predicate == "line_count":
        return f"It runs to {value.get('count')} lines."

    if predicate == "stanza_count":
        return f"It is built of {value.get('count')} quatrains."

    if predicate == "sales_copies":
        return f"It {vtext}." if vtext.lower().startswith("sold") \
            else f"It sold {value.get('count'):,} copies."

    if predicate == "subject_matter":
        return f"It describes {value.get('text', vtext)}."

    if predicate == "setting":
        return f"It was written in {value.get('text', vtext)}."

    if predicate == "stylistic_elements":
        return f"It incorporates {value.get('text', vtext)}."

    return vtext
