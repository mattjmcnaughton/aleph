"""OpenRouter model resolution seam (TDD §5.3, D4; ticket AL-030).

A thin factory, not a client — pydantic-ai's ``OpenRouterProvider`` owns the
protocol. This service owns what pydantic-ai can't: building
``OpenAIChatModel(id, provider=OpenRouterProvider)`` from *our* config, **caching
built models per id** so each doesn't leak a fresh ``httpx.AsyncClient`` pool per
request (habagou's documented rationale — the provider owns the pool, so identity
matters), picker display labels, and **resolving the ``stub`` id** to the
deterministic test model (``services/stub_model.py``, §12).

It also preserves the layering rule: agents bind no model, so the binding lives
here in ``services/`` (which may import config), never in ``agents/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from aleph.config import STUB_MODEL_ID, settings
from aleph.services.stub_model import build_stub_model

if TYPE_CHECKING:
    from pydantic_ai.models import Model

# Keyed on (model id, API key) so tests that flip configuration get a matching
# model. Growth is bounded by the distinct configurations seen in one process —
# a handful outside of tests.
_model_cache: dict[tuple[str, str], OpenAIChatModel] = {}

# Display names for the admin model picker (§5.3). Ids outside this map fall back
# to the raw OpenRouter id — accurate if less pretty, never a blocker.
_MODEL_LABELS = {
    "anthropic/claude-sonnet-5": "Claude Sonnet 5",
    "anthropic/claude-haiku-4-5": "Claude Haiku 4.5",
    "anthropic/claude-opus-4-8": "Claude Opus 4.8",
    "openai/gpt-5.6-terra": "GPT-5.6 Terra",
    "minimax/minimax-m3": "MiniMax M3",
    STUB_MODEL_ID: "Deterministic stub (CI/e2e)",
}


def model_label(model_id: str) -> str:
    """Human-readable picker label for an OpenRouter model id."""
    return _MODEL_LABELS.get(model_id, model_id)


def build_openrouter_model(model_name: str) -> OpenAIChatModel:
    """Return a cached OpenRouter-backed model for ``model_name``.

    Constructing the model performs no network I/O; the request happens when an
    agent runs. Callers gate on configuration (an API key) before running.
    """
    key = (model_name, settings.openrouter_api_key)
    model = _model_cache.get(key)
    if model is None:
        model = OpenAIChatModel(
            model_name,
            provider=OpenRouterProvider(api_key=settings.openrouter_api_key),
        )
        _model_cache[key] = model
    return model


def resolve_model(model_id: str) -> Model:
    """Resolve a configured model id to a runnable pydantic-ai model.

    The ``stub`` id (TDD §12/D9) resolves to the deterministic
    :class:`~pydantic_ai.models.function.FunctionModel`; every other id resolves
    to a cached OpenRouter-backed :class:`OpenAIChatModel`. The production guard
    that forbids ``stub`` lives in ``config.py`` (fails fast at startup), so this
    resolver trusts the id it is handed.

    ``build_stub_model`` is itself cached (``functools.cache``), so repeated
    ``resolve_model("stub")`` calls return the same instance — identity, like
    the OpenRouter models.
    """
    if model_id == STUB_MODEL_ID:
        return build_stub_model()
    return build_openrouter_model(model_id)
