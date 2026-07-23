"""Unit tests for the OpenRouter resolution seam (TDD §5.3, ticket AL-030).

New file (AL-030). Covers picker labels, per-id caching (no per-request httpx
pools), and resolution of the ``stub`` id to the deterministic FunctionModel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel

from aleph.config import STUB_MODEL_ID
from aleph.services import openrouter
from aleph.services.openrouter import (
    build_openrouter_model,
    model_label,
    resolve_model,
)

if TYPE_CHECKING:
    import pytest


def test_model_label_maps_known_ids() -> None:
    assert model_label("anthropic/claude-sonnet-5") == "Claude Sonnet 5"
    assert model_label("anthropic/claude-haiku-4-5") == "Claude Haiku 4.5"
    assert model_label("minimax/minimax-m3") == "MiniMax M3"


def test_model_label_falls_back_to_the_id() -> None:
    assert model_label("someone/unmapped-model") == "someone/unmapped-model"


def test_builder_caches_per_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(openrouter, "_model_cache", {})

    first = build_openrouter_model("anthropic/claude-sonnet-5")
    second = build_openrouter_model("minimax/minimax-m3")
    again = build_openrouter_model("anthropic/claude-sonnet-5")

    # Distinct ids get distinct models; the same id reuses the cached instance
    # (the provider owns an httpx pool, so identity matters, not just equality).
    assert first is not second
    assert first is again
    assert first.model_name == "anthropic/claude-sonnet-5"
    assert second.model_name == "minimax/minimax-m3"


def test_resolve_model_returns_cached_openrouter_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(openrouter.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(openrouter, "_model_cache", {})

    first = resolve_model("anthropic/claude-sonnet-5")
    again = resolve_model("anthropic/claude-sonnet-5")

    assert isinstance(first, OpenAIChatModel)
    assert first is again


def test_resolve_model_returns_the_stub_function_model() -> None:
    model = resolve_model(STUB_MODEL_ID)
    assert isinstance(model, FunctionModel)


def test_resolve_stub_is_the_same_instance_each_time() -> None:
    # "Same id twice -> same model instance" also holds for the stub.
    assert resolve_model(STUB_MODEL_ID) is resolve_model(STUB_MODEL_ID)
