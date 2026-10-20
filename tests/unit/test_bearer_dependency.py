"""Credential selection in the current-user dependency (``dependencies.py``).

An ``Authorization`` header, when present, alone decides the request: a bad
bearer token is ``401`` and never falls back to an ambient session cookie. With
no header the cookie path is unchanged. The database is an in-memory fake that
records every call, so "the cookie was never consulted" is a direct assertion.
The happy bearer path (token -> provisioned account) needs real Postgres and
lives in ``tests/integration/test_bearer_auth.py``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated, Any

import pytest
from fastapi import Depends, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from aleph import dependencies, events
from aleph.app import create_app
from aleph.bearer import (
    AccessTokenVerifier,
    BearerAuthenticator,
    IdentityProviderUnavailable,
    get_bearer_authenticator,
)
from aleph.config import Settings
from aleph.dependencies import bearer_client, bearer_token, get_current_user
from aleph.models import User
from tests._bearer_fakes import (
    AUDIENCE,
    IMPOSTER_KEY,
    ISSUER,
    NOW,
    RSA_KEY,
    FakeKeySource,
    fake_authenticator,
    mint,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_request_client() -> Iterator[None]:
    # The dependency binds ``request_client`` in the caller's context; a test
    # calling it directly must not leak that binding into the next test.
    token = events.request_client.set(None)
    yield
    events.request_client.reset(token)


COOKIE_USER = User(id=uuid.uuid4(), username="cookie", display_name="Cookie")


class FakeSession:
    """Just enough of ``AsyncSession`` for the cookie path: ``get`` by PK."""

    def __init__(self, *users: User) -> None:
        self.users = {user.id: user for user in users}
        self.calls: list[str] = []

    async def get(self, _model: type, user_id: uuid.UUID) -> User | None:
        self.calls.append("get")
        return self.users.get(user_id)

    async def commit(self) -> None:
        pass


def as_session(fake: FakeSession) -> AsyncSession:
    return fake  # ty: ignore[invalid-return-type]


def make_request(*headers: tuple[str, str], session: dict[str, Any]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/_authprobe",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
            "session": session,
        }
    )


def cookie_session() -> dict[str, Any]:
    return {"user_id": str(COOKIE_USER.id)}


@pytest.mark.anyio
async def test_no_header_keeps_the_cookie_path() -> None:
    db = FakeSession(COOKIE_USER)
    request = make_request(session=cookie_session())

    user = await dependencies.get_optional_current_user(
        request, as_session(db), fake_authenticator()
    )

    assert user is COOKIE_USER
    assert request.state.auth_method == "session"
    assert events.request_client.get() == "web"


@pytest.mark.anyio
async def test_no_header_and_no_cookie_is_anonymous() -> None:
    user = await dependencies.get_optional_current_user(
        make_request(session={}), as_session(FakeSession()), fake_authenticator()
    )

    assert user is None
    assert events.request_client.get() is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "token",
    [
        mint(IMPOSTER_KEY),  # bad signature
        mint(exp=NOW - 3600),  # expired
        mint(aud="somebody-else"),  # wrong audience
        "not-a-jwt",
    ],
)
async def test_invalid_bearer_never_falls_back_to_a_valid_cookie(token: str) -> None:
    db = FakeSession(COOKIE_USER)
    session = cookie_session()
    request = make_request(("Authorization", f"Bearer {token}"), session=session)

    with pytest.raises(HTTPException) as raised:
        await dependencies.get_optional_current_user(
            request, as_session(db), fake_authenticator()
        )

    assert raised.value.status_code == 401
    assert raised.value.headers == {"WWW-Authenticate": 'Bearer error="invalid_token"'}
    # Neither the cookie nor the database was consulted, and the session was
    # left exactly as it was (nothing cookie-specific happens for bearer).
    assert db.calls == []
    assert session == cookie_session()
    assert events.request_client.get() is None


@pytest.mark.anyio
async def test_bearer_is_rejected_when_bearer_auth_is_not_configured() -> None:
    request = make_request(("Authorization", f"Bearer {mint()}"), session={})

    with pytest.raises(HTTPException) as raised:
        await dependencies.get_optional_current_user(
            request, as_session(FakeSession()), None
        )

    assert raised.value.status_code == 401


@pytest.mark.anyio
async def test_provider_outage_is_503_not_anonymous() -> None:
    class DownUserInfo:
        async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
            raise IdentityProviderUnavailable("down")

    authenticator = BearerAuthenticator(
        verifier=AccessTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            key_source=FakeKeySource(RSA_KEY),
            now=lambda: NOW,
        ),
        userinfo=DownUserInfo(),
    )
    request = make_request(("Authorization", f"Bearer {mint()}"), session={})

    with pytest.raises(HTTPException) as raised:
        await dependencies.get_optional_current_user(
            request, as_session(FakeSession()), authenticator
        )

    assert raised.value.status_code == 503


@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", "Basic ZGV2OmRldg==")],
        [("Authorization", "Bearer")],
        [("Authorization", "Bearer    ")],
        [("Authorization", "Bearer a b")],
        [("Authorization", "")],
        [("Authorization", "Bearer a.b.c"), ("Authorization", "Bearer d.e.f")],
    ],
)
def test_malformed_authorization_headers_are_401(
    headers: list[tuple[str, str]],
) -> None:
    with pytest.raises(HTTPException) as raised:
        bearer_token(make_request(*headers, session={}))

    assert raised.value.status_code == 401


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_bearer_scheme_is_case_insensitive(scheme: str) -> None:
    request = make_request(("Authorization", f"{scheme} a.b.c"), session={})

    assert bearer_token(request) == "a.b.c"


def test_no_authorization_header_yields_no_token() -> None:
    assert bearer_token(make_request(session={})) is None


# --------------------------------------------------------------------------- #
# Through the real app: the shared error envelope, no cookie set
# --------------------------------------------------------------------------- #


@pytest.fixture
async def client() -> Any:
    app = create_app()

    @app.get("/api/v1/_authprobe")
    async def _authprobe(
        user: Annotated[User, Depends(get_current_user)],
    ) -> dict[str, str]:
        return {"id": str(user.id)}

    app.dependency_overrides[get_bearer_authenticator] = fake_authenticator
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/v1/_authprobe", "/api/v1/auth/session"])
async def test_invalid_bearer_is_an_enveloped_401(
    client: AsyncClient, path: str
) -> None:
    response = await client.get(
        path, headers={"Authorization": f"Bearer {mint(IMPOSTER_KEY)}"}
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    body = response.json()
    assert body["error"]["code"] == "unauthenticated"
    assert body["error"]["message"] == "invalid bearer token"
    assert "set-cookie" not in response.headers


def test_bearer_auth_is_off_by_default() -> None:
    assert Settings().oidc_api_audience == ""


def test_api_audience_must_not_equal_the_web_client_id() -> None:
    # The web client's ID tokens carry ``aud = OIDC_CLIENT_ID``; an API audience
    # equal to it would accept them as API access tokens.
    with pytest.raises(ValueError, match="OIDC_API_AUDIENCE"):
        Settings(oidc_client_id="aleph", oidc_api_audience="aleph")


# --- the ``client`` event dimension's lookup (TDD §12, D18) ------------------- #

LABELS = {"aleph-ios": "ios", "auth0-native-id": "ios"}


@pytest.mark.parametrize("client_id", ["aleph-ios", "auth0-native-id"])
def test_a_mapped_bearer_client_takes_its_label(client_id: str) -> None:
    assert bearer_client(client_id, LABELS) == "ios"


@pytest.mark.parametrize("client_id", ["aleph-api-test", "ALEPH-IOS", "", None])
def test_an_unmapped_or_missing_bearer_client_is_unknown(
    client_id: str | None,
) -> None:
    # Exact match only, and never a guess: an id the map does not name, or a
    # token naming no client at all, records as ``unknown``.
    assert bearer_client(client_id, LABELS) == "unknown"


def test_an_empty_map_makes_every_bearer_client_unknown() -> None:
    assert bearer_client("aleph-ios", {}) == "unknown"
