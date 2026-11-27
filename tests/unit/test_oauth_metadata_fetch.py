"""The client-metadata fetch connects only to the public address it checked.

A fake resolver and an ``httpx.MockTransport`` stand in for DNS and the
network, so these tests see exactly where the request would connect.
"""

from __future__ import annotations

import httpx
import pytest

from aleph.services.oauth import HttpMetadataFetcher, OAuthError

URL = "https://client.example/oauth/metadata.json"
DOC = {"client_id": URL, "redirect_uris": ["http://127.0.0.1/cb"]}


class SequenceResolver:
    """Answers each lookup with the next address list: a rebinding DNS."""

    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        return self.answers.pop(0)


def _recording() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=DOC)

    return httpx.MockTransport(handler), seen


@pytest.mark.anyio
async def test_connects_to_the_checked_address_even_if_dns_changes() -> None:
    # Public on the first lookup, internal on any second one.
    resolver = SequenceResolver(["93.184.215.14"], ["127.0.0.1"])
    transport, seen = _recording()

    doc = await HttpMetadataFetcher(resolver, transport).fetch(URL)

    assert doc == DOC
    assert resolver.calls == [("client.example", 443)]
    [request] = seen
    assert request.url.host == "93.184.215.14"
    assert request.url.path == "/oauth/metadata.json"
    # TLS verifies the real name, and the server sees it as Host.
    assert request.extensions["sni_hostname"] == "client.example"
    assert request.headers["host"] == "client.example"


@pytest.mark.anyio
async def test_ipv6_and_explicit_ports_are_pinned_too() -> None:
    resolver = SequenceResolver(["2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
    transport, seen = _recording()

    await HttpMetadataFetcher(resolver, transport).fetch(
        "https://client.example:8443/meta.json"
    )

    assert resolver.calls == [("client.example", 8443)]
    [request] = seen
    assert request.url.host == "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
    assert request.url.port == 8443
    assert request.headers["host"] == "client.example:8443"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "addresses",
    [["10.0.0.5"], ["93.184.215.14", "169.254.169.254"], ["::1"], []],
)
async def test_a_non_public_answer_is_refused_before_any_request(
    addresses: list[str],
) -> None:
    transport, seen = _recording()
    with pytest.raises(OAuthError) as raised:
        await HttpMetadataFetcher(SequenceResolver(addresses), transport).fetch(URL)
    assert raised.value.error == "invalid_client"
    assert seen == []
