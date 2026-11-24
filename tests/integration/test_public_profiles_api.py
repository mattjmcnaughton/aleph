"""Public profiles against real Postgres (Public profiles TDD §9).

Two owners (A and C) and an anonymous visitor. Auth is the real cookie flow
with a stubbed OIDC exchange (the ``test_account_api`` pattern); content is
seeded directly so no generation runs except where a Copy is created.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.responses import RedirectResponse
from httpx import ASGITransport, AsyncClient

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.bearer import get_bearer_authenticator
from aleph.models import Lesson, LessonGenerationState, Level, Path, PathStatus, Unit
from aleph.repositories import BeatRepository, BriefRepository, NewSource
from aleph.services import generation as gen_module
from tests._bearer_fakes import USERINFO, fake_authenticator, mint

from .conftest import CollectingSpawn, _disable_flag_globally, stub_resolver

if TYPE_CHECKING:
    from fastapi import FastAPI

ORIGIN = "http://testserver"
A = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="owner-a",
    username="owner-a",
    display_name="Matt McNaughton",
    email="a@example.com",
)
C = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="owner-c",
    username="owner-c",
    display_name="Cara Owner",
    email="c@example.com",
)
B = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="visitor-b",
    username="visitor-b",
    display_name="Bea Visitor",
    email="b@example.com",
)


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}

    async def authorize_redirect(self, _request: object, _url: str) -> RedirectResponse:
        return RedirectResponse("https://idp.example.test/authorize", status_code=302)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from .conftest import _enable_flag_globally

    _enable_flag_globally(monkeypatch, "public_profiles")
    return create_app()


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    collector = CollectingSpawn()
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", stub_resolver()
    )
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    return collector


def _client(app: FastAPI, *, origin: bool = True) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN} if origin else {},
    )


async def _sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, identity: AuthIdentity
) -> uuid.UUID:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: identity)
    response = await client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 303, response.text
    session = await client.get("/api/v1/auth/session")
    return uuid.UUID(session.json()["user"]["id"])


async def _publish(client: AsyncClient, handle: str, *, activity: bool = True) -> None:
    response = await client.put(
        "/api/v1/account/public-profile",
        json={
            "handle": handle,
            "profile_public": True,
            "profile_show_activity": activity,
        },
    )
    assert response.status_code == 200, response.text


async def _seed_path(user_id: uuid.UUID, topic: str, *, done: int = 1) -> uuid.UUID:
    async with db.async_session() as session:
        path = Path(
            user_id=user_id,
            topic=topic,
            level=Level.SOME_EXPERIENCE,
            status=PathStatus.READY,
            guidance="I write Go at work.",
            teaching_style="Short passages.",
            search_outline=True,
        )
        session.add(path)
        await session.flush()
        unit = Unit(path=path, position=1, title="Ownership", summary="s")
        session.add(unit)
        await session.flush()
        for position in range(1, 4):
            session.add(
                Lesson(
                    unit_id=unit.id,
                    path_id=path.id,
                    position_in_path=position,
                    position_in_unit=position,
                    title=f"Lesson {position}",
                    generation_state=LessonGenerationState.GENERATED,
                    read_passage="SECRET PASSAGE BODY",
                    completed_at=datetime.now(UTC) if position <= done else None,
                )
            )
        await session.commit()
        return path.id


async def _seed_beat(user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A Beat with one published Brief and one Skipped entry."""
    async with db.async_session() as session:
        beat = await BeatRepository(session).create(
            user_id=user_id,
            topic="AI infrastructure",
            level=Level.WORK_IN_IT,
            anchor_weekday=0,
            guidance="Focus on inference.",
        )
        briefs = BriefRepository(session)
        published = await briefs.create_published(
            beat_id=beat.id,
            number=1,
            published_at=datetime(2026, 11, 2, 9, tzinfo=UTC),
            published_on=date(2026, 11, 2),
            title="Where the GPUs are",
            body_markdown="Body of the Brief.",
            claims=["c"],
            sources=[
                NewSource(
                    url="https://example.com/a",
                    publisher="Example",
                    title="A source",
                    published_on=date(2026, 11, 1),
                )
            ],
        )
        skipped = await briefs.create_skipped(
            beat_id=beat.id,
            published_at=datetime(2026, 11, 9, 9, tzinfo=UTC),
            published_on=date(2026, 11, 9),
            skip_line="Nothing material.",
        )
        await session.commit()
        return beat.id, published.id, skipped.id


def _body(response) -> dict:  # noqa: ANN001
    """The 404 envelope minus its per-request id, for uniformity checks."""
    error = dict(response.json()["error"])
    error.pop("request_id")
    return error


NOT_FOUND = {"code": "not_found", "message": "not found"}


# --------------------------------------------------------------------------- #
# The chain (D4) and uniform absence (D5)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_only_the_whole_chain_reads_and_every_absence_looks_the_same(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a, _client(app) as c, _client(app) as anon:
        a_id = await _sign_in(a, monkeypatch, A)
        c_id = await _sign_in(c, monkeypatch, C)
        await _publish(a, "matt")
        await _publish(c, "cara")
        shared = await _seed_path(a_id, "Rust ownership")
        private = await _seed_path(a_id, "Private topic")
        c_shared = await _seed_path(c_id, "C's path")
        beat, brief, skipped = await _seed_beat(a_id)
        unshared_beat, unshared_brief, _ = await _seed_beat(a_id)
        for url in (
            f"/api/v1/paths/{shared}/shared",
            f"/api/v1/beats/{beat}/shared",
        ):
            assert (await a.put(url, json={"shared": True})).status_code == 200
        assert (
            await c.put(f"/api/v1/paths/{c_shared}/shared", json={"shared": True})
        ).status_code == 200

        base = "/api/v1/public/profiles"
        readable = [
            f"{base}/matt",
            f"{base}/matt/paths/{shared}",
            f"{base}/matt/beats/{beat}",
            f"{base}/matt/beats/{beat}/briefs/{brief}",
        ]
        for url in readable:
            assert (await anon.get(url)).status_code == 200, url

        absent = [
            f"{base}/nobody",
            f"{base}/MATT",
            f"{base}/admin",
            f"{base}/matt/paths/{private}",
            f"{base}/matt/paths/{c_shared}",
            f"{base}/cara/paths/{shared}",
            f"{base}/matt/paths/{uuid.uuid4()}",
            f"{base}/matt/beats/{unshared_beat}",
            f"{base}/matt/beats/{unshared_beat}/briefs/{unshared_brief}",
            f"{base}/matt/beats/{beat}/briefs/{unshared_brief}",
            f"{base}/matt/beats/{beat}/briefs/{skipped}",
            f"{base}/cara/beats/{beat}/briefs/{brief}",
        ]
        for url in absent:
            response = await anon.get(url)
            assert response.status_code == 404, url
            assert _body(response) == NOT_FOUND, url


@pytest.mark.anyio
async def test_the_profile_shows_only_shared_items_and_no_private_fields(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a, _client(app) as anon:
        a_id = await _sign_in(a, monkeypatch, A)
        await _publish(a, "matt")
        shared = await _seed_path(a_id, "Rust ownership", done=1)
        private = await _seed_path(a_id, "Private topic")
        beat, brief, _ = await _seed_beat(a_id)
        await a.put(f"/api/v1/paths/{shared}/shared", json={"shared": True})
        await a.put(f"/api/v1/beats/{beat}/shared", json={"shared": True})

        profile = (await anon.get("/api/v1/public/profiles/matt")).json()
        assert profile["display_name"] == "Matt McNaughton"
        assert [p["id"] for p in profile["paths"]] == [str(shared)]
        assert profile["paths"][0] | {"current_streak": 0} == {
            "id": str(shared),
            "title": "Rust ownership",
            "level": "some_experience",
            "done": 1,
            "total": 3,
            "current_streak": 0,
        }
        assert profile["beats"][0]["latest_brief_title"] == "Where the GPUs are"
        assert profile["beats"][0]["brief_count"] == 1
        assert profile["activity"]["current_streak"] >= 1
        assert len(profile["activity"]["activity"]) == 49

        text = (await anon.get("/api/v1/public/profiles/matt")).text
        for leaked in (str(private), str(a_id), "a@example.com", "SECRET PASSAGE"):
            assert leaked not in text

        path = (await anon.get(f"/api/v1/public/profiles/matt/paths/{shared}")).json()
        assert path["guidance"] == "I write Go at work."
        assert path["teaching_style"] == "Short passages."
        assert path["units"][0]["lessons"] == [
            {"title": "Lesson 1", "complete": True, "current": False},
            {"title": "Lesson 2", "complete": False, "current": True},
            {"title": "Lesson 3", "complete": False, "current": False},
        ]
        assert "search_outline" not in path

        detail = await anon.get(
            f"/api/v1/public/profiles/matt/beats/{beat}/briefs/{brief}"
        )
        assert detail.json()["sources"][0]["url"] == "https://example.com/a"

        # The activity strip has its own switch.
        await _publish(a, "matt", activity=False)
        hidden = (await anon.get("/api/v1/public/profiles/matt")).json()
        assert hidden["activity"] is None


@pytest.mark.anyio
async def test_a_public_brief_read_never_marks_it_read(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a:
        a_id = await _sign_in(a, monkeypatch, A)
        await _publish(a, "matt")
        beat, brief, _ = await _seed_beat(a_id)
        await a.put(f"/api/v1/beats/{beat}/shared", json={"shared": True})
        # Even the owner, signed in, reading their own Brief publicly.
        await a.get(f"/api/v1/public/profiles/matt/beats/{beat}/briefs/{brief}")
        mine = (await a.get(f"/api/v1/beats/{beat}")).json()
        [entry] = [e for e in mine["entries"] if e["id"] == str(brief)]
        assert entry["read_at"] is None


@pytest.mark.anyio
async def test_the_brief_list_is_paginated(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a, _client(app) as anon:
        a_id = await _sign_in(a, monkeypatch, A)
        await _publish(a, "matt")
        beat, _, _ = await _seed_beat(a_id)
        async with db.async_session() as session:
            for number in range(2, 26):
                await BriefRepository(session).create_published(
                    beat_id=beat,
                    number=number,
                    published_at=datetime(2026, 1, 1, tzinfo=UTC),
                    published_on=date(2026, 1, 1),
                    title=f"Brief {number}",
                    body_markdown="b",
                    claims=[],
                    sources=[],
                )
            await session.commit()
        await a.put(f"/api/v1/beats/{beat}/shared", json={"shared": True})

        url = f"/api/v1/public/profiles/matt/beats/{beat}"
        first = (await anon.get(url)).json()
        assert [b["number"] for b in first["briefs"]] == list(range(25, 5, -1))
        assert first["next_cursor"] == 6
        second = (await anon.get(url, params={"cursor": 6})).json()
        assert [b["number"] for b in second["briefs"]] == [5, 4, 3, 2, 1]
        assert second["next_cursor"] is None


# --------------------------------------------------------------------------- #
# Withdrawal closes direct and nested reads on the very next request
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("withdraw", ["unshare", "profile_off", "delete", "flag_off"])
@pytest.mark.anyio
async def test_withdrawing_closes_every_read_immediately(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, withdraw: str
) -> None:
    # The release gate (security review): two owners and an anonymous visitor;
    # withdrawal closes direct and nested reads, and nothing served before it
    # was cacheable or carried a cookie, so no cache can replay it.
    async with _client(app) as a, _client(app) as c, _client(app) as anon:
        a_id = await _sign_in(a, monkeypatch, A)
        c_id = await _sign_in(c, monkeypatch, C)
        await _publish(a, "matt")
        await _publish(c, "cara")
        path = await _seed_path(a_id, "Rust ownership")
        beat, brief, _ = await _seed_beat(a_id)
        c_path = await _seed_path(c_id, "C's path")
        await a.put(f"/api/v1/paths/{path}/shared", json={"shared": True})
        await a.put(f"/api/v1/beats/{beat}/shared", json={"shared": True})
        await c.put(f"/api/v1/paths/{c_path}/shared", json={"shared": True})
        urls = [
            f"/api/v1/public/profiles/matt/paths/{path}",
            f"/api/v1/public/profiles/matt/beats/{beat}",
            f"/api/v1/public/profiles/matt/beats/{beat}/briefs/{brief}",
        ]
        c_url = f"/api/v1/public/profiles/cara/paths/{c_path}"
        for url in [*urls, c_url]:
            response = await anon.get(url)
            assert response.status_code == 200, url
            assert response.headers["cache-control"] == "no-store", url
            assert "set-cookie" not in response.headers, url

        if withdraw == "unshare":
            await a.put(f"/api/v1/paths/{path}/shared", json={"shared": False})
            await a.put(f"/api/v1/beats/{beat}/shared", json={"shared": False})
        elif withdraw == "profile_off":
            await a.put(
                "/api/v1/account/public-profile",
                json={
                    "handle": "matt",
                    "profile_public": False,
                    "profile_show_activity": True,
                },
            )
        elif withdraw == "delete":
            await a.delete(f"/api/v1/paths/{path}")
            await a.delete(f"/api/v1/beats/{beat}")
        else:
            _disable_flag_globally(monkeypatch, "public_profiles")

        for url in urls:
            assert (await anon.get(url)).status_code == 404, url
        profile = await anon.get("/api/v1/public/profiles/matt")
        if withdraw in ("profile_off", "flag_off"):
            assert profile.status_code == 404
        else:
            assert profile.json()["paths"] == profile.json()["beats"] == []
        # The other owner is untouched, unless the kill switch closed everyone.
        expected = 404 if withdraw == "flag_off" else 200
        assert (await anon.get(c_url)).status_code == expected


@pytest.mark.anyio
async def test_turning_the_profile_off_keeps_each_items_choice(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a, _client(app) as anon:
        a_id = await _sign_in(a, monkeypatch, A)
        await _publish(a, "matt")
        path = await _seed_path(a_id, "Rust ownership")
        await a.put(f"/api/v1/paths/{path}/shared", json={"shared": True})
        off = {"handle": "matt", "profile_public": False, "profile_show_activity": True}
        await a.put("/api/v1/account/public-profile", json=off)
        await _publish(a, "matt")
        url = f"/api/v1/public/profiles/matt/paths/{path}"
        assert (await anon.get(url)).status_code == 200


# --------------------------------------------------------------------------- #
# Caching, indexing and cookies (Constitution §1, SR 4)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_public_responses_are_uncacheable_unindexed_and_cookieless(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a, _client(app) as anon:
        a_id = await _sign_in(a, monkeypatch, A)
        await _publish(a, "matt")
        path = await _seed_path(a_id, "Rust ownership")
        await a.put(f"/api/v1/paths/{path}/shared", json={"shared": True})

        for client in (anon, a):
            for url in (
                "/api/v1/public/profiles/matt",
                f"/api/v1/public/profiles/matt/paths/{path}",
                "/api/v1/public/profiles/nobody",
            ):
                response = await client.get(url)
                assert response.headers["cache-control"] == "no-store", url
                assert response.headers["x-robots-tag"] == "noindex", url
                assert "set-cookie" not in response.headers, url

        # A signed-in visitor gets exactly what an anonymous one gets.
        signed_in = await a.get(f"/api/v1/public/profiles/matt/paths/{path}")
        anonymous = await anon.get(f"/api/v1/public/profiles/matt/paths/{path}")
        assert signed_in.json() == anonymous.json()

        probe = await a.get("/api/v1/auth/session")
        assert probe.headers["cache-control"] == "private, no-store"
        private_read = await a.get("/api/v1/paths")
        assert private_read.headers["cache-control"] == "no-store"
        assert (await anon.get("/api/v1/auth/session")).headers[
            "cache-control"
        ] == "private, no-store"


# --------------------------------------------------------------------------- #
# Owner settings: Handle rules (D8) and the Origin check (SR 5)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_handles_are_unique_validated_and_permanent(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a, _client(app) as c:
        await _sign_in(a, monkeypatch, A)
        await _sign_in(c, monkeypatch, C)
        url = "/api/v1/account/public-profile"

        initial = (await a.get(url)).json()
        assert initial == {
            "handle": None,
            "profile_public": False,
            "profile_show_activity": True,
        }
        no_handle = await a.put(
            url,
            json={
                "handle": None,
                "profile_public": True,
                "profile_show_activity": True,
            },
        )
        assert no_handle.json()["error"]["code"] == "handle_required"

        rejected = await a.put(
            url,
            json={
                "handle": "admin",
                "profile_public": True,
                "profile_show_activity": True,
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["details"] == {"reason": "reserved"}

        await _publish(a, "Matt")
        assert (await a.get(url)).json()["handle"] == "matt"

        taken = await c.put(
            url,
            json={
                "handle": "matt",
                "profile_public": True,
                "profile_show_activity": True,
            },
        )
        assert taken.status_code == 409
        assert taken.json()["error"]["code"] == "handle_taken"

        locked = await a.put(
            url,
            json={
                "handle": "other",
                "profile_public": True,
                "profile_show_activity": True,
            },
        )
        assert locked.status_code == 409
        assert locked.json()["error"]["code"] == "handle_locked"


@pytest.mark.anyio
async def test_owner_routes_hide_behind_the_flag(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_flag_globally(monkeypatch, "public_profiles")
    async with _client(app) as a:
        a_id = await _sign_in(a, monkeypatch, A)
        path = await _seed_path(a_id, "Rust ownership")
        assert (await a.get("/api/v1/account/public-profile")).status_code == 404
        put = await a.put(f"/api/v1/paths/{path}/shared", json={"shared": True})
        assert put.status_code == 404


@pytest.mark.anyio
async def test_sharing_another_learners_item_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as a, _client(app) as c:
        a_id = await _sign_in(a, monkeypatch, A)
        await _sign_in(c, monkeypatch, C)
        path = await _seed_path(a_id, "Rust ownership")
        beat, _, _ = await _seed_beat(a_id)
        assert (
            await c.put(f"/api/v1/paths/{path}/shared", json={"shared": True})
        ).status_code == 404
        assert (
            await c.put(f"/api/v1/beats/{beat}/shared", json={"shared": True})
        ).status_code == 404


@pytest.mark.anyio
async def test_cross_origin_cookie_writes_are_rejected_and_bearer_is_not(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"handle": "devuser", "profile_public": True, "profile_show_activity": True}
    url = "/api/v1/account/public-profile"
    async with _client(app, origin=False) as a:
        await _sign_in(a, monkeypatch, A)
        for headers in (
            {"Origin": "https://evil.example"},
            {"Referer": "https://evil.example/page"},
            {"Origin": "http://testserver.evil.example"},
            {},
        ):
            response = await a.put(url, json=body, headers=headers)
            assert response.status_code == 403, headers
        assert (await a.get(url)).json()["handle"] is None
        ok = await a.put(url, json=body, headers={"Referer": f"{ORIGIN}/settings"})
        assert ok.status_code == 200

    authenticator = fake_authenticator(USERINFO)
    app.dependency_overrides[get_bearer_authenticator] = lambda: authenticator
    async with _client(app, origin=False) as native:
        response = await native.put(
            url,
            json={**body, "handle": "native"},
            headers={"Authorization": f"Bearer {mint()}", "Origin": "https://x.test"},
        )
        assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# Copy credit (D7)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_copy_credit_is_kept_only_for_a_public_source(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, spawn: CollectingSpawn
) -> None:
    async with _client(app) as a, _client(app) as b:
        a_id = await _sign_in(a, monkeypatch, A)
        b_id = await _sign_in(b, monkeypatch, B)
        await _publish(a, "matt")
        shared = await _seed_path(a_id, "Rust ownership")
        private = await _seed_path(a_id, "Private topic")
        own = await _seed_path(b_id, "B's own path")
        await a.put(f"/api/v1/paths/{shared}/shared", json={"shared": True})

        async def create(source: object) -> dict:
            created = await b.post(
                "/api/v1/paths",
                json={"topic": "Rust", "level": "new_to_it", "source_path_id": source},
            )
            assert created.status_code == 202, created.text
            await spawn.drain()
            return (await b.get(f"/api/v1/paths/{created.json()['id']}")).json()

        for source in (str(private), str(own), str(uuid.uuid4())):
            assert (await create(source))["source"] is None

        copy = await create(str(shared))
        assert copy["source"] == {
            "display_name": "Matt McNaughton",
            "handle": "matt",
            "path_id": str(shared),
        }
        # Nothing but the credit came from the source.
        assert copy["topic"] == "Rust"
        assert copy["guidance"] is None
        assert copy["search_outline"] is False

        await a.put(f"/api/v1/paths/{shared}/shared", json={"shared": False})
        assert (await b.get(f"/api/v1/paths/{copy['id']}")).json()["source"] is None
        await a.put(f"/api/v1/paths/{shared}/shared", json={"shared": True})
        assert (await b.get(f"/api/v1/paths/{copy['id']}")).json()["source"]

        await a.delete(f"/api/v1/paths/{shared}")
        assert (await b.get(f"/api/v1/paths/{copy['id']}")).json()["source"] is None


@pytest.mark.anyio
async def test_beat_copy_credit_follows_the_same_rule(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aleph.services import briefing as briefing_module

    monkeypatch.setattr(briefing_module.briefing_service, "_spawn", CollectingSpawn())
    async with _client(app) as a, _client(app) as b:
        a_id = await _sign_in(a, monkeypatch, A)
        await _sign_in(b, monkeypatch, B)
        await _publish(a, "matt")
        beat, _, _ = await _seed_beat(a_id)
        payload = {"topic": "AI", "level": "new_to_it", "anchor_weekday": 2}

        unshared = await b.post(
            "/api/v1/beats", json={**payload, "source_beat_id": str(beat)}
        )
        assert unshared.status_code == 202, unshared.text
        assert unshared.json()["source"] is None

        await a.put(f"/api/v1/beats/{beat}/shared", json={"shared": True})
        copied = await b.post(
            "/api/v1/beats", json={**payload, "source_beat_id": str(beat)}
        )
        assert copied.json()["source"]["beat_id"] == str(beat)
        assert copied.json()["entries"] == []


# --------------------------------------------------------------------------- #
# The typed sign-in copy intent (§5, SR 6)
# --------------------------------------------------------------------------- #


async def _login_then_callback(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, query: str
) -> str:
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: B)
    login = await client.get(f"/auth/login{query}", follow_redirects=False)
    assert login.status_code == 302
    callback = await client.get("/auth/callback", follow_redirects=False)
    assert callback.status_code == 303
    return callback.headers["location"]


@pytest.mark.anyio
async def test_the_copy_intent_returns_to_the_form_once(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = uuid.uuid4()
    async with _client(app) as client:
        assert (
            await _login_then_callback(client, monkeypatch, f"?copy=path:matt:{item}")
        ) == f"/new?from=matt/{item}"
        # Single-use: the next sign-in without an intent goes home.
        assert await _login_then_callback(client, monkeypatch, "") == "/"
        beat = await _login_then_callback(
            client, monkeypatch, f"?copy=beat:matt:{item}"
        )
        assert beat == f"/beats/new?from=matt/{item}"


@pytest.mark.parametrize(
    "query",
    [
        "?next=https://evil.example",
        "?copy=https://evil.example",
        "?copy=path:matt:not-a-uuid",
        "?copy=lesson:matt:5b0f4c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f",
        "?copy=path:a:5b0f4c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f",
    ],
)
@pytest.mark.anyio
async def test_anything_but_a_valid_intent_signs_in_to_home(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    async with _client(app) as client:
        assert await _login_then_callback(client, monkeypatch, query) == "/"


@pytest.mark.anyio
async def test_a_failed_sign_in_drops_the_intent(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = uuid.uuid4()

    def fail(*_a: object) -> AuthIdentity:
        raise ValueError("missing claims")

    async with _client(app) as client:
        monkeypatch.setattr(
            "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
        )
        monkeypatch.setattr("aleph.routers.auth.fetch_identity", fail)
        await client.get(f"/auth/login?copy=path:matt:{item}", follow_redirects=False)
        failed = await client.get("/auth/callback", follow_redirects=False)
        assert failed.headers["location"] == "/login?error=auth_failed"
        assert await _login_then_callback(client, monkeypatch, "") == "/"
