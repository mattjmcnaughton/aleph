"""Aleph as the OAuth 2.1 authorization server for its MCP server.

MCP clients (Claude, Amp) sign a learner in with Authorization Code + PKCE
(S256 only) and identify themselves with a **Client ID Metadata Document**
(CIMD): the ``client_id`` is an HTTPS URL serving the client's name and
redirect URIs, so nothing is registered ahead of time. Any HTTPS client may
ask; the learner decides on the consent page, which names the client and the
host it returns to (MCP authorization spec, "Trust Policies").

A completed flow creates a **connection**: an ``api_keys`` row carrying the
client's ``client_id``. Its access token is an API key, so it works only
through ``/mcp``. Following OAuth 2.1 for public clients, the access token
expires after :data:`ACCESS_TOKEN_TTL` and the refresh token rotates on every
use; presenting a replaced refresh token (a sign it was stolen) revokes the
connection, as does presenting a redeemed authorization code again.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

import httpx

from aleph.repositories.api_keys import ApiKeyRepository
from aleph.repositories.oauth_codes import OAuthCodeRepository
from aleph.services.api_keys import digest

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

SCOPE = "mcp"
CODE_TTL = timedelta(seconds=60)
ACCESS_TOKEN_TTL = timedelta(hours=1)
# Sliding: each refresh renews it, so a client used at least monthly stays
# connected and an abandoned one lapses.
REFRESH_TOKEN_TTL = timedelta(days=30)
# Approvals (codes issued) per learner per hour.
APPROVALS_PER_HOUR = 10
# The CIMD draft suggests servers cap documents at 5 KB.
MAX_METADATA_BYTES = 5 * 1024
METADATA_TIMEOUT_SECONDS = 5.0
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OAuthError(Exception):
    """An RFC 6749 error: ``error`` is the code, the message its description."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


@dataclass(frozen=True)
class ClientMetadata:
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]

    @property
    def loopback_only(self) -> bool:
        """Every redirect goes to this device: any local program could claim
        to be this client, so the consent page warns (CIMD §6)."""
        return all(_is_loopback(uri) for uri in self.redirect_uris)


class MetadataFetcher(Protocol):
    """Fetches a client's metadata document; raises :class:`OAuthError`."""

    async def fetch(self, url: str) -> Mapping[str, Any]: ...


def is_client_id_url(client_id: str) -> bool:
    """An HTTPS URL with a path, no fragment and no credentials (CIMD §3)."""
    parts = urlsplit(client_id)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.path not in ("", "/")
        and not parts.fragment
        and parts.username is None
        and parts.password is None
    )


async def resolve_client(fetcher: MetadataFetcher, client_id: str) -> ClientMetadata:
    """The client a ``client_id`` URL names, validated per the CIMD draft."""
    if not is_client_id_url(client_id):
        raise OAuthError("invalid_client", "client_id must be an HTTPS metadata URL")
    doc = await fetcher.fetch(client_id)
    if doc.get("client_id") != client_id:
        raise OAuthError("invalid_client", "metadata client_id does not match")
    uris = doc.get("redirect_uris")
    if (
        not isinstance(uris, list)
        or not uris
        or not all(isinstance(uri, str) for uri in uris)
    ):
        raise OAuthError("invalid_client", "metadata has no redirect_uris")
    # A public client: CIMD forbids shared secrets, and this server only
    # accepts PKCE-bound public clients.
    if doc.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("invalid_client", "only public clients are supported")
    name = doc.get("client_name")
    return ClientMetadata(
        client_id=client_id,
        client_name=(
            name.strip()[:80]
            if isinstance(name, str) and name.strip()
            else str(urlsplit(client_id).hostname)
        ),
        redirect_uris=tuple(uris),
    )


def _is_loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS


def redirect_allowed(client: ClientMetadata, redirect_uri: str) -> bool:
    """Exact match, except a loopback URI may use any port (RFC 8252 §7.3):
    native clients such as Claude Code bind an ephemeral port."""
    if redirect_uri in client.redirect_uris:
        return True
    if not _is_loopback(redirect_uri):
        return False
    asked = urlsplit(redirect_uri)
    return any(
        _is_loopback(uri)
        and (parts := urlsplit(uri)).hostname == asked.hostname
        and parts.path == asked.path
        and parts.query == asked.query
        for uri in client.redirect_uris
    )


def pkce_matches(verifier: str, challenge: str) -> bool:
    """RFC 7636 S256: ``BASE64URL(SHA256(verifier)) == challenge``."""
    if not 43 <= len(verifier) <= 128:
        return False
    digest = hashlib.sha256(verifier.encode("ascii", "replace")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(expected, challenge)


class HttpMetadataFetcher:
    """Fetches metadata over HTTPS from public addresses only (SSRF guard)."""

    async def fetch(self, url: str) -> Mapping[str, Any]:
        host = urlsplit(url).hostname or ""
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, 443, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise OAuthError("invalid_client", "metadata host not found") from exc
        if not infos or not all(
            ipaddress.ip_address(info[4][0]).is_global for info in infos
        ):
            raise OAuthError("invalid_client", "metadata host is not public")
        try:
            async with (
                httpx.AsyncClient(
                    timeout=METADATA_TIMEOUT_SECONDS, follow_redirects=False
                ) as client,
                client.stream(
                    "GET", url, headers={"Accept": "application/json"}
                ) as response,
            ):
                response.raise_for_status()
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_METADATA_BYTES:
                        raise OAuthError("invalid_client", "metadata is too large")
            doc = httpx.Response(200, content=body).json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthError("invalid_client", "metadata could not be fetched") from exc
        if not isinstance(doc, dict):
            raise OAuthError("invalid_client", "metadata is not a JSON object")
        return doc


_http_fetcher = HttpMetadataFetcher()


async def get_metadata_fetcher() -> MetadataFetcher:
    """FastAPI dependency; tests override it with an in-memory fake."""
    return _http_fetcher


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int


def _now() -> datetime:
    return datetime.now(UTC)


async def approve(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    client: ClientMetadata,
    redirect_uri: str,
    code_challenge: str,
) -> str:
    """Record the learner's approval and return a one-time code.

    Raises ``OAuthError("rate_limited")`` past :data:`APPROVALS_PER_HOUR`.
    """
    codes = OAuthCodeRepository(session)
    if await codes.count_since(user_id, _now() - timedelta(hours=1)) >= (
        APPROVALS_PER_HOUR
    ):
        raise OAuthError("rate_limited", "too many connections in the last hour")
    code = secrets.token_urlsafe(32)
    await codes.add(
        code_digest=digest(code),
        user_id=user_id,
        client_id=client.client_id,
        client_name=client.client_name,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        expires_at=_now() + CODE_TTL,
    )
    await session.commit()
    return code


async def exchange_code(
    session: AsyncSession,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> TokenSet:
    """Redeem a code once for a new connection's tokens."""
    codes = OAuthCodeRepository(session)
    grant = await codes.redeem(digest(code))
    if grant is None:
        # A second redemption means the code leaked: revoke what it issued.
        spent = await codes.by_digest(digest(code))
        if spent is not None and spent.api_key_id is not None:
            await ApiKeyRepository(session).revoke(spent.api_key_id)
        await session.commit()
        raise OAuthError("invalid_grant", "code is invalid, expired or used")
    # The code is spent from here on, whatever the outcome.
    await session.commit()
    if client_id != grant.client_id:
        raise OAuthError("invalid_grant", "code was issued to another client")
    if redirect_uri != grant.redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri does not match")
    if not pkce_matches(code_verifier, grant.code_challenge):
        raise OAuthError("invalid_grant", "code_verifier does not match")

    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = _now()
    key = await ApiKeyRepository(session).add(
        user_id=grant.user_id,
        name=grant.client_name,
        digest=digest(access),
        client_id=grant.client_id,
        expires_at=now + ACCESS_TOKEN_TTL,
        refresh_digest=digest(refresh),
        refresh_expires_at=now + REFRESH_TOKEN_TTL,
    )
    await codes.link_api_key(grant.id, key.id)
    await session.commit()
    return TokenSet(access, refresh, int(ACCESS_TOKEN_TTL.total_seconds()))


async def refresh(
    session: AsyncSession, *, refresh_token: str, client_id: str
) -> TokenSet:
    """Rotate a connection's tokens; a replaced refresh token revokes it."""
    keys = ApiKeyRepository(session)
    access, new_refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = _now()
    key = await keys.rotate(
        refresh_digest=digest(refresh_token),
        client_id=client_id,
        digest=digest(access),
        new_refresh_digest=digest(new_refresh),
        expires_at=now + ACCESS_TOKEN_TTL,
        refresh_expires_at=now + REFRESH_TOKEN_TTL,
    )
    if key is None:
        await keys.revoke_by_previous_refresh(digest(refresh_token))
        await session.commit()
        raise OAuthError("invalid_grant", "refresh token is invalid or expired")
    await session.commit()
    return TokenSet(access, new_refresh, int(ACCESS_TOKEN_TTL.total_seconds()))
