"""Wikisource adapter (§5: PD / CC, FULL_TEXT).

This is the one adapter that can hand the engine an actual primary text — the
poem itself, in the public domain, quotable in full. For poetry that is the
difference between explaining a work and reproducing it, so the close reading
blocks can quote real lines with real spans.

The page is fetched as plain text via the MediaWiki extract API, which keeps
paragraph breaks and drops the wiki furniture.
"""

from __future__ import annotations

import html
import re

from src import rights as rights_mod
from src.models import AuthorityTier, Source, SourceType, Work
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import unknown

#: Language edition of Wikisource per source language.
_LANG_HOST = {
    "fr": "https://fr.wikisource.org/w/api.php",
    "en": "https://en.wikisource.org/w/api.php",
    "de": "https://de.wikisource.org/w/api.php",
}


class WikisourceAdapter(SourceAdapter):
    name = "wikisource"
    default_subtype = "primary_text"

    def search(self, query: str, work: Work | None = None, limit: int = 2) -> list[Hit]:
        page = (work.identifiers.get("wikisource_page") if work else None)
        lang = (work.identifiers.get("wikisource_lang", "fr") if work else "fr")
        if page:
            return [Hit(adapter=self.name, key=page, title=page,
                        url=f"{_LANG_HOST[lang].split('/w/')[0]}/wiki/{page.replace(' ', '_')}",
                        extra={"lang": lang})]

        api = _LANG_HOST.get(lang, _LANG_HOST["fr"])
        data = self._json(build_url(api, {
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": limit, "format": "json",
        }))
        if not data:
            return []
        return [
            Hit(adapter=self.name, key=item["title"], title=item["title"],
                url=f"https://{lang}.wikisource.org/wiki/{item['title'].replace(' ', '_')}",
                extra={"lang": lang})
            for item in data.get("query", {}).get("search", [])[:limit]
        ]

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        lang = hit.extra.get("lang", "fr")
        api = _LANG_HOST.get(lang, _LANG_HOST["fr"])
        # `action=parse` rather than `prop=extracts`: poem pages transclude the
        # scan's text, and an extract returns only the transclusion markup.
        data = self._json(build_url(api, {
            "action": "parse", "format": "json", "formatversion": 2,
            "page": hit.key, "prop": "text|revid", "redirects": 1,
        }))
        if not data or "parse" not in data:
            self.errors.append(f"wikisource: parse failed for {hit.key}")
            return None

        parsed = data["parse"]
        text = html_to_text(parsed.get("text", ""))
        if len(text) < 200:
            self.errors.append(f"wikisource: page {hit.key} has no transcluded text")
            return None

        return Source(
            id=f"{self.name}:{lang}:{parsed.get('revid', hit.key)}",
            work_id=work.id,
            type=SourceType.PRIMARY,
            subtype=self.default_subtype,
            title=parsed.get("title", hit.key),
            author=(work.primary_creator.name if work.primary_creator else "unknown"),
            date_document=work.date_published or unknown("publication date unrecorded"),
            url=hit.url,
            text=text,
            language=lang,
            rights=self.rights(),
            authority=AuthorityTier.PRIMARY,
        )

    def rights(self, payload: dict | None = None):
        return rights_mod.RightsInfo.public_domain("US")


# ── HTML → text ───────────────────────────────────────────────────────────

_STYLE_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.S | re.I)
_BLOCK_RE = re.compile(r"</(p|div|h[1-6]|li|tr)>|<br\s*/?>", re.I)
_CONTENT_RE = re.compile(r'<div class="mw-parser-output"[^>]*>(.*)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(markup: str) -> str:
    """Flatten a parsed Wikisource page into quotable plain text.

    The scan header (edition, publisher, page numbers) is kept: it is how a
    reader verifies which printing we quoted. CSS blocks are not.
    """
    markup = _STYLE_RE.sub("", markup)
    match = _CONTENT_RE.search(markup)
    body = match.group(1) if match else markup
    body = _BLOCK_RE.sub("\n", body)
    body = _TAG_RE.sub("", body)
    body = html.unescape(body)
    lines = [line.strip() for line in body.split("\n")]
    lines = [line for line in lines if line and not _is_css(line)]
    return "\n".join(lines)


def _is_css(line: str) -> bool:
    return "{" in line and "}" in line
