"""A redirect is a second URL, and the second URL needs the same check.

The contribution flow already refuses to fetch a private address a reader typed.
It did not refuse the address that address *redirects to*: httpx followed up to
twenty hops by itself, so `https://attacker.example/x` could answer
`302 Location: http://169.254.169.254/latest/meta-data/` and the server would
fetch cloud metadata with its own network position.

The check is applied per hop, and the client no longer follows redirects on its
own — the only redirects that happen are the ones this module has looked at.
"""

from __future__ import annotations

import pytest

from src.retrieval.http import FetchError, get_following_public_redirects


class _Reply:
    def __init__(self, status: int, location: str | None = None, body: str = "ok") -> None:
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.text = body


class _Client:
    """Records every URL it is asked for, so a refused hop is provably not fetched."""

    def __init__(self, replies: dict[str, _Reply]) -> None:
        self.replies = replies
        self.seen: list[str] = []

    def get(self, url: str, **kwargs) -> _Reply:
        self.seen.append(url)
        return self.replies.get(url, _Reply(404))


def _public(url: str) -> bool:
    """A stand-in for the DNS check, so these tests never touch the network."""
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return not (
        host.startswith(("127.", "10.", "192.168.", "169.254."))
        or host.startswith("localhost")
    )


def test_a_public_redirect_is_followed():
    client = _Client({
        "https://example.org/a": _Reply(301, "https://example.org/b"),
        "https://example.org/b": _Reply(200, body="final"),
    })
    reply = get_following_public_redirects(client, "https://example.org/a", is_public=_public)
    assert reply.text == "final"
    assert client.seen == ["https://example.org/a", "https://example.org/b"]


def test_a_redirect_to_loopback_is_refused_and_not_fetched():
    client = _Client({
        "https://example.org/a": _Reply(302, "http://127.0.0.1:8000/account"),
    })
    with pytest.raises(FetchError):
        get_following_public_redirects(client, "https://example.org/a", is_public=_public)
    assert client.seen == ["https://example.org/a"]


def test_a_redirect_to_cloud_metadata_is_refused():
    client = _Client({
        "https://example.org/a": _Reply(307, "http://169.254.169.254/latest/meta-data/"),
    })
    with pytest.raises(FetchError):
        get_following_public_redirects(client, "https://example.org/a", is_public=_public)
    assert len(client.seen) == 1


def test_a_scheme_relative_redirect_is_resolved_before_the_check():
    client = _Client({
        "https://example.org/a": _Reply(302, "//127.0.0.1/x"),
    })
    with pytest.raises(FetchError):
        get_following_public_redirects(client, "https://example.org/a", is_public=_public)


def test_a_redirect_loop_stops_instead_of_running_forever():
    client = _Client({"https://example.org/a": _Reply(302, "https://example.org/a")})
    with pytest.raises(FetchError):
        get_following_public_redirects(client, "https://example.org/a", is_public=_public, max_hops=2)
    assert len(client.seen) == 3
