"""Bearer access-token verification for native clients (``aleph/bearer.py``).

Fakes over mocks: tokens are signed with locally generated keys and verified
against an in-memory JWKS (:class:`FakeKeySource`); userinfo is a canned
in-memory :class:`FakeUserInfo`. No network, no Keycloak — the end-to-end check
against a real provider is ``tests/integration/test_bearer_keycloak.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import OctKey

from aleph.auth import AuthIdentity
from aleph.bearer import (
    AccessTokenVerifier,
    BearerAuthentication,
    BearerAuthenticator,
    IdentityProviderUnavailable,
    InvalidBearerToken,
    OIDCProviderClient,
)
from tests._bearer_fakes import (
    AUDIENCE,
    EC_KEY,
    IMPOSTER_KEY,
    ISSUER,
    NOW,
    ROTATED_KEY,
    RSA_KEY,
    USERINFO,
    FakeKeySource,
    FakeUserInfo,
    b64,
    claims,
    mint,
    public,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def verifier(source: FakeKeySource | None = None, **kwargs: Any) -> AccessTokenVerifier:
    return AccessTokenVerifier(
        issuer=kwargs.pop("issuer", ISSUER),
        audience=kwargs.pop("audience", AUDIENCE),
        key_source=source or FakeKeySource(RSA_KEY, EC_KEY),
        now=lambda: NOW,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# AccessTokenVerifier
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_accepts_a_valid_rs256_token() -> None:
    verified = await verifier().verify(mint())

    assert verified.issuer == ISSUER
    assert verified.subject == "subject-1"


@pytest.mark.anyio
async def test_accepts_a_valid_es256_token_with_a_string_audience() -> None:
    verified = await verifier().verify(mint(EC_KEY, alg="ES256", aud=AUDIENCE))

    assert verified.subject == "subject-1"


@pytest.mark.anyio
async def test_issuer_trailing_slash_is_tolerated_either_way() -> None:
    token = mint(iss=ISSUER.rstrip("/"))

    verified = await verifier().verify(token)

    # The identity key is the token's own ``iss``, never a normalized copy.
    assert verified.issuer == ISSUER.rstrip("/")


@pytest.mark.anyio
async def test_rejects_an_expired_token() -> None:
    with pytest.raises(InvalidBearerToken):
        await verifier().verify(mint(exp=NOW - 120))


@pytest.mark.anyio
async def test_tolerates_small_clock_skew_on_exp() -> None:
    verified = await verifier().verify(mint(exp=NOW - 30))

    assert verified.subject == "subject-1"


@pytest.mark.anyio
async def test_rejects_a_token_not_yet_valid() -> None:
    with pytest.raises(InvalidBearerToken):
        await verifier().verify(mint(nbf=NOW + 600))


@pytest.mark.anyio
async def test_rejects_the_wrong_issuer() -> None:
    with pytest.raises(InvalidBearerToken):
        await verifier().verify(mint(iss="https://evil.example.test/"))


@pytest.mark.anyio
async def test_rejects_an_issuer_that_merely_prefixes_the_configured_one() -> None:
    with pytest.raises(InvalidBearerToken):
        await verifier().verify(mint(iss=f"{ISSUER}realms/other"))


@pytest.mark.anyio
@pytest.mark.parametrize("aud", ["aleph", ["account", "aleph-ios"], "aleph-api-x"])
async def test_rejects_the_wrong_audience(aud: object) -> None:
    with pytest.raises(InvalidBearerToken):
        await verifier().verify(mint(aud=aud))


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["aud", "exp", "sub", "iss"])
async def test_rejects_a_token_missing_an_essential_claim(missing: str) -> None:
    with pytest.raises(InvalidBearerToken):
        overrides: dict[str, Any] = {missing: None}
        await verifier().verify(mint(**overrides))


@pytest.mark.anyio
async def test_rejects_a_bad_signature() -> None:
    # Signed by a different private key that claims the trusted key's ``kid``.
    with pytest.raises(InvalidBearerToken):
        await verifier().verify(mint(IMPOSTER_KEY))


@pytest.mark.anyio
async def test_rejects_a_tampered_payload() -> None:
    header, _payload, signature = mint().split(".")
    forged = b64(claims(sub="someone-else"))

    with pytest.raises(InvalidBearerToken):
        await verifier().verify(f"{header}.{forged}.{signature}")


@pytest.mark.anyio
async def test_rejects_alg_none() -> None:
    token = f"{b64({'alg': 'none', 'kid': 'rsa-1'})}.{b64(claims())}."

    with pytest.raises(InvalidBearerToken):
        await verifier().verify(token)


@pytest.mark.anyio
async def test_rejects_hs256_even_when_the_jwks_publishes_a_matching_oct_key() -> None:
    secret = OctKey.generate_key(256, parameters={"kid": "shared"}, private=True)
    source = FakeKeySource(RSA_KEY)
    source.served.append(dict(secret.as_dict(private=True)))
    token = jwt.encode(
        {"alg": "HS256", "kid": "shared"}, claims(), secret, algorithms=["HS256"]
    )

    with pytest.raises(InvalidBearerToken):
        await verifier(source).verify(token)


@pytest.mark.anyio
@pytest.mark.filterwarnings("ignore::joserfc.jwk.SecurityWarning")
async def test_rejects_hs256_signed_with_the_rsa_public_key_as_secret() -> None:
    # The classic alg-confusion forgery: HMAC keyed by the public key bytes.
    public_pem = RSA_KEY.as_pem(private=False)
    hmac_key = OctKey.import_key(public_pem)
    token = jwt.encode(
        {"alg": "HS256", "kid": "rsa-1"}, claims(), hmac_key, algorithms=["HS256"]
    )

    with pytest.raises(InvalidBearerToken):
        await verifier().verify(token)


@pytest.mark.anyio
async def test_rejects_a_key_of_the_wrong_family_for_the_alg() -> None:
    # An RS256 header pointing at the EC key's ``kid``.
    token = mint(RSA_KEY)
    header, payload, signature = token.split(".")
    relabelled = b64({"alg": "RS256", "kid": "ec-1"})

    with pytest.raises(InvalidBearerToken):
        await verifier().verify(f"{relabelled}.{payload}.{signature}")


@pytest.mark.anyio
async def test_rejects_a_token_alg_that_differs_from_the_keys_declared_alg() -> None:
    # Same RSA family, so only the JWK's own ``alg`` pins RS256 over PS256.
    source = FakeKeySource()
    source.served.append({**public(RSA_KEY), "alg": "RS256"})

    with pytest.raises(InvalidBearerToken):
        await verifier(source).verify(mint(RSA_KEY, alg="PS256"))


@pytest.mark.anyio
async def test_rejects_a_kid_shared_by_two_published_keys() -> None:
    # Ambiguous key set: the genuine key is first, so "take the first match"
    # would accept; refusing is the only safe reading.
    source = FakeKeySource(RSA_KEY, IMPOSTER_KEY)

    with pytest.raises(InvalidBearerToken):
        await verifier(source).verify(mint(RSA_KEY))


@pytest.mark.anyio
async def test_unknown_kid_refreshes_the_key_set_once() -> None:
    # Key rotation: the cached JWKS predates ``rsa-2``; a refresh publishes it.
    source = FakeKeySource(RSA_KEY, published=[RSA_KEY, ROTATED_KEY])

    verified = await verifier(source).verify(mint(ROTATED_KEY))

    assert verified.subject == "subject-1"
    assert source.refreshes == 1


@pytest.mark.anyio
async def test_unknown_kid_after_refresh_is_rejected() -> None:
    source = FakeKeySource(RSA_KEY)

    with pytest.raises(InvalidBearerToken):
        await verifier(source).verify(mint(ROTATED_KEY))
    assert source.refreshes == 1


@pytest.mark.anyio
async def test_rejects_a_token_without_a_kid() -> None:
    token = jwt.encode({"alg": "RS256"}, claims(), RSA_KEY, algorithms=["RS256"])

    with pytest.raises(InvalidBearerToken):
        await verifier().verify(token)


@pytest.mark.anyio
async def test_rejects_a_key_marked_for_encryption() -> None:
    source = FakeKeySource()
    source.served.append({**public(RSA_KEY), "use": "enc"})

    with pytest.raises(InvalidBearerToken):
        await verifier(source).verify(mint())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "token",
    ["", "garbage", "a.b", "a.b.c", "!!!.???.***", "x" * 20_000, "a.b.c.d.e"],
)
async def test_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(InvalidBearerToken):
        await verifier().verify(token)


def test_verifier_requires_issuer_and_audience() -> None:
    with pytest.raises(ValueError, match="required"):
        AccessTokenVerifier(issuer=ISSUER, audience="", key_source=FakeKeySource())


# --------------------------------------------------------------------------- #
# BearerAuthenticator: token -> the cookie flow's AuthIdentity
# --------------------------------------------------------------------------- #


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def authenticator(
    userinfo: FakeUserInfo, *, clock: Clock | None = None, now: int = NOW
) -> BearerAuthenticator:
    return BearerAuthenticator(
        verifier=AccessTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            key_source=FakeKeySource(RSA_KEY),
            now=lambda: now,
        ),
        userinfo=userinfo,
        identity_ttl=300,
        clock=clock or Clock(),
    )


@pytest.mark.anyio
async def test_maps_userinfo_to_the_cookie_flow_identity() -> None:
    identity = (
        await authenticator(FakeUserInfo(USERINFO)).authenticate(mint())
    ).identity

    assert identity == AuthIdentity(
        issuer=ISSUER,
        subject="subject-1",
        username="dev",
        display_name="Dev User",
        email="dev@example.com",
    )


@pytest.mark.anyio
async def test_drops_an_unverified_userinfo_email() -> None:
    userinfo = FakeUserInfo(
        {**USERINFO, "email": "x@mattjmcnaughton.com", "email_verified": False}
    )

    identity = (await authenticator(userinfo).authenticate(mint())).identity

    assert identity.email is None


@pytest.mark.anyio
async def test_identity_claims_never_come_from_the_access_token_body() -> None:
    # Only the verified ``iss``/``sub`` are taken from the token; an ``email``
    # claim riding in it (absent from userinfo) must not become identity data.
    userinfo = FakeUserInfo({"sub": "subject-1", "preferred_username": "dev"})

    authenticated = await authenticator(userinfo).authenticate(
        mint(email="admin@mattjmcnaughton.com", email_verified=True)
    )
    identity = authenticated.identity

    assert identity.email is None


@pytest.mark.anyio
async def test_rejects_a_userinfo_subject_mismatch() -> None:
    userinfo = FakeUserInfo({**USERINFO, "sub": "someone-else"})

    with pytest.raises(InvalidBearerToken):
        await authenticator(userinfo).authenticate(mint())


@pytest.mark.anyio
async def test_caches_identity_per_subject_but_still_verifies_every_token() -> None:
    userinfo = FakeUserInfo(USERINFO)
    clock = Clock()
    auth = authenticator(userinfo, clock=clock)

    await auth.authenticate(mint())
    await auth.authenticate(mint(jti="second"))
    assert userinfo.calls == 1

    # A cached subject does not make an invalid token acceptable.
    with pytest.raises(InvalidBearerToken):
        await auth.authenticate(mint(exp=NOW - 3600))

    clock.now += 301
    await auth.authenticate(mint())
    assert userinfo.calls == 2


# --- the token's client id, for the ``client`` event dimension (TDD §12) ---- #


@pytest.mark.anyio
async def test_returns_the_tokens_azp_as_its_client_id() -> None:
    result = await authenticator(FakeUserInfo(USERINFO)).authenticate(mint())

    assert result.client_id == "aleph-ios"


@pytest.mark.anyio
async def test_falls_back_to_client_id_when_there_is_no_azp() -> None:
    result = await authenticator(FakeUserInfo(USERINFO)).authenticate(
        mint(azp=None, client_id="some-native-app")
    )

    assert result.client_id == "some-native-app"


@pytest.mark.anyio
async def test_azp_wins_over_client_id() -> None:
    result = await authenticator(FakeUserInfo(USERINFO)).authenticate(
        mint(azp="aleph-ios", client_id="something-else")
    )

    assert result.client_id == "aleph-ios"


@pytest.mark.anyio
@pytest.mark.parametrize("value", [None, "", 42])
async def test_a_missing_or_unusable_client_id_is_none(value: object) -> None:
    result = await authenticator(FakeUserInfo(USERINFO)).authenticate(mint(azp=value))

    assert result.client_id is None


@pytest.mark.anyio
async def test_the_client_id_comes_from_each_token_not_the_identity_cache() -> None:
    # The identity is cached per subject; the client id is per token, so one
    # learner on two clients is attributed to each correctly.
    userinfo = FakeUserInfo(USERINFO)
    auth = authenticator(userinfo)

    first = await auth.authenticate(mint(azp="aleph-ios"))
    second = await auth.authenticate(mint(azp="another-client", jti="second"))

    assert userinfo.calls == 1
    assert (first.client_id, second.client_id) == ("aleph-ios", "another-client")


@pytest.mark.anyio
async def test_an_invalid_token_never_reaches_userinfo() -> None:
    userinfo = FakeUserInfo(USERINFO)

    with pytest.raises(InvalidBearerToken):
        await authenticator(userinfo).authenticate(mint(IMPOSTER_KEY))
    assert userinfo.calls == 0


class GatedUserInfo(FakeUserInfo):
    """Userinfo that blocks until released, so concurrent callers overlap."""

    def __init__(self, claims: dict[str, Any], *, fail_first: bool = False) -> None:
        super().__init__(claims)
        self.release = asyncio.Event()
        self.fail_first = fail_first

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        self.calls += 1
        call = self.calls
        await self.release.wait()
        if self.fail_first and call == 1:
            raise IdentityProviderUnavailable("provider hiccup")
        return self.claims


@pytest.mark.anyio
async def test_concurrent_cache_misses_for_one_subject_call_userinfo_once() -> None:
    # An app launch fires several requests at once; the provider's per-user
    # userinfo rate limit must see one call, not one per request.
    userinfo = GatedUserInfo(USERINFO)
    auth = authenticator(userinfo)

    pending = asyncio.gather(*(auth.authenticate(mint(jti=str(n))) for n in range(5)))
    await asyncio.sleep(0)
    userinfo.release.set()
    results = await pending

    assert userinfo.calls == 1
    assert {result.identity.subject for result in results} == {"subject-1"}


@pytest.mark.anyio
async def test_a_failed_userinfo_call_does_not_fail_waiting_requests() -> None:
    # Each request carries its own valid token: a follower retries with its own
    # rather than inheriting the leader's failure.
    userinfo = GatedUserInfo(USERINFO, fail_first=True)
    auth = authenticator(userinfo)

    pending = asyncio.gather(
        auth.authenticate(mint()),
        auth.authenticate(mint(jti="second")),
        return_exceptions=True,
    )
    await asyncio.sleep(0)
    userinfo.release.set()
    first, second = await pending

    assert isinstance(first, IdentityProviderUnavailable)
    assert isinstance(second, BearerAuthentication)
    assert userinfo.calls == 2


# --------------------------------------------------------------------------- #
# OIDCProviderClient over an in-memory HTTP transport
# --------------------------------------------------------------------------- #


class FakeProvider:
    """An in-memory OIDC provider: discovery, JWKS and userinfo."""

    def __init__(self) -> None:
        self.keys = [public(RSA_KEY)]
        self.jwks_fetches = 0
        self.userinfo_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": "https://issuer.example.test/jwks",
                    "userinfo_endpoint": "https://issuer.example.test/userinfo",
                },
            )
        if path == "/jwks":
            self.jwks_fetches += 1
            return httpx.Response(200, json={"keys": self.keys})
        if path == "/userinfo":
            assert request.headers["authorization"] == "Bearer the-token"
            return httpx.Response(self.userinfo_status, json=USERINFO)
        return httpx.Response(404)


def provider_client(provider: FakeProvider, clock: Clock) -> OIDCProviderClient:
    return OIDCProviderClient(
        "https://issuer.example.test/.well-known/openid-configuration",
        transport=httpx.MockTransport(provider.handler),
        min_refresh_interval=30,
        clock=clock,
    )


@pytest.mark.anyio
async def test_provider_client_discovers_and_caches_the_jwks() -> None:
    provider = FakeProvider()
    clock = Clock()
    client = provider_client(provider, clock)

    assert await client.get_keys() == [public(RSA_KEY)]
    await client.get_keys()
    assert provider.jwks_fetches == 1


@pytest.mark.anyio
async def test_provider_client_throttles_refreshes() -> None:
    provider = FakeProvider()
    clock = Clock()
    client = provider_client(provider, clock)
    await client.get_keys()

    provider.keys = [public(RSA_KEY), public(ROTATED_KEY)]
    # Inside the throttle window a refresh serves the cached set.
    assert len(await client.get_keys(refresh=True)) == 1
    clock.now += 31
    assert len(await client.get_keys(refresh=True)) == 2
    assert provider.jwks_fetches == 2


@pytest.mark.anyio
async def test_provider_client_fetches_userinfo() -> None:
    client = provider_client(FakeProvider(), Clock())

    assert await client.fetch_userinfo("the-token") == USERINFO


@pytest.mark.anyio
async def test_provider_client_maps_a_userinfo_rejection_to_invalid_token() -> None:
    provider = FakeProvider()
    provider.userinfo_status = 401

    with pytest.raises(InvalidBearerToken):
        await provider_client(provider, Clock()).fetch_userinfo("the-token")


@pytest.mark.anyio
async def test_provider_client_maps_an_outage_to_unavailable() -> None:
    provider = FakeProvider()
    provider.userinfo_status = 502

    with pytest.raises(IdentityProviderUnavailable):
        await provider_client(provider, Clock()).fetch_userinfo("the-token")
