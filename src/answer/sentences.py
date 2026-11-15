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

from src.models import ExtractedClaim


def sentence_for(extracted: ExtractedClaim) -> str:
    return sentence_from_values(extracted.predicate, extracted.value, extracted.value_text)


def sentence_from_values(predicate: str, value: dict | None, value_text: str) -> str:
    value = value or {}
    vtext = (value_text or "").strip().rstrip(".")

    if predicate == "composition_date":
        if vtext.lower().startswith(("written", "composed")):
            return f"The work was {vtext}."
        return f"The work was written in {vtext}."

    if predicate == "publication_date":
        if vtext.lower().startswith(("published", "released")):
            return f"It was {vtext}."
        return f"It was published in {vtext}."

    if predicate == "recording_date":
        return f"It was {vtext}."

    if predicate == "premiere_date":
        if vtext.lower().startswith("first"):
            return f"It was {vtext}."
        return f"The premiere was in {vtext}."

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
