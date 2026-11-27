"""Reasoning on Quick checks, end to end (CONTEXT.md: *Reasoning*).

Real Postgres, the real routes, and the deterministic stub at the model seam for
both lesson generation and the reasoning grader. The stub grades on the
learner's own text (``services/stub_model.py``): under four words covers no
point, "because" plus twelve words covers both, anything else covers one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aleph.services import reasoning as reasoning_module
from aleph.services.stub_model import FORCE_GRADER_FAILURE, STUB_FOLLOW_UP_QUESTION

from .conftest import _disable_flag_globally, _enable_flag_globally, stub_resolver
from .test_lessons_api import (
    OWNER,
    _client,
    _create_and_ready,
    _lesson,
    _lessons,
    _quick_check_row,
    _sign_in,
    app,  # noqa: F401 - pytest fixture
    spawn,  # noqa: F401 - pytest fixture
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx import AsyncClient

    from .conftest import CollectingSpawn

SOLID = (
    "It holds because the principle in the lesson applies directly to "
    "the scenario the question describes."
)
PARTIAL = "The lesson's principle applies here."


@pytest.fixture(autouse=True)
def stub_grader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reasoning_module.reasoning_service, "_resolve_model", stub_resolver()
    )


@pytest.fixture
def explain_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag_globally(monkeypatch, "explain_checks")


async def _first_lesson(
    client: AsyncClient, collector: CollectingSpawn, topic: str
) -> tuple[str, int]:
    path = await _create_and_ready(client, collector, topic)
    lesson_id = _lessons(path)[0]["id"]
    await _lesson(client, collector, lesson_id)
    row = await _quick_check_row(lesson_id)
    return lesson_id, row.correct_index


@pytest.mark.anyio
@pytest.mark.usefixtures("explain_on")
async def test_partial_reasoning_gets_feedback_and_one_follow_up(
    app: FastAPI,  # noqa: F811
    spawn: CollectingSpawn,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        lesson_id, correct = await _first_lesson(client, spawn, "Rust lifetimes")
        detail = await _lesson(client, spawn, lesson_id)
        assert detail["quick_check"]["asks_reasoning"] is True

        resp = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt",
            json={"selected_index": correct, "reasoning": PARTIAL},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The Outcome of record is still the option: a partial "why" is formative.
        assert body["outcome"] == "correct"
        reasoning = body["reasoning"]
        assert reasoning["state"] == "graded"
        assert reasoning["grade"] == "partial"
        assert reasoning["covered_points"] == [0]
        assert len(reasoning["key_points"]) == 2
        assert reasoning["follow_up"] == {
            "question": STUB_FOLLOW_UP_QUESTION,
            "answer": None,
            "state": None,
            "grade": None,
            "feedback": None,
        }

        # A resubmitted Attempt cannot replace the Reasoning (first wins).
        again = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt",
            json={"selected_index": correct, "reasoning": SOLID},
        )
        assert again.json()["reasoning"]["text"] == PARTIAL
        assert again.json()["reasoning"]["grade"] == "partial"

        follow = await client.post(
            f"/api/v1/lessons/{lesson_id}/reasoning/follow-up",
            json={"answer": "Without it the borrow would outlive its owner."},
        )
        assert follow.status_code == 200, follow.text
        follow_up = follow.json()["reasoning"]["follow_up"]
        assert follow_up["state"] == "graded"
        assert follow_up["grade"] == "solid"
        # The first round's grade stands; the follow-up is feedback only.
        assert follow.json()["reasoning"]["grade"] == "partial"

        # Only one follow-up answer is accepted.
        second = await client.post(
            f"/api/v1/lessons/{lesson_id}/reasoning/follow-up",
            json={"answer": "something else"},
        )
        assert second.status_code == 200
        assert second.json()["reasoning"]["follow_up"]["answer"] == (
            "Without it the borrow would outlive its owner."
        )

        # The reveal survives a reload.
        reloaded = await _lesson(client, spawn, lesson_id)
        assert reloaded["attempt"]["reasoning"]["follow_up"]["grade"] == "solid"


@pytest.mark.anyio
@pytest.mark.usefixtures("explain_on")
async def test_a_sound_argument_for_the_wrong_option_is_not_solid(
    app: FastAPI,  # noqa: F811
    spawn: CollectingSpawn,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        lesson_id, correct = await _first_lesson(client, spawn, "Bayes rule")
        resp = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt",
            json={"selected_index": (correct + 1) % 3, "reasoning": SOLID},
        )
        reasoning = resp.json()["reasoning"]
        assert resp.json()["outcome"] == "incorrect"
        assert reasoning["covered_points"] == [0, 1]
        assert reasoning["grade"] == "partial"
        assert reasoning["misconceptions_shown"]
        assert reasoning["follow_up"] is not None


@pytest.mark.anyio
@pytest.mark.usefixtures("explain_on")
async def test_solid_reasoning_asks_no_follow_up(
    app: FastAPI,  # noqa: F811
    spawn: CollectingSpawn,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        lesson_id, correct = await _first_lesson(client, spawn, "Compound interest")
        resp = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt",
            json={"selected_index": correct, "reasoning": SOLID},
        )
        reasoning = resp.json()["reasoning"]
        assert reasoning["grade"] == "solid"
        assert reasoning["follow_up"] is None
        follow = await client.post(
            f"/api/v1/lessons/{lesson_id}/reasoning/follow-up",
            json={"answer": "anything"},
        )
        assert follow.status_code == 409


@pytest.mark.anyio
@pytest.mark.usefixtures("explain_on")
async def test_a_failed_grader_keeps_the_attempt_and_shows_the_key(
    app: FastAPI,  # noqa: F811
    spawn: CollectingSpawn,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        lesson_id, correct = await _first_lesson(client, spawn, "TCP handshakes")
        resp = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt",
            json={
                "selected_index": correct,
                "reasoning": f"{PARTIAL} {FORCE_GRADER_FAILURE}",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "correct"
        assert body["reasoning"]["state"] == "failed"
        assert body["reasoning"]["grade"] is None
        assert len(body["reasoning"]["key_points"]) == 2


@pytest.mark.anyio
async def test_flag_off_asks_for_and_stores_no_reasoning(
    app: FastAPI,  # noqa: F811
    spawn: CollectingSpawn,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_flag_globally(monkeypatch, "explain_checks")
    async with _client(app) as client:
        await _sign_in(client, monkeypatch, OWNER)
        lesson_id, correct = await _first_lesson(client, spawn, "Photosynthesis")
        detail = await _lesson(client, spawn, lesson_id)
        assert detail["quick_check"]["asks_reasoning"] is False
        resp = await client.post(
            f"/api/v1/lessons/{lesson_id}/attempt",
            json={"selected_index": correct, "reasoning": SOLID},
        )
        assert resp.status_code == 200
        assert resp.json()["reasoning"] is None
        follow = await client.post(
            f"/api/v1/lessons/{lesson_id}/reasoning/follow-up",
            json={"answer": "anything"},
        )
        assert follow.status_code == 404
