"""Bearer access-token authentication for native clients (iOS app).

The web SPA authenticates with the first-party session cookie set by the
server-side OIDC code flow (``routers/auth.py``). A native client cannot hold
that cookie, so it runs Authorization Code + PKCE against the **same** OIDC
provider itself and calls ``/api/v1`` with ``Authorization: Bearer <access
token>``. This module turns such a token into the same :class:`AuthIdentity`
the cookie flow's :func:`aleph.auth.fetch_identity` produces, so the account,
admin classification, feature flags and rate limits all resolve identically.

Three seams, each a small ``Protocol`` so unit tests swap in in-memory fakes
(locally generated keys, canned userinfo) instead of patching HTTP:

* :class:`SigningKeySource` — the provider's published JWKS. Production:
  :class:`OIDCProviderClient`, discovered from the issuer metadata, cached, and
  refreshed (throttled) when a token names an unknown ``kid`` (key rotation).
* :class:`UserInfoSource` — the provider's userinfo endpoint. Access tokens do
  not reliably carry profile/email claims (Auth0's never do), and derived admin
  (TDD D14) keys off a **verified** email, so identity claims come from
  userinfo — the same standard claim set the ID token gives the cookie flow —
  and never from the access token body (only its verified ``iss``/``sub``).
* :class:`AccessTokenVerifier` — pure JWT validation over a key source:
  signature, an allowlist of asymmetric algorithms (never ``none``/``HS*``),
  ``iss``, ``aud``, ``exp``/``nbf``/``iat`` with a small leeway.

:class:`BearerAuthenticator` composes them and caches the userinfo-derived
identity per ``(issuer, subject)`` for a few minutes, so a token is still fully
verified on every request but the provider's (rate-limited) userinfo endpoint is
not called on each one.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
import structlog
from joserfc import jws, jwt
from joserfc.errors import JoseError
from joserfc.jwk import import_key

from aleph.auth import AuthIdentity, fetch_identity, metadata_url_for
from aleph.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

logger = structlog.get_logger()

# Asymmetric JWS algorithms only, mapped to the JWK key type each requires. A
# provider-signed access token is never symmetric: accepting ``HS*`` would let
# anyone holding a *public* key (or a JWKS ``oct`` entry) mint tokens, and
# ``none`` is no signature at all. Pinning the key type per algorithm closes the
# classic RSA-public-key-as-HMAC-secret confusion as well.
ALGORITHM_KEY_TYPES: dict[str, Literal["RSA", "EC"]] = {
    "RS256": "RSA",
    "RS384": "RSA",
    "RS512": "RSA",
    "PS256": "RSA",
    "PS384": "RSA",
    "PS512": "RSA",
    "ES256": "EC",
    "ES384": "EC",
    "ES512": "EC",
}

# Clock-skew allowance for ``exp``/``nbf``/``iat`` (seconds).
DEFAULT_LEEWAY_SECONDS = 60
# How long a verified subject's userinfo-derived identity is reused. Bounds how
# stale a display name / email (and so admin classification) can be for a
# bearer client, and how often the provider's userinfo endpoint is hit.
DEFAULT_IDENTITY_TTL_SECONDS = 300
_IDENTITY_CACHE_SIZE = 1024
# A real access token is a few KB at most; refuse to parse anything absurd.
MAX_TOKEN_LENGTH = 16 * 1024


class InvalidBearerToken(Exception):  # noqa: N818 - reads as the condition it signals.
    """The presented bearer token is not acceptable (always a ``401``).

    The message is for logs only; clients get a generic ``invalid bearer token``
    so the response never explains which check failed.
    """


class IdentityProviderUnavailable(Exception):  # noqa: N818
    """The provider could not be reached to resolve keys or userinfo (``503``)."""


class SigningKeySource(Protocol):
    """The provider's published signing keys, as JWK dicts."""

    async def get_keys(self, *, refresh: bool = False) -> Sequence[Mapping[str, Any]]:
        """Return the (cached) key set; ``refresh`` asks for a re-fetch.

        Implementations may throttle refreshes and return the cached set.
        """
        ...


class UserInfoSource(Protocol):
    """The provider's userinfo endpoint, called with the presented token."""

    async def fetch_userinfo(self, access_token: str) -> Mapping[str, Any]:
        """Return the userinfo claims.

        Raises :class:`InvalidBearerToken` when the provider rejects the token
        and :class:`IdentityProviderUnavailable` when it cannot be reached.
        """
        ...


@dataclass(frozen=True)
class VerifiedAccessToken:
    """The validated claims of an accepted access token."""

    issuer: str
    subject: str
    claims: Mapping[str, Any]


class AccessTokenVerifier:
    """Validate a JWT access token against the provider's signing keys."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_source: SigningKeySource,
        leeway: int = DEFAULT_LEEWAY_SECONDS,
        now: Callable[[], int] | None = None,
    ) -> None:
        if not issuer or not audience:
            raise ValueError("issuer and audience are required")
        # The configured issuer is matched exactly, tolerating only the presence
        # or absence of one trailing slash (Auth0 issuers end in ``/``; an
        # operator may configure ``OIDC_ISSUER`` either way). The identity key
        # stored on the account is always the token's own ``iss`` string — the
        # same value the ID token carries into the cookie flow.
        base = issuer.rstrip("/")
        self._issuers = [base, f"{base}/"]
        self._audience = audience
        self._key_source = key_source
        self._leeway = leeway
        self._now = now

    async def verify(self, token: str) -> VerifiedAccessToken:
        """Return the token's claims, or raise :class:`InvalidBearerToken`."""
        if not token or len(token) > MAX_TOKEN_LENGTH or token.count(".") != 2:
            raise InvalidBearerToken("not a compact JWS")

        try:
            header = jws.extract_compact(token.encode("ascii")).protected
        except (JoseError, ValueError, TypeError, UnicodeError) as exc:
            raise InvalidBearerToken(f"malformed token: {exc}") from exc

        alg = header.get("alg")
        kid = header.get("kid")
        if not isinstance(alg, str) or alg not in ALGORITHM_KEY_TYPES:
            raise InvalidBearerToken(f"disallowed alg: {alg!r}")
        if not isinstance(kid, str) or not kid:
            raise InvalidBearerToken("missing kid")

        key = await self._find_key(kid, alg)

        try:
            decoded = jwt.decode(token, key, algorithms=[alg])
            self._claims_registry().validate(decoded.claims)
        except (JoseError, ValueError, TypeError) as exc:
            raise InvalidBearerToken(f"rejected token: {exc}") from exc

        claims = decoded.claims
        return VerifiedAccessToken(
            issuer=str(claims["iss"]),
            subject=str(claims["sub"]),
            claims=claims,
        )

    async def _find_key(self, kid: str, alg: str) -> Any:
        jwk = _select_jwk(await self._key_source.get_keys(), kid)
        if jwk is None:
            # An unknown ``kid`` is what a key rotation looks like: re-fetch
            # once (the source throttles) before giving up.
            jwk = _select_jwk(await self._key_source.get_keys(refresh=True), kid)
        if jwk is None:
            raise InvalidBearerToken("unknown kid")

        if jwk.get("use") not in (None, "sig"):
            raise InvalidBearerToken("key is not a signing key")
        if jwk.get("alg") not in (None, alg):
            raise InvalidBearerToken("token alg does not match key alg")
        try:
            # ``key_type`` pins the JWK family to the algorithm's (an ``oct`` or
            # EC key can never verify an RS256 token, and vice versa).
            return import_key(dict(jwk), key_type=ALGORITHM_KEY_TYPES[alg])
        except (JoseError, ValueError, TypeError) as exc:
            raise InvalidBearerToken(f"unusable key: {exc}") from exc

    def _claims_registry(self) -> jwt.JWTClaimsRegistry:
        return jwt.JWTClaimsRegistry(
            now=self._now,
            leeway=self._leeway,
            iss={"essential": True, "values": self._issuers},
            sub={"essential": True},
            aud={"essential": True, "value": self._audience},
            exp={"essential": True},
        )


def _select_jwk(
    keys: Sequence[Mapping[str, Any]], kid: str
) -> Mapping[str, Any] | None:
    matches = [key for key in keys if key.get("kid") == kid]
    # Two keys sharing one ``kid`` is ambiguous; refuse rather than guess.
    return matches[0] if len(matches) == 1 else None


class BearerAuthenticator:
    """Bearer token -> :class:`AuthIdentity`, with a short identity cache."""

    def __init__(
        self,
        *,
        verifier: AccessTokenVerifier,
        userinfo: UserInfoSource,
        identity_ttl: float = DEFAULT_IDENTITY_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._verifier = verifier
        self._userinfo = userinfo
        self._ttl = identity_ttl
        self._clock = clock
        self._cache: OrderedDict[tuple[str, str], tuple[float, AuthIdentity]] = (
            OrderedDict()
        )

    async def authenticate(self, token: str) -> AuthIdentity:
        """Verify ``token`` and return the identity it authenticates.

        Every call fully verifies the token; only the userinfo lookup is
        cached, keyed by the *verified* ``(iss, sub)`` so a forged token can
        never read or poison another subject's entry.
        """
        verified = await self._verifier.verify(token)
        key = (verified.issuer, verified.subject)

        cached = self._cache.get(key)
        if cached is not None and cached[0] > self._clock():
            self._cache.move_to_end(key)
            return cached[1]

        info = await self._userinfo.fetch_userinfo(token)
        # OIDC Core §5.3.2: the userinfo ``sub`` MUST match the token's, or the
        # response must not be used.
        if str(info.get("sub") or "") != verified.subject:
            raise InvalidBearerToken("userinfo subject does not match token")
        try:
            # The exact mapping the cookie flow uses (including dropping an
            # explicitly unverified email); ``iss``/``sub`` come from the
            # verified token since userinfo responses usually omit ``iss``.
            identity = fetch_identity(
                {
                    "userinfo": {
                        **info,
                        "iss": verified.issuer,
                        "sub": verified.subject,
                    }
                }
            )
        except ValueError as exc:
            raise InvalidBearerToken(str(exc)) from exc

        self._cache[key] = (self._clock() + self._ttl, identity)
        self._cache.move_to_end(key)
        while len(self._cache) > _IDENTITY_CACHE_SIZE:
            self._cache.popitem(last=False)
        return identity


class OIDCProviderClient:
    """HTTP :class:`SigningKeySource` + :class:`UserInfoSource` via discovery."""

    def __init__(
        self,
        metadata_url: str,
        *,
        timeout: float = 5.0,
        min_refresh_interval: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._metadata_url = metadata_url
        self._timeout = timeout
        self._min_refresh_interval = min_refresh_interval
        self._transport = transport
        self._clock = clock
        self._metadata: dict[str, Any] | None = None
        self._keys: list[dict[str, Any]] | None = None
        self._keys_fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get_keys(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            stale = self._clock() - self._keys_fetched_at >= self._min_refresh_interval
            # A refresh is throttled so a stream of tokens with made-up ``kid``s
            # cannot turn into a stream of JWKS fetches against the provider.
            if self._keys is None or (refresh and stale):
                metadata = await self._get_metadata()
                body = await self._get_json(_required_url(metadata, "jwks_uri"))
                keys = body.get("keys")
                if not isinstance(keys, list):
                    raise IdentityProviderUnavailable("JWKS has no keys array")
                self._keys = [key for key in keys if isinstance(key, dict)]
                self._keys_fetched_at = self._clock()
            return self._keys

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        metadata = await self._get_metadata()
        url = _required_url(metadata, "userinfo_endpoint")
        try:
            async with self._client() as client:
                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            raise IdentityProviderUnavailable(f"userinfo failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise InvalidBearerToken("provider rejected the token at userinfo")
        if response.status_code != 200:
            raise IdentityProviderUnavailable(
                f"userinfo returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            # A signed (``application/jwt``) userinfo response lands here too;
            # neither dev Keycloak nor Auth0 is configured to send one.
            raise IdentityProviderUnavailable("userinfo is not JSON") from exc
        if not isinstance(body, dict):
            raise IdentityProviderUnavailable("userinfo is not a JSON object")
        return body

    async def _get_metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            self._metadata = await self._get_json(self._metadata_url)
        return self._metadata

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(url)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IdentityProviderUnavailable(f"fetching {url} failed: {exc}") from exc
        if not isinstance(body, dict):
            raise IdentityProviderUnavailable(f"{url} did not return a JSON object")
        return body

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)


def _required_url(metadata: Mapping[str, Any], name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or not value:
        raise IdentityProviderUnavailable(f"provider metadata has no {name}")
    return value


def get_bearer_authenticator() -> BearerAuthenticator | None:
    """The process-wide authenticator, or ``None`` when bearer auth is off.

    A FastAPI dependency (tests override it with a fake-backed authenticator).
    Bearer auth is enabled only when ``OIDC_API_AUDIENCE`` is configured: with
    no audience there is nothing to bind a token to *this* API, so every bearer
    token is rejected. Built per distinct configuration and cached, so the JWKS
    and identity caches persist across requests.
    """
    if not settings.oidc_api_audience:
        return None
    return _build_authenticator(
        settings.oidc_issuer, settings.oidc_api_audience, metadata_url_for(settings)
    )


@functools.cache
def _build_authenticator(
    issuer: str, audience: str, metadata_url: str
) -> BearerAuthenticator:
    provider = OIDCProviderClient(metadata_url)
    return BearerAuthenticator(
        verifier=AccessTokenVerifier(
            issuer=issuer, audience=audience, key_source=provider
        ),
        userinfo=provider,
    )
