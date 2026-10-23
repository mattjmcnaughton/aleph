"""Acceptance identity hashes exactly the validated creation inputs."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from aleph.dtos.beats import DeployBeatRequest
from aleph.dtos.paths import CreatePathRequest
from aleph.services.creation import creation_hash


@pytest.mark.parametrize("model", [CreatePathRequest, DeployBeatRequest])
def test_pair_requires_both_fields(model):
    base = {"topic": "Test", "level": "new_to_it", "anchor_weekday": 2}
    for fields in [
        {"learning_recommendation_id": uuid4()},
        {"learning_tutor_revision": 0},
        {"learning_recommendation_id": uuid4(), "learning_tutor_revision": -1},
    ]:
        with pytest.raises(ValidationError):
            model.model_validate(base | fields)


@pytest.mark.parametrize("model", [CreatePathRequest, DeployBeatRequest])
def test_hash_is_normalized_and_revision_independent(model):
    base = {"topic": " Topic ", "level": "new_to_it", "anchor_weekday": 2}
    plain = model.model_validate(base)
    recommended = model.model_validate(
        base
        | {
            "topic": "Topic",
            "guidance": None,
            "learning_recommendation_id": uuid4(),
            "learning_tutor_revision": 42,
        }
    )
    assert creation_hash(plain) == creation_hash(recommended)
    changes: dict[str, str | int] = {
        "topic": "Changed",
        "level": "work_in_it",
        "guidance": "Specific",
    }
    if model is CreatePathRequest:
        changes |= {
            "teaching_style": "Examples",
            "model_outline": "a/b",
            "model_lesson": "c/d",
        }
    else:
        changes |= {"anchor_weekday": 4, "model_research": "a/b", "model_brief": "c/d"}
    for field, value in changes.items():
        assert creation_hash(
            model.model_validate(base | {field: value})
        ) != creation_hash(plain)
