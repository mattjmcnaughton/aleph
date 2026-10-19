"""Test-only fake OIDC issuer for the iOS UI tests (iOS TDD D22, §16.3).

The macOS runner that runs ``AlephUITests`` has no Docker, so no Keycloak. In
its place the stub backend (``scripts/e2e_backend.py``) serves a minimal OIDC
issuer of its own when ``E2E_FAKE_ISSUER=1``:

* ``GET /__e2e__/oidc/.well-known/openid-configuration`` — discovery;
* ``GET /__e2e__/oidc/jwks`` — the public half of a **committed, test-only**
  RS256 key (``tests/fixtures/e2e_oidc/signing_key.jwk``);
* ``GET /__e2e__/oidc/userinfo`` — the identity for ``e2e-learner`` or
  ``e2e-admin``, after verifying the presented token.

The app's tokens are **pre-signed** with that key and committed as
``clients/ios/AlephUITests/Fixtures/tokens.json`` (``scripts/mint_e2e_tokens.py``
re-mints them). Nothing here replaces production code: the real
:class:`aleph.bearer.BearerAuthenticator` discovers this issuer over HTTP and
validates every request exactly as it validates Auth0's tokens.

Safety. The private key is published in this repository, so this issuer must
never be trusted outside the e2e harness:

* nothing under ``src/aleph`` imports this module, and neither ``scripts/`` nor
  ``tests/`` is copied into the production image;
* :func:`configure_fake_issuer` refuses any ``ENV`` but ``test``;
* ``aleph.config`` rejects an ``/__e2e__/`` issuer or metadata URL under
  ``ENV=production``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

from aleph.bearer import AccessTokenVerifier, InvalidBearerToken

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from aleph.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNING_KEY_PATH = REPO_ROOT / "tests" / "fixtures" / "e2e_oidc" / "signing_key.jwk"
TOKENS_PATH = (
    REPO_ROOT / "clients" / "ios" / "AlephUITests" / "Fixtures" / "tokens.json"
)

ISSUER_PATH = "/__e2e__/oidc"
# The ios-e2e job boots the stub backend on 127.0.0.1:8000 (.github/workflows/ios.yml);
# the issuer is baked into every pre-signed token's ``iss``.
FAKE_ISSUER = f"http://127.0.0.1:8000{ISSUER_PATH}"
FAKE_AUDIENCE = "aleph-api"
# Dev Keycloak's iOS client id: the default ``OIDC_CLIENT_MAP`` labels it ``ios``.
FAKE_CLIENT_ID = "aleph-ios"
SIGNING_KEY_ID = "e2e-fake-issuer-1"

LEARNER_SUBJECT = "e2e-learner"
ADMIN_SUBJECT = "e2e-admin"

# Fixed claims, so a re-mint reproduces the committed file byte for byte.
ISSUED_AT = 1_767_225_600  # 2026-01-01T00:00:00Z
VALID_UNTIL = 4_102_444_800  # 2100-01-01T00:00:00Z
EXPIRED_AT = 1_767_229_200  # 2026-01-01T01:00:00Z
SCOPE = "openid profile email offline_access"


def fake_issuer_requested(environ: Mapping[str, str]) -> bool:
    """True only for ``E2E_FAKE_ISSUER=1`` exactly."""
    return environ.get("E2E_FAKE_ISSUER") == "1"


def configure_fake_issuer(settings: Settings) -> None:
    """Point bearer auth at the fake issuer. Refuses anything but ``ENV=test``."""
    if settings.env != "test":
        raise RuntimeError(
            "E2E_FAKE_ISSUER=1 is only honoured with ENV=test: the fake issuer's "
            f"signing key is public (ENV={settings.env})."
        )
    settings.oidc_issuer = FAKE_ISSUER
    settings.oidc_metadata_url = ""
    settings.oidc_api_audience = FAKE_AUDIENCE


def load_signing_key(path: Path = SIGNING_KEY_PATH) -> RSAKey:
    """The committed test-only private key."""
    return RSAKey.import_key(json.loads(path.read_text()))


def public_jwks(key: RSAKey) -> dict[str, Any]:
    public: dict[str, Any] = {
        **key.as_dict(private=False),
        "kid": key.kid,
        "use": "sig",
        "alg": "RS256",
    }
    return {"keys": [public]}


def mint_token(key: RSAKey, subject: str, *, expires_at: int) -> str:
    """One RS256 access token as the fake issuer would issue it."""
    header = {"alg": "RS256", "kid": key.kid, "typ": "JWT"}
    claims = {
        "iss": FAKE_ISSUER,
        "sub": subject,
        "aud": FAKE_AUDIENCE,
        "azp": FAKE_CLIENT_ID,
        "scope": SCOPE,
        "iat": ISSUED_AT,
        "exp": expires_at,
    }
    return jwt.encode(header, claims, key)


def mint_tokens(key: RSAKey) -> dict[str, str]:
    """The UI-test fixture: a learner, an admin and an already-expired learner."""
    return {
        "learner": mint_token(key, LEARNER_SUBJECT, expires_at=VALID_UNTIL),
        "admin": mint_token(key, ADMIN_SUBJECT, expires_at=VALID_UNTIL),
        "expiredLearner": mint_token(key, LEARNER_SUBJECT, expires_at=EXPIRED_AT),
    }


def render_tokens(tokens: Mapping[str, str]) -> str:
    return json.dumps(dict(tokens), indent=2, sort_keys=True) + "\n"


def userinfo_for(subject: str, *, admin_domain: str | None) -> dict[str, Any] | None:
    """The userinfo claims for a known e2e subject, else ``None``."""
    if subject == LEARNER_SUBJECT:
        return {
            "sub": subject,
            "name": "E2E Learner",
            "preferred_username": subject,
            "email": "e2e-learner@example.com",
            "email_verified": True,
        }
    if subject == ADMIN_SUBJECT:
        claims: dict[str, Any] = {
            "sub": subject,
            "name": "E2E Admin",
            "preferred_username": subject,
        }
        if admin_domain:
            claims.update(
                {"email": f"{subject}@{admin_domain}", "email_verified": True}
            )
        return claims
    return None


class _StaticKeySource:
    def __init__(self, keys: Sequence[Mapping[str, Any]]) -> None:
        self._keys = keys

    async def get_keys(self, *, refresh: bool = False) -> Sequence[Mapping[str, Any]]:
        return self._keys


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"error": "invalid_token"},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


def fake_issuer_router(
    issuer: str = FAKE_ISSUER,
    *,
    admin_domain: str | None,
    key: RSAKey | None = None,
) -> APIRouter:
    """Discovery, JWKS and userinfo under :data:`ISSUER_PATH`.

    ``admin_domain`` is the email domain the admin subject gets, so the
    backend's derived-admin rule (``ADMIN_EMAIL_DOMAINS``) classifies it.
    """
    signing_key = key or load_signing_key()
    jwks = public_jwks(signing_key)
    verifier = AccessTokenVerifier(
        issuer=issuer, audience=FAKE_AUDIENCE, key_source=_StaticKeySource(jwks["keys"])
    )
    router = APIRouter(prefix=ISSUER_PATH, include_in_schema=False)

    @router.get("/.well-known/openid-configuration")
    async def discovery() -> dict[str, Any]:
        return {
            "issuer": issuer,
            "jwks_uri": f"{issuer}/jwks",
            "userinfo_endpoint": f"{issuer}/userinfo",
            "id_token_signing_alg_values_supported": ["RS256"],
            "subject_types_supported": ["public"],
        }

    @router.get("/jwks")
    async def jwks_endpoint() -> dict[str, Any]:
        return jwks

    @router.get("/userinfo", response_model=None)
    async def userinfo(request: Request) -> dict[str, Any] | JSONResponse:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return _unauthorized()
        try:
            verified = await verifier.verify(token)
        except InvalidBearerToken:
            return _unauthorized()
        claims = userinfo_for(verified.subject, admin_domain=admin_domain)
        return claims if claims is not None else _unauthorized()

    return router
