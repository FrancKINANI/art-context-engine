"""Reader contributions, and the check that decides whether they count.

The product's promise is that a sentence is only printed because a dated source
supports it. A reader's addition cannot be exempt from that: if "this verse is
about the 2005 riots" is going to sit under a document, someone has to be able
to see where it comes from.

So the rule is mechanical, and deliberately stupid:

1. the contributor quotes a sentence, verbatim, and gives the page it is on;
2. the page is fetched;
3. the sentence is looked for in it, whitespace-normalised.

Found, and the contribution is ACCEPTED. Not found, and it is REJECTED with the
reason. No model is asked whether the reading is good, because a model agreeing
is not evidence — and a reader whose note was rejected for the wrong reason can
see exactly which sentence failed and fix it.

What this cannot do is check that the sentence *supports* the contribution. A
real quotation can be attached to a wrong reading. That is why accepted
contributions are shown beside the document as margin notes, attributed, and
never merged into the thesis.
"""

from __future__ import annotations

import html
import re

from src.config import settings
from src.models import Contribution, ContributionStatus
from src.retrieval.http import FetchError, UnsafeAddress, get_backend, is_public_url

#: Shorter than this and a "quotation" is a common phrase that proves nothing.
MIN_QUOTE_CHARS = 40
MAX_QUOTE_CHARS = 700
MAX_BODY_CHARS = 700

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _SPACE_RE.sub(" ", (text or "")).strip()


def page_text(raw: str) -> str:
    """HTML to the text a reader would see, near enough to compare quotations."""
    without_scripts = _SCRIPT_RE.sub(" ", raw)
    text = html.unescape(_TAG_RE.sub(" ", without_scripts))
    return normalize(text)


def verify(contribution: Contribution) -> Contribution:
    """Fetch the cited page and look for the quoted sentence.

    Returns the contribution with its status set. Network failures leave it
    PENDING rather than REJECTED: "we could not check" and "we checked and it
    is not there" are different statements, and only one of them is about the
    contributor.
    """
    quote = normalize(contribution.quote)
    if len(quote) < MIN_QUOTE_CHARS:
        return contribution.model_copy(update={
            "status": ContributionStatus.REJECTED,
            "note": (
                f"A quotation under {MIN_QUOTE_CHARS} characters cannot be checked: "
                "it is a phrase, not a sentence."
            ),
        })
    if not is_public_url(contribution.source_url):
        return contribution.model_copy(update={
            "status": ContributionStatus.REJECTED,
            "note": "That address is not a public web page this server will fetch.",
        })
    if not settings.allow_live:
        return contribution.model_copy(update={
            "status": ContributionStatus.PENDING,
            "note": "Verification needs the live web; this install is offline.",
        })

    backend = get_backend()
    try:
        # public_only: the URL is a reader's, so every redirect hop is checked
        # too — a public page that answers "302 http://169.254.169.254/" is the
        # same request forgery as typing the metadata address directly.
        response = backend.get(contribution.source_url, public_only=True)
    except UnsafeAddress:
        return contribution.model_copy(update={
            "status": ContributionStatus.REJECTED,
            "note": "That address is not a public web page this server will fetch.",
        })
    except (FetchError, OSError) as exc:
        return contribution.model_copy(update={
            "status": ContributionStatus.PENDING,
            "note": f"The page could not be fetched: {type(exc).__name__}.",
        })
    if not response.ok:
        return contribution.model_copy(update={
            "status": ContributionStatus.REJECTED,
            "note": f"The page answered {response.status}, so nothing could be checked.",
        })

    haystack = page_text(response.body)
    if quote.casefold() in haystack.casefold():
        return contribution.model_copy(update={
            "status": ContributionStatus.ACCEPTED,
            "note": "Quotation found on the cited page.",
        })
    return contribution.model_copy(update={
        "status": ContributionStatus.REJECTED,
        "note": (
            "That sentence does not appear on the cited page. Copy it exactly as "
            "the page prints it, including punctuation."
        ),
    })


def badges(accepted: int, endorsements: int) -> list[str]:
    """Reputation, in plain increments. Nothing here is bought or random."""
    earned = []
    if accepted >= 1:
        earned.append("First light")
    if accepted >= 5:
        earned.append("Reader")
    if accepted >= 20:
        earned.append("Archivist")
    if accepted >= 50:
        earned.append("Scholiast")
    if endorsements >= 10:
        earned.append("Trusted")
    return earned


#: Names that would let a reader speak as the site rather than for themselves.
#: A handle is not a verification, so it must not look like one.
RESERVED_HANDLES = {
    "subtext", "admin", "administrator", "staff", "editor", "editors",
    "moderator", "mod", "support", "root", "system", "official",
}

#: A handle is cited in a margin, so it has to survive being read aloud: letters
#: and digits, with the separators people actually use in a name.
_HANDLE = re.compile(r"^[^\W\d_][\w .'’\-]{1,23}$", re.UNICODE)


def clean_handle(raw: str) -> str | None:
    """The name as it will be stored, or None if it cannot be one.

    Whitespace is collapsed rather than refused: a double space in a typed name
    is a typing artefact, not an attack. Uniqueness is the store's job — it is
    the only place that can decide it without a race.
    """
    name = " ".join(raw.split())
    if not _HANDLE.fullmatch(name):
        return None
    if name.casefold() in RESERVED_HANDLES:
        return None
    return name

