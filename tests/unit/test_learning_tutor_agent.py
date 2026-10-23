"""Closed output vocabulary and the total serialized context boundary."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from aleph.agents.learning_tutor import (
    SYSTEM_PROMPT,
    LearningTutorDeps,
    LearningTutorOutput,
    build_learning_tutor_agent,
    output_violation,
)
from aleph.services.stub_model import build_stub_model


@pytest.mark.anyio
async def test_stub_delay_is_async_and_owned_by_the_provider():
    deps = LearningTutorDeps(
        context='{"kind":"turn","content":"[lt-delay] [lt-path]",'
        '"evidence_keys":["message"]}',
        kind="turn",
        evidence_keys=("message",),
    )
    task = asyncio.create_task(
        build_learning_tutor_agent().run("Respond", deps=deps, model=build_stub_model())
    )
    await asyncio.sleep(0.05)
    assert not task.done()
    result = await task
    assert len(result.output.recommendations) == 1


def test_turn_requires_reply_and_known_evidence():
    deps = LearningTutorDeps(context="{}", kind="turn", evidence_keys=("message",))
    assert output_violation(LearningTutorOutput(), deps)
    draft = dict(
        kind="path",
        topic="Electricity",
        level="new_to_it",
        description="Learn",
        rationale="You asked",
        evidence_keys=["fake"],
    )
    output = LearningTutorOutput(reply="Hello", recommendations=[draft])
    assert output_violation(output, deps)
    output.recommendations[0].evidence_keys = ["message"]
    assert output_violation(output, deps) is None


def test_context_cannot_exceed_total_bound():
    with pytest.raises(ValueError):
        LearningTutorDeps(context="x" * 40001, kind="refresh")
    assert len(SYSTEM_PROMPT) < 2000


@pytest.mark.parametrize(
    "kind,slots,count", [("turn", 3, 2), ("turn", 0, 1), ("refresh", 3, 4)]
)
def test_output_card_caps(kind, slots, count):
    deps = LearningTutorDeps(
        context="{}", kind=kind, evidence_keys=("focus",), available_slots=slots
    )
    payload = dict(
        reply="Hello" if kind == "turn" else None,
        recommendations=[
            dict(
                kind="path",
                topic=str(i),
                level="new_to_it",
                description="Learn",
                rationale="You asked",
                evidence_keys=["focus"],
            )
            for i in range(count)
        ],
    )
    if count > 3:
        with pytest.raises(ValidationError):
            LearningTutorOutput.model_validate(payload)
    else:
        assert output_violation(LearningTutorOutput.model_validate(payload), deps)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sentinel,valid",
    [("[lt-path]", True), ("[lt-invalid]", False), ("[lt-empty]", True)],
)
async def test_real_agent_stub_structured_retry_and_private_instrumentation(
    sentinel, valid
):
    import json

    deps = LearningTutorDeps(
        context=json.dumps(
            dict(
                kind="turn",
                content=sentinel,
                focus=None,
                evidence_keys=["message"],
                paths=[],
                beats=[],
            ),
            separators=(",", ":"),
        ),
        kind="turn",
        evidence_keys=("message",),
    )
    agent = build_learning_tutor_agent()
    assert agent.instrument is False
    if valid:
        result = await agent.run("Respond", deps=deps, model=build_stub_model())
        assert result.output.reply
        assert len(result.output.recommendations) == (
            1 if sentinel == "[lt-path]" else 0
        )
    else:
        with pytest.raises(UnexpectedModelBehavior):
            await agent.run("Respond", deps=deps, model=build_stub_model())


def test_targets_exact_exclusion_analyst_and_refinement_are_closed():
    selected = uuid4()
    deps = LearningTutorDeps(
        context="{}",
        kind="turn",
        evidence_keys=("message",),
        available_slots=0,
        selected_id=selected,
    )
    output = LearningTutorOutput(
        reply="Discuss",
        recommendations=[
            dict(
                kind="path",
                topic="  ＥLECTRICITY  ",
                level="new_to_it",
                description="Learn",
                rationale="You asked",
                evidence_keys=["message"],
            )
        ],
        replacement=selected,
    )
    assert output_violation(output, deps) is None
    assert output_violation(
        output, replace(deps, excluded=frozenset({("path", "electricity")}))
    )
    assert output_violation(output, replace(deps, selected_id=uuid4()))
    output.recommendations[0].kind = "beat"
    assert output_violation(output, deps)
    assert output_violation(output, replace(deps, analyst_enabled=True)) is None
    forged = LearningTutorOutput(
        reply="Continue",
        existing_work=[
            dict(kind="path", supplied_target_key="https://untrusted.invalid")
        ],
    )
    assert output_violation(forged, deps)
    with pytest.raises(ValidationError):
        LearningTutorOutput.model_validate({"reply": "Do it", "create_path": True})


@pytest.mark.anyio
async def test_global_instrumentation_does_not_capture_learning_context(
    capfire, monkeypatch
):
    monkeypatch.setattr(Agent, "_instrument_default", True)
    deps = LearningTutorDeps(
        context='{"kind":"turn","content":"PRIVATE_LEARNING_INTEREST","focus":null,"evidence_keys":["message"],"paths":[],"beats":[]}',
        kind="turn",
        evidence_keys=("message",),
    )
    result = await build_learning_tutor_agent().run(
        "Respond", deps=deps, model=build_stub_model()
    )
    assert result.output.reply
    assert "PRIVATE_LEARNING_INTEREST" not in repr(
        capfire.exporter.exported_spans_as_dict()
    )
