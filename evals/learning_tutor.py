"""Synthetic Account-wide Tutor evals. Smoke verifies plumbing, never quality."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from aleph.agents.learning_tutor import (
    LearningTutorDeps,
    LearningTutorOutput,
    build_learning_tutor_agent,
    output_violation,
)
from evals.generation import SeedMeta
from evals.judge import Judge, JudgeDeps
from evals.rubric import APPLICABLE_ITEMS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic_ai.models import Model

SEED_PATH = Path(__file__).with_name("learning_tutor_seed_set.yaml")
HARD_FLOORS = frozenset({"LearningInvariants"})
JUDGE_CHECKS = frozenset(APPLICABLE_ITEMS["learning_tutor"])
JUDGE_HARD_FLOORS = frozenset({"grounding", "privacy", "safety"})
ASSERTIONS = {"LearningInvariants": HARD_FLOORS, "LearningJudge": JUDGE_CHECKS}
output_checks = output_violation  # Exact production predicate, not a second spelling.


class LearningCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["turn", "refresh"] = "turn"
    context: dict[str, Any]
    evidence_keys: tuple[str, ...] = ()
    targets: tuple[tuple[str, str], ...] = ()
    excluded: frozenset[tuple[str, str]] = frozenset()
    analyst_enabled: bool = False
    available_slots: int = Field(default=3, ge=0, le=3)
    expected: str = Field(min_length=20)
    forbidden: str = Field(min_length=20)
    smoke: LearningTutorOutput

    def deps(self) -> LearningTutorDeps:
        context = dict(
            self.context,
            kind=self.kind,
            evidence_keys=self.evidence_keys,
            analyst_enabled=self.analyst_enabled,
            available_slots=self.available_slots,
        )
        return LearningTutorDeps(
            context=json.dumps(context),
            kind=self.kind,
            evidence_keys=self.evidence_keys,
            targets=self.targets,
            excluded=self.excluded,
            analyst_enabled=self.analyst_enabled,
            available_slots=self.available_slots,
        )


def load_cases(path: Path = SEED_PATH) -> list[LearningCase]:
    raw_cases = yaml.safe_load(path.read_text())
    if not isinstance(raw_cases, list):
        raise ValueError("Seed data must be a list of curated cases")
    cases = [LearningCase.model_validate(raw) for raw in raw_cases]
    if len(cases) < 20 or len({case.name for case in cases}) != len(cases):
        raise ValueError("Require at least 20 uniquely named curated cases")
    return cases


def build_task(
    model: Model | None = None,
) -> Callable[[LearningCase], Awaitable[LearningTutorOutput]]:
    async def task(case: LearningCase) -> LearningTutorOutput:
        # Only the offline provider sees the scripted output, not the agent prompt.
        smoke = FunctionModel(
            lambda messages, info: ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name, case.smoke.model_dump(mode="json")
                    )
                ]
            )
        )
        result = await build_learning_tutor_agent().run(
            "Respond to the current request in the supplied context.",
            deps=case.deps(),
            model=model or smoke,
        )
        return result.output

    return task


@dataclass
class LearningInvariants(Evaluator[LearningCase, LearningTutorOutput, SeedMeta]):
    def evaluate(
        self, ctx: EvaluatorContext[LearningCase, LearningTutorOutput, SeedMeta]
    ) -> EvaluationReason:
        reason = output_checks(ctx.output, ctx.inputs.deps())
        return EvaluationReason(
            value=reason is None,
            reason=reason or "Production predicates held; quality unmeasured",
        )


def judge_prompt(case: LearningCase, output: LearningTutorOutput) -> str:
    return "artifact=learning_tutor\n" + json.dumps(
        {
            "context": json.loads(case.deps().context),
            "allowed_targets": case.targets,
            "excluded_topics": sorted(case.excluded),
            "expected_behavior": case.expected,
            "forbidden_behavior": case.forbidden,
            "output": output.model_dump(mode="json"),
        }
    )


@dataclass
class LearningJudge(Evaluator[LearningCase, LearningTutorOutput, SeedMeta]):
    judge: Judge

    def build_serialization_arguments(self) -> dict[str, object]:
        return {"judge": self.judge.label}

    async def evaluate(
        self, ctx: EvaluatorContext[LearningCase, LearningTutorOutput, SeedMeta]
    ) -> dict[str, EvaluationReason]:
        result = await self.judge.agent.run(
            judge_prompt(ctx.inputs, ctx.output),
            deps=JudgeDeps(artifact="learning_tutor"),
            model=self.judge.model,
        )
        return {
            item.item: EvaluationReason(value=item.passed, reason=item.reason)
            for item in result.output.items
        }


def dataset(
    cases: list[LearningCase], judge: Judge | None = None
) -> Dataset[LearningCase, LearningTutorOutput, SeedMeta]:
    result = Dataset[LearningCase, LearningTutorOutput, SeedMeta](
        name="learning-tutor",
        cases=[Case(name=case.name, inputs=case) for case in cases],
        evaluators=[LearningInvariants()],
    )
    if judge:
        result.add_evaluator(LearningJudge(judge))
    return result
