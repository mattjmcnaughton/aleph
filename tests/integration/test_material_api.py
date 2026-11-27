"""Linked Material through ``POST /api/v1/paths`` (docs/tdds/linked-material.md).

Real Postgres, real cookie auth, the stub model at the resolution seam and the
stub Material reader at the reading seam. Proves: the snapshot is stored with
the path, the outline's grounding plan lands on each lesson, a lesson's prompt
carries exactly its mapped sections, and a failed read creates no path.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from aleph import db
from aleph.models import Lesson, Material, MaterialSection, Path
from aleph.services import generation as gen_module
from aleph.services.material import StubMaterialReader
from aleph.services.stub_model import _stub_respond

from .conftest import CollectingSpawn, _disable_flag_globally, _enable_flag_globally
from .test_paths_api import OWNER, _client, _poll, _sign_in

if TYPE_CHECKING:
    from fastapi import FastAPI
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo

LONG_TEXT = "## Four key metrics\n" + "Deployment frequency and lead time. " * 40


@pytest.fixture
def app() -> FastAPI:
    from aleph.app import create_app

    return create_app()


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo):  # noqa: ANN202
        seen.append(
            "\n".join(
                part.content
                for message in messages
                for part in getattr(message, "parts", [])
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            )
        )
        return _stub_respond(messages, info)

    model = FunctionModel(respond)
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", lambda _id: model
    )
    monkeypatch.setattr(
        "aleph.routers.v1.paths.default_reader", lambda: StubMaterialReader()
    )
    return seen


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> CollectingSpawn:
    collector = CollectingSpawn()
    monkeypatch.setattr(gen_module.generation_orchestrator, "_spawn", collector)
    return collector


BODY = {
    "topic": "Delivery performance",
    "level": "some_experience",
    "material_mode": "teach",
    "material": [
        {"kind": "docs", "url": "https://docs.example.com/factories"},
        {"kind": "ai_summary", "title": "Accelerate summary", "text": LONG_TEXT},
    ],
}


@pytest.mark.anyio
async def test_teach_this_path_stores_its_snapshot_and_grounds_each_lesson(
    app: FastAPI,
    spawn: CollectingSpawn,
    prompts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag_globally(monkeypatch, "material")
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post("/api/v1/paths", json=BODY)
        assert resp.status_code == 202, resp.text
        path_id = resp.json()["id"]
        await spawn.drain()
        body = await _poll(client, spawn, path_id)

    assert body["status"] == "ready"
    assert body["material_mode"] == "teach"
    assert [(m["kind"], m["title"]) for m in body["materials"]] == [
        ("docs", "Factories: part 1 (4 pages)"),
        ("ai_summary", "Accelerate summary"),
    ]

    pid = uuid.UUID(path_id)
    async with db.async_session() as session:
        sections = (
            await session.scalars(
                select(MaterialSection)
                .where(MaterialSection.path_id == pid)
                .order_by(MaterialSection.position)
            )
        ).all()
        lessons = (
            await session.scalars(
                select(Lesson)
                .where(Lesson.path_id == pid)
                .order_by(Lesson.position_in_path)
            )
        ).all()
    # Sections are numbered 1..N across the whole path, docs pages first.
    assert [s.position for s in sections] == list(range(1, len(sections) + 1))
    assert sections[-1].title == "Four key metrics"
    # Teach this: every lesson names sections the path owns.
    owned = {s.position for s in sections}
    assert lessons
    assert all(lesson.material_sections for lesson in lessons)
    assert all(set(lesson.material_sections or []) <= owned for lesson in lessons)

    # The first lesson's prompt carries its mapped section, not the others.
    first, other = lessons[0], sections[-1]
    by_position = {s.position: s for s in sections}
    mapped = by_position[(first.material_sections or [0])[0]]
    lesson_prompt = next(p for p in prompts if "position_in_path=1" in p)
    assert mapped.text[:60] in lesson_prompt
    assert other.position not in (first.material_sections or [])
    assert other.text[:60] not in lesson_prompt


@pytest.mark.anyio
async def test_an_unreadable_item_fails_the_create_and_stores_nothing(
    app: FastAPI,
    spawn: CollectingSpawn,
    prompts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag_globally(monkeypatch, "material")
    body = {
        **BODY,
        "material": [
            {"kind": "article", "url": "https://ok.example.com/post"},
            {"kind": "article", "url": "https://unreadable.example.com/x"},
            {"kind": "article", "url": "https://www.youtube.com/watch?v=abc"},
        ],
    }
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post("/api/v1/paths", json=body)

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["details"]["reason"] == "material_unreadable"
    assert error["details"]["items"] == [
        {"index": 1, "reason": "unreadable"},
        {"index": 2, "reason": "paste_transcript"},
    ]
    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Path)) == 0
        assert await session.scalar(select(func.count()).select_from(Material)) == 0
    assert prompts == []


@pytest.mark.anyio
async def test_material_is_forbidden_while_the_flag_is_off(
    app: FastAPI,
    spawn: CollectingSpawn,
    prompts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_flag_globally(monkeypatch, "material")
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post("/api/v1/paths", json=BODY)
        # The flag gates Material only: an ordinary create still works.
        plain = await client.post(
            "/api/v1/paths", json={"topic": "Rust", "level": "new_to_it"}
        )
    assert resp.status_code == 403
    assert plain.status_code == 202


@pytest.mark.anyio
async def test_mode_and_material_must_come_together(
    app: FastAPI, spawn: CollectingSpawn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        no_mode = await client.post(
            "/api/v1/paths", json={**BODY, "material_mode": None}
        )
        no_text = await client.post(
            "/api/v1/paths",
            json={**BODY, "material": [{"kind": "text"}]},
        )
    assert no_mode.status_code == 422
    assert no_text.status_code == 422


@pytest.mark.anyio
async def test_an_arxiv_paper_is_read_digested_and_taught_concept_by_concept(
    app: FastAPI,
    spawn: CollectingSpawn,
    prompts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Papers TDD slice 1: the arXiv item is read through the paper reader, a
    digest runs before the outline, and the outline prompt carries it."""
    from aleph.services.arxiv import StubPaperReader

    monkeypatch.setattr(
        "aleph.routers.v1.paths.default_paper_reader", lambda: StubPaperReader()
    )
    _enable_flag_globally(monkeypatch, "material")
    body = {
        "topic": "Attention Is All You Need",
        "level": "some_experience",
        "material_mode": "teach",
        "material": [{"kind": "arxiv", "url": "https://arxiv.org/abs/1706.03762"}],
    }
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post("/api/v1/paths", json=body)
        assert resp.status_code == 202, resp.text
        path_id = resp.json()["id"]
        await spawn.drain()
        detail = await _poll(client, spawn, path_id)

    assert detail["status"] == "ready"
    assert [(m["kind"], m["title"]) for m in detail["materials"]] == [
        ("arxiv", "Stub paper 1706.03762")
    ]
    digest_prompts = [p for p in prompts if "<paper>" in p]
    outline_prompts = [p for p in prompts if "<paper_concepts>" in p]
    assert len(digest_prompts) == 1
    assert len(outline_prompts) == 1
    async with db.async_session() as session:
        grounded = (
            await session.scalars(
                select(Lesson.material_sections)
                .where(Lesson.path_id == uuid.UUID(path_id))
                .order_by(Lesson.position_in_path)
            )
        ).all()
    # The stub digest chains one concept per section, so the lessons teach
    # sections 1..5 in that order.
    assert grounded == [[1], [2], [3], [4], [5]]
    async with db.async_session() as session:
        concepts = (
            await session.scalars(
                select(Lesson.material_concepts)
                .where(Lesson.path_id == uuid.UUID(path_id))
                .order_by(Lesson.position_in_path)
            )
        ).all()
        digest = await session.scalar(
            select(Material.digest).where(Material.path_id == uuid.UUID(path_id))
        )
    assert concepts == [[f"p1.c{n}"] for n in range(1, 6)]
    assert digest is not None
    assert [c["id"] for c in digest["concepts"]] == [f"c{n}" for n in range(1, 6)]


@pytest.mark.anyio
async def test_a_retried_outline_reuses_the_stored_paper_digest(
    app: FastAPI,
    spawn: CollectingSpawn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The digest is stored before the outline runs, so when the outline fails
    and the learner retries, only the outline asks the model again (D7)."""
    from aleph.services.arxiv import StubPaperReader

    calls = {"digest": 0, "outline": 0}

    def respond(messages: list[ModelMessage], info: AgentInfo):  # noqa: ANN202
        names = {
            prop
            for tool in info.output_tools
            for prop in tool.parameters_json_schema.get("properties", {})
        }
        if "prerequisites" in names:
            calls["digest"] += 1
        elif "units" in names:
            calls["outline"] += 1
            if calls["outline"] == 1:
                raise RuntimeError("provider blip on the first outline")
        return _stub_respond(messages, info)

    model = FunctionModel(respond)
    monkeypatch.setattr(
        gen_module.generation_orchestrator, "_resolve_model", lambda _id: model
    )
    monkeypatch.setattr(
        "aleph.routers.v1.paths.default_paper_reader", lambda: StubPaperReader()
    )
    _enable_flag_globally(monkeypatch, "material")
    body = {
        "topic": "Attention Is All You Need",
        "level": "some_experience",
        "material_mode": "teach",
        "material": [{"kind": "arxiv", "url": "https://arxiv.org/abs/1706.03762"}],
    }
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        path_id = (await client.post("/api/v1/paths", json=body)).json()["id"]
        await spawn.drain()
        assert (await _poll(client, spawn, path_id))["status"] == "failed"
        assert (await client.post(f"/api/v1/paths/{path_id}/retry")).status_code == 202
        await spawn.drain()
        assert (await _poll(client, spawn, path_id))["status"] == "ready"
    assert calls == {"digest": 1, "outline": 2}


@pytest.mark.anyio
async def test_an_arxiv_paper_without_html_creates_no_path(
    app: FastAPI,
    spawn: CollectingSpawn,
    prompts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aleph.services.arxiv import StubPaperReader

    monkeypatch.setattr(
        "aleph.routers.v1.paths.default_paper_reader", lambda: StubPaperReader()
    )
    _enable_flag_globally(monkeypatch, "material")
    body = {
        "topic": "Something new",
        "level": "some_experience",
        "material_mode": "teach",
        "material": [{"kind": "arxiv", "url": "https://arxiv.org/abs/2401.99999"}],
    }
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        resp = await client.post("/api/v1/paths", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["items"] == [
        {"index": 0, "reason": "no_html"}
    ]
    async with db.async_session() as session:
        assert await session.scalar(select(func.count()).select_from(Path)) == 0
