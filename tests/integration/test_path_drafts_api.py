"""Contract tests for the Path drafts API (CONTEXT.md: *Path draft*).

Real Postgres and the real cookie flow, with the deterministic stub model at the
model-resolution seam. Pins the round trip the New path page makes: draft from
an idea, one Interview round, a redraft with the answers. Nothing is created,
and a failed model run is an explicit error, never an empty ``200``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from aleph import db
from aleph.app import create_app
from aleph.auth import AuthIdentity
from aleph.models import Path
from aleph.services.path_draft import path_draft_service

from .conftest import stub_resolver

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models.function import AgentInfo

LEARNER = AuthIdentity(
    issuer="https://issuer.example.test",
    subject="draft-learner-subject",
    username="draft-learner",
    display_name="Draft Learner",
    email="draft-learner@example.com",
)
IDEA = "I want to understand how Postgres runs our slow queries at work."


class StubOAuthClient:
    async def authorize_access_token(self, _request: object) -> dict[str, str]:
        return {"access_token": "stub"}


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(path_draft_service, "_resolve_model", stub_resolver())
    monkeypatch.setattr(
        "aleph.routers.auth.oauth.create_client", lambda _p: StubOAuthClient()
    )
    monkeypatch.setattr("aleph.routers.auth.fetch_identity", lambda *_a: LEARNER)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as http:
        response = await http.get("/auth/callback", follow_redirects=False)
        assert response.status_code == 303, response.text
        yield http


@pytest.mark.anyio
async def test_draft_interview_and_redraft_round_trip(client: AsyncClient) -> None:
    first = await client.post("/api/v1/path-drafts", json={"idea": f"  {IDEA}  "})
    assert first.status_code == 200, first.text
    draft = first.json()
    assert set(draft) == {"topic", "level", "guidance", "teaching_style"}
    assert "Postgres" in draft["topic"]
    assert draft["level"] in {"new_to_it", "some_experience", "work_in_it"}

    asked = await client.post(
        "/api/v1/path-drafts/questions", json={"idea": IDEA, "draft": draft}
    )
    assert asked.status_code == 200, asked.text
    questions = asked.json()["questions"]
    assert 2 <= len(questions) <= 5
    for question in questions:
        assert set(question) == {"prompt", "why", "options", "multi"}
        assert 2 <= len(question["options"]) <= 4

    answers = [{"question": q["prompt"], "answer": q["options"][0]} for q in questions]
    redraft = await client.post(
        "/api/v1/path-drafts",
        json={"idea": IDEA, "answers": answers, "previous": draft},
    )
    assert redraft.status_code == 200, redraft.text
    sharpened = redraft.json()
    assert sharpened["topic"] != draft["topic"]
    assert set(sharpened["guidance"].splitlines()) - set(draft["guidance"].splitlines())

    more = await client.post(
        "/api/v1/path-drafts/questions",
        json={"idea": IDEA, "draft": sharpened, "answers": answers},
    )
    assert more.status_code == 200, more.text
    assert not {q["prompt"] for q in more.json()["questions"]} & {
        a["question"] for a in answers
    }

    # A draft is a proposal: nothing was created.
    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Path)) == 0


@pytest.mark.anyio
async def test_requests_are_validated_and_authenticated(client: AsyncClient) -> None:
    blank = await client.post("/api/v1/path-drafts", json={"idea": "   "})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "validation_error"
    too_many = await client.post(
        "/api/v1/path-drafts",
        json={"idea": IDEA, "answers": [{"question": "q", "answer": "a"}] * 21},
    )
    assert too_many.status_code == 422

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as anonymous:
        response = await anonymous.post("/api/v1/path-drafts", json={"idea": IDEA})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_a_provider_failure_is_an_error_not_an_empty_draft(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _down(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise RuntimeError("provider unavailable")

    broken = FunctionModel(_down)
    monkeypatch.setattr(path_draft_service, "_resolve_model", lambda _id: broken)

    drafted = await client.post("/api/v1/path-drafts", json={"idea": IDEA})
    assert drafted.status_code == 502
    assert drafted.json()["error"]["code"] == "draft_failed"

    draft = {"topic": "Postgres", "level": "some_experience"}
    asked = await client.post(
        "/api/v1/path-drafts/questions", json={"idea": IDEA, "draft": draft}
    )
    assert asked.status_code == 502
    assert asked.json()["error"]["code"] == "draft_failed"
