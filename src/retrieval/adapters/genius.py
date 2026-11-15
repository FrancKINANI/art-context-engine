"""Genius adapter (§5: licensed lyrics, never stored, never quoted).

The rule this adapter exists to demonstrate is §3.4's: *a claimed span must fit
the policy; a better quote we cannot show becomes a citation*. Genius returns
lyrics under licence, so `text` is always empty and `storage_allowed` is always
False. What we keep is the song identity, the URL, and the annotations
metadata — enough to write "the third verse names X (see Genius)" and enough for
a claim with `stance: CITATION_ONLY`.

The API requires a token. Without one the adapter still resolves a curated
song URL from the work registry, because a citation needs no credential.
"""

from __future__ import annotations

import os

from src import rights as rights_mod
from src.models import AuthorityTier, Source, SourceType, Work
from src.retrieval.adapters.base import Hit, SourceAdapter
from src.retrieval.http import build_url
from src.temporal import unknown

API = "https://api.genius.com/search"


class GeniusAdapter(SourceAdapter):
    name = "genius"
    default_subtype = "song_page"

    @property
    def token(self) -> str | None:
        return os.environ.get("GENIUS_TOKEN")

    def search(self, query: str, work: Work | None = None, limit: int = 2) -> list[Hit]:
        curated_url = work.identifiers.get("genius_url") if work else None
        curated_id = work.identifiers.get("genius_id") if work else None
        if curated_url or curated_id:
            return [Hit(
                adapter=self.name,
                key=curated_id or curated_url,
                title=work.title,
                url=curated_url or f"https://genius.com/songs/{curated_id}",
            )]

        if not self.token:
            self.errors.append("genius: no GENIUS_TOKEN; using curated identifiers only")
            return []

        url = build_url(API, {"q": query})
        # Genius authenticates with a bearer header; the Backend only does GETs,
        # so this call is made directly and any failure degrades to [].
        try:
            import httpx

            response = httpx.get(
                url, headers={"Authorization": f"Bearer {self.token}"}, timeout=15
            )
            data = response.json()
        except Exception as exc:
            self.errors.append(f"genius: {type(exc).__name__}: {exc}")
            return []

        hits = []
        for hit in data.get("response", {}).get("hits", [])[:limit]:
            result = hit.get("result", {})
            hits.append(Hit(
                adapter=self.name,
                key=str(result.get("id")),
                title=result.get("full_title") or result.get("title", ""),
                url=result.get("url"),
                snippet=(result.get("title") or ""),
            ))
        return hits

    def fetch(self, hit: Hit, work: Work) -> Source | None:
        # Deliberately no body fetch: even the API's lyric payload must not be
        # stored. Metadata + URL is the whole source.
        return Source(
            id=f"{self.name}:{hit.key}",
            work_id=work.id,
            type=SourceType.PRIMARY,
            subtype=self.default_subtype,
            title=hit.title or work.title,
            author=work.primary_creator.name if work.primary_creator else "unknown",
            date_document=work.date_published or unknown("release date unrecorded"),
            url=hit.url or (f"https://genius.com/songs/{hit.key}"),
            text="",  # §3.4: never stored
            language="en",
            rights=self.rights(),
            authority=AuthorityTier.PRIMARY,
        )

    def rights(self, payload: dict | None = None):
        return rights_mod.genius_rights()
