"""In-memory OIDC fakes for bearer-token tests (unit and integration).

Locally generated signing keys, an in-memory JWKS, canned userinfo, and a token
minter — the fakes behind ``aleph.bearer``'s ``SigningKeySource`` /
``UserInfoSource`` protocols, so no test needs a mock or the network to prove
token handling.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

from joserfc import jwt
from joserfc.jwk import ECKey, RSAKey

from aleph.bearer import AccessTokenVerifier, BearerAuthenticator

if TYPE_CHECKING:
    from collections.abc import Mapping

ISSUER = "https://issuer.example.test/"
AUDIENCE = "aleph-api"
NOW = 1_800_000_000

RSA_KEY = RSAKey.generate_key(2048, parameters={"kid": "rsa-1"}, private=True)
EC_KEY = ECKey.generate_key("P-256", parameters={"kid": "ec-1"}, private=True)
ROTATED_KEY = RSAKey.generate_key(2048, parameters={"kid": "rsa-2"}, private=True)
# Same ``kid`` as RSA_KEY but a different key pair: a forgery attempt.
IMPOSTER_KEY = RSAKey.generate_key(2048, parameters={"kid": "rsa-1"}, private=True)


def public(key: Any) -> dict[str, Any]:
    return dict(key.as_dict(private=False))


class FakeKeySource:
    """An in-memory JWKS: ``served`` until a refresh picks up ``published``."""

    def __init__(self, *keys: Any, published: list[Any] | None = None) -> None:
        self.served = [public(key) for key in keys]
        self.published = (
            [public(key) for key in published] if published is not None else None
        )
        self.refreshes = 0

    async def get_keys(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        if refresh:
            self.refreshes += 1
            if self.published is not None:
                self.served = self.published
        return self.served


class FakeUserInfo:
    def __init__(self, claims: Mapping[str, Any]) -> None:
        self.claims = dict(claims)
        self.calls = 0

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        self.calls += 1
        return self.claims


def claims(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "subject-1",
        "aud": [AUDIENCE, "https://issuer.example.test/userinfo"],
        "iat": NOW - 10,
        "nbf": NOW - 10,
        "exp": NOW + 300,
        "azp": "aleph-ios",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def mint(key: Any = RSA_KEY, *, alg: str = "RS256", **claim_overrides: Any) -> str:
    header = {"alg": alg, "kid": key.kid, "typ": "JWT"}
    return jwt.encode(header, claims(**claim_overrides), key, algorithms=[alg])


def b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


USERINFO = {
    "sub": "subject-1",
    "preferred_username": "dev",
    "name": "Dev User",
    "email": "dev@example.com",
    "email_verified": True,
}


def fake_authenticator(
    userinfo: Mapping[str, Any] | None = None, *, issuer: str = ISSUER
) -> BearerAuthenticator:
    """A real authenticator over the in-memory JWKS and canned userinfo."""
    return BearerAuthenticator(
        verifier=AccessTokenVerifier(
            issuer=issuer,
            audience=AUDIENCE,
            key_source=FakeKeySource(RSA_KEY, EC_KEY),
            now=lambda: NOW,
        ),
        userinfo=FakeUserInfo(USERINFO if userinfo is None else userinfo),
    )
