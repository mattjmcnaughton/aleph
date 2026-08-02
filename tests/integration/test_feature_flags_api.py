"""Admin feature-flag API + session delivery (real app, real Postgres).

AL-203 (epic #82, owner amendment 1). A dark flag defaults **off** globally and
**on for admins**, so a phase can merge and deploy with zero learner exposure
while admins dogfood in production. Phase 2's ``tutor`` and Phase 2B's
``shaping`` (AL-301, epic #114) shipped that way, so every resolved map here
carries both keys and the assertions read them together. This module is the
contract test for that story end to end:

* the admin-only override API (403 / 404 / upsert / idempotent delete),
* the resolved map delivered on ``GET /api/v1/auth/session`` (the only surface a
  regular learner ever sees — they never call the admin routes),
* the ``ON DELETE CASCADE`` that keeps ``user_feature_overrides`` orphan-free,
* the launch rehearsal: flipping ``FEATURE_FLAG_DEFAULTS`` reaches learners
  without a code deploy (AL-270), and a per-user override still wins over it.

Auth is the real cookie flow with a stubbed OIDC code exchange (mirroring
``test_auth_api`` / ``test_paths_api``), so the admin gate is genuine — admin
status is derived from the email domain (``authz.is_admin``), never stored.

**Both ``tutor`` and ``shaping`` are now launched and default on** (AL-270,
AL-370), which would make most of the resolution machinery below untestable — a
flag that is on for everyone cannot demonstrate an admin baseline, an override
that flips a learner *on*, or a `404` gate. So ``dark_flag_defaults`` (autouse)
puts both code defaults back to ``False`` for this module: every test here is
about the machinery, and the machinery's interesting case is a dark flag. The
launched defaults get their own test, ``test_launched_flags_reach_a_plain_learner``,
which opts back out.

**Phase 5's ``streaks`` (D7) is registered but still ships dark** — its code
default is already ``False`` and it needs no forcing, so it is deliberately
left out of ``dark_flag_defaults``. Every resolved map and every admin listing
below still carries it (the session/list surfaces the whole registry, not just
the flags a given test is about), which is what proves adding a third flag
never widens what a plain learner sees.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.config import settings
from aleph.models import User, UserFeatureOverride
from aleph.services import feature_flags

if TYPE_CHECKING:
    from fastapi import FastAPI

FLAGS_URL = "/api/v1/admin/feature-flags"
SESSION_URL = "/api/v1/auth/session"
TUTOR = "tutor"
# Phase 2B's flag (AL-301), registered alongside ``tutor`` and shipping dark the
# same way. It is in every resolved map below because the session carries the
# whole registry, not just the flag a test is about.
SHAPING = "shaping"
# Phase 5's flag (D7), registered the same way but not yet launched — see the
# module docstring. Also in every resolved map below, for the same reason.
STREAKS = "streaks"

LEARNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="flag-learner-subject",
    username="flag-learner",
    display_name="Flag Learner",
    email="learner@example.com",
)
# The email domain (``mattjmcnaughton.com``) is the default admin domain.
ADMIN = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="flag-admin-subject",
    username="flag-admin",
    display_name="Flag Admin",
    email="admin@mattjmcnaughton.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> uuid.UUID:
    """Complete the stubbed OIDC callback; returns the local account id."""
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303
    body = (await client.get(SESSION_URL)).json()
    return uuid.UUID(body["user"]["id"])


@pytest.fixture(autouse=True)
def dark_flag_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force both code defaults back to ``False`` for this module.

    See the module docstring: ``tutor`` and ``shaping`` are launched and default
    on, but every test here is about the *resolution machinery*, whose whole
    subject matter is a flag that is not simply on for everyone. Patching
    ``FLAG_DEFAULTS`` (rather than the settings map) is deliberate — the settings
    map is step 2 of the resolution order and would outrank the admin baseline
    these tests exist to exercise.
    """
    for flag in (feature_flags.FeatureFlag.TUTOR, feature_flags.FeatureFlag.SHAPING):
        monkeypatch.setitem(feature_flags.FLAG_DEFAULTS, flag, False)


async def _flags_on_session(client: AsyncClient) -> dict[str, bool]:
    response = await client.get(SESSION_URL)
    assert response.status_code == 200, response.text
    return response.json()["user"]["feature_flags"]


def _resolved(*, tutor: bool, shaping: bool, streaks: bool = False) -> dict[str, bool]:
    """The full resolved map — the session carries **every** registered flag.

    Spelled as a helper so a new flag joining the registry is one edit here
    rather than one per assertion, while the assertions stay exact: an extra key
    leaking into a learner's map (a stale override row, say) still fails.
    ``streaks`` defaults to ``False`` because none of this module's tests
    override or dogfood it — it is here purely to prove the third registered
    flag never leaks a different value than its own resolution would give.
    """
    return {TUTOR: tutor, SHAPING: shaping, STREAKS: streaks}


def _flag_row(key: str, *, enabled_default: bool, override_count: int = 0) -> dict:
    """One row of ``GET /api/v1/admin/feature-flags`` (the list is sorted by key)."""
    return {
        "key": key,
        "enabled_default": enabled_default,
        "override_count": override_count,
    }


async def _override_count() -> int:
    async with db.async_session() as session:
        return (
            await session.scalar(select(func.count()).select_from(UserFeatureOverride))
            or 0
        )


# --------------------------------------------------------------------------- #
# The admin gate
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_flag_routes_require_an_admin(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        target = f"{FLAGS_URL}/{TUTOR}/users/{uuid.uuid4()}"
        # Signed out is 401 (the shared auth gate), not 403.
        assert (await client.get(FLAGS_URL)).status_code == 401

        await _sign_in(client, monkeypatch, LEARNER)
        assert (await client.get(FLAGS_URL)).status_code == 403
        assert (await client.put(target, json={"enabled": True})).status_code == 403
        assert (await client.delete(target)).status_code == 403


@pytest.mark.anyio
async def test_admin_lists_every_registered_flag(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, ADMIN)

        response = await client.get(FLAGS_URL)

        assert response.status_code == 200, response.text
        # ``enabled_default`` is the *global* default — the admin baseline is a
        # resolution-time concern, not a property of the flag.
        assert response.json() == {
            "flags": [
                _flag_row(SHAPING, enabled_default=False),
                _flag_row(STREAKS, enabled_default=False),
                _flag_row(TUTOR, enabled_default=False),
            ]
        }


@pytest.mark.anyio
async def test_unknown_flag_or_user_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        admin_id = await _sign_in(client, monkeypatch, ADMIN)

        unknown_flag = f"{FLAGS_URL}/not_a_flag/users/{admin_id}"
        assert (await client.put(unknown_flag, json={"enabled": True})).status_code == (
            404
        )
        assert (await client.delete(unknown_flag)).status_code == 404

        unknown_user = f"{FLAGS_URL}/{TUTOR}/users/{uuid.uuid4()}"
        assert (await client.put(unknown_user, json={"enabled": True})).status_code == (
            404
        )
        # Nothing was written for the missing user.
        assert await _override_count() == 0


# --------------------------------------------------------------------------- #
# Session delivery + the override round trip
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_launched_flags_reach_a_plain_learner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launched posture (AL-270, AL-370): on for everyone, nothing configured.

    Opts back out of ``dark_flag_defaults`` by restoring the real registry
    values, so this is the one test in the module reading the defaults the
    codebase actually ships. A plain learner — no admin domain, no override row,
    no ``FEATURE_FLAG_DEFAULTS`` — gets both surfaces, which is what makes a
    fresh clone and a local ``just dev`` show the real product.
    """
    for flag in (feature_flags.FeatureFlag.TUTOR, feature_flags.FeatureFlag.SHAPING):
        monkeypatch.setitem(feature_flags.FLAG_DEFAULTS, flag, True)
    assert not settings.feature_flag_defaults, "nothing may be configured here"

    async with _client(app) as learner:
        await _sign_in(learner, monkeypatch, LEARNER)
        assert await _flags_on_session(learner) == _resolved(tutor=True, shaping=True)


@pytest.mark.anyio
async def test_dark_flags_resolve_on_for_admins(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Amendment 1: off for learners, on for admins, defaults untouched.

    Both dark phases at once — Phase 2's ``tutor`` (AL-203) and Phase 2B's
    ``shaping`` (AL-301) — because they are registered the same way and the
    story is the same one: merge and deploy with zero learner exposure while
    admins dogfood in production.
    """
    async with _client(app) as learner:
        await _sign_in(learner, monkeypatch, LEARNER)
        assert await _flags_on_session(learner) == _resolved(tutor=False, shaping=False)

    async with _client(app) as admin:
        await _sign_in(admin, monkeypatch, ADMIN)
        assert await _flags_on_session(admin) == _resolved(
            tutor=True, shaping=True, streaks=True
        )
        # The global defaults are still off — nothing was mutated to make the
        # admin's map true.
        listed = (await admin.get(FLAGS_URL)).json()["flags"]
        assert listed == [
            _flag_row(SHAPING, enabled_default=False),
            _flag_row(STREAKS, enabled_default=False),
            _flag_row(TUTOR, enabled_default=False),
        ]


@pytest.mark.anyio
async def test_override_flips_a_learner_on_and_clears_idempotently(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as learner, _client(app) as admin:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        admin_id = await _sign_in(admin, monkeypatch, ADMIN)
        target = f"{FLAGS_URL}/{TUTOR}/users/{learner_id}"

        assert await _flags_on_session(learner) == _resolved(tutor=False, shaping=False)

        response = await admin.put(target, json={"enabled": True})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "flag_key": TUTOR,
            "user_id": str(learner_id),
            "enabled": True,
        }
        # The override moves its own flag only; ``shaping`` stays at its default.
        assert await _flags_on_session(learner) == _resolved(tutor=True, shaping=False)

        # The override is one row, and it targets exactly one learner: the
        # admin's own map is unchanged.
        assert await _override_count() == 1
        assert (await admin.get(FLAGS_URL)).json()["flags"] == [
            _flag_row(SHAPING, enabled_default=False),
            _flag_row(STREAKS, enabled_default=False),
            _flag_row(TUTOR, enabled_default=False, override_count=1),
        ]

        # A repeat PUT updates in place rather than inserting a second row.
        assert (await admin.put(target, json={"enabled": False})).status_code == 200
        assert await _override_count() == 1
        assert await _flags_on_session(learner) == _resolved(tutor=False, shaping=False)

        # An override wins for admins too — including over the admin baseline,
        # which still holds for the flag the override says nothing about.
        admin_target = f"{FLAGS_URL}/{TUTOR}/users/{admin_id}"
        assert (await admin.put(admin_target, json={"enabled": False})).status_code == (
            200
        )
        assert await _flags_on_session(admin) == _resolved(
            tutor=False, shaping=True, streaks=True
        )

        # DELETE is idempotent: clearing an absent override is still a 204.
        assert (await admin.delete(target)).status_code == 204
        assert (await admin.delete(target)).status_code == 204
        assert await _flags_on_session(learner) == _resolved(tutor=False, shaping=False)
        assert (await admin.delete(admin_target)).status_code == 204
        assert await _flags_on_session(admin) == _resolved(
            tutor=True, shaping=True, streaks=True
        )
        assert await _override_count() == 0


@pytest.mark.anyio
async def test_settings_default_flips_the_flag_without_a_deploy(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AL-270's launch move, rehearsed: env flip on; a per-user override still wins."""
    async with _client(app) as learner, _client(app) as admin:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        await _sign_in(admin, monkeypatch, ADMIN)
        monkeypatch.setattr(settings, "feature_flag_defaults", f"{TUTOR}:on")

        # One flag flipped, the other left dark: the entry is per key, so
        # launching Phase 2 never launches Phase 2B by accident.
        assert await _flags_on_session(learner) == _resolved(tutor=True, shaping=False)
        assert (await admin.get(FLAGS_URL)).json()["flags"] == [
            _flag_row(SHAPING, enabled_default=False),
            _flag_row(STREAKS, enabled_default=False),
            _flag_row(TUTOR, enabled_default=True),
        ]

        target = f"{FLAGS_URL}/{TUTOR}/users/{learner_id}"
        assert (await admin.put(target, json={"enabled": False})).status_code == 200
        assert await _flags_on_session(learner) == _resolved(tutor=False, shaping=False)


@pytest.mark.anyio
async def test_tutor_flag_enabled_fixture_opens_the_surface_for_a_learner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tutor_flag_enabled: None
) -> None:
    """The shared fixture later tutor tickets gate their coverage on.

    AL-221/AL-220/AL-240 drive the tutor surface as an ordinary learner; without
    this they would be testing the flag gate. Pinned here so a change to the
    resolution order cannot silently leave those suites testing a 404.
    """
    async with _client(app) as learner:
        await _sign_in(learner, monkeypatch, LEARNER)

        assert await _flags_on_session(learner) == _resolved(tutor=True, shaping=False)


@pytest.mark.anyio
async def test_shaping_flag_enabled_fixture_opens_the_surface_for_a_learner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, shaping_flag_enabled: None
) -> None:
    """The Phase 2B twin, for the tickets that drive shaping as a learner.

    AL-320/AL-321/AL-340 gate their coverage on this fixture; without it they
    would be testing the 404 the flag gate returns rather than the shaping
    surface. It moves ``shaping`` alone — the tutor stays dark, which is what
    proves the two phases launch independently.
    """
    async with _client(app) as learner:
        await _sign_in(learner, monkeypatch, LEARNER)

        assert await _flags_on_session(learner) == _resolved(tutor=False, shaping=True)


@pytest.mark.anyio
async def test_both_flag_fixtures_compose(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tutor_flag_enabled: None,
    shaping_flag_enabled: None,
) -> None:
    """Requesting both fixtures turns both flags on.

    They mutate one setting (``FEATURE_FLAG_DEFAULTS``), so the additive helper
    behind them is load-bearing: a plain assignment would leave whichever fixture
    ran first silently undone, and the suite that needs both surfaces would meet
    a 404 it could not explain.
    """
    async with _client(app) as learner:
        await _sign_in(learner, monkeypatch, LEARNER)

        assert await _flags_on_session(learner) == _resolved(tutor=True, shaping=True)


@pytest.mark.anyio
async def test_stale_override_rows_are_ignored(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row for a flag no longer in code never leaks into a resolved map."""
    async with _client(app) as learner:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        async with db.async_session() as session:
            session.add(
                UserFeatureOverride(
                    user_id=learner_id, flag_key="removed_flag", enabled=True
                )
            )
            await session.commit()

        assert await _flags_on_session(learner) == _resolved(tutor=False, shaping=False)


@pytest.mark.anyio
async def test_deleting_a_user_cascades_their_overrides(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as learner, _client(app) as admin:
        learner_id = await _sign_in(learner, monkeypatch, LEARNER)
        await _sign_in(admin, monkeypatch, ADMIN)
        target = f"{FLAGS_URL}/{TUTOR}/users/{learner_id}"
        assert (await admin.put(target, json={"enabled": True})).status_code == 200
        assert await _override_count() == 1

        async with db.async_session() as session:
            user = await session.get(User, learner_id)
            assert user is not None
            await session.delete(user)
            await session.commit()

        assert await _override_count() == 0
