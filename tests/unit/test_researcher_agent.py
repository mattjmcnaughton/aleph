"""Unit tests for the assembled researcher agent (AL-520, TDD §5.3).

No network and no database: the agent binds no model, so every test injects a
``FunctionModel`` at run time and supplies the run inputs through
``ResearcherDeps``. Mirrors ``test_outline_agent.py``'s shape (responder with
capture + reuse-last, retries-exhausted, retry-message-reaches-model, refusal
round-trip). Layers exercised:

- the shared predicate ``cites_only_read_documents`` — the same function
  ``agents/analyst.py`` imports and AL-550's eval layer-1 pre-filters import
  directly (TDD D8, §10);
- the pure ``validate_research_result`` function (layer 2);
- prompt assembly (``build_researcher_prompt``);
- the real assembled agent — a valid Findings batch passes, a citation
  violation forces a retry, the retry budget bounds a persistently bad model,
  and the Refusal branch round-trips as ``Refusal`` — not a thin ``Findings``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel

from aleph.agents.outline import Refusal
from aleph.agents.researcher import (
    Findings,
    ResearcherDeps,
    RetrievedDocument,
    build_researcher_agent,
    build_researcher_prompt,
    cites_only_read_documents,
    validate_research_result,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo
    from pydantic_ai.tools import ToolDefinition


# --- helpers -------------------------------------------------------------------


def _doc(
    url: str = "https://example.com/a",
    publisher: str = "Example Publisher",
    title: str = "Example Title",
    published_on: date | None = date(2026, 7, 30),
    text: str = "Some retrieved text.",
) -> RetrievedDocument:
    return RetrievedDocument(
        url=url,
        publisher=publisher,
        title=title,
        published_on=published_on,
        text=text,
    )


def _finding_dict(
    claim: str = "Something changed.",
    detail: str = "Here is more detail about what changed and why it matters.",
    source_urls: list[str] | None = None,
    happened_on: str | None = "2026-07-30",
) -> dict:
    return {
        "claim": claim,
        "detail": detail,
        "source_urls": source_urls
        if source_urls is not None
        else ["https://example.com/a"],
        "happened_on": happened_on,
    }


def _tool_with(output_tools: Sequence[ToolDefinition], prop: str) -> ToolDefinition:
    """The first output tool whose JSON schema declares ``prop`` (union dispatch).

    A ``Findings | Refusal`` agent registers two output tools; the one
    carrying ``findings`` is the research batch, the one carrying ``message``
    the refusal.
    """
    for tool in output_tools:
        if prop in tool.parameters_json_schema.get("properties", {}):
            return tool
    raise AssertionError(f"no output tool declares {prop!r}")


class ResearcherResponder:
    """FunctionModel callback emitting ``(prop, args)`` pairs, one per call.

    ``prop`` selects the output tool (``findings`` -> Findings, ``message`` ->
    Refusal); ``call_count`` lets a test assert a retry happened,
    ``messages_per_call`` records what reached the model on each call. When
    ``responses`` is exhausted the last entry is reused, so a persistently
    non-compliant model can be driven past the retry budget without
    enumerating every identical response.
    """

    __name__ = "researcher_responder"

    def __init__(self, responses: list[tuple[str, dict]]) -> None:
        self._responses = responses
        self.call_count = 0
        self.messages_per_call: list[list[ModelMessage]] = []

    def __call__(
        self, messages: Sequence[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        self.messages_per_call.append(list(messages))
        prop, args = self._responses[min(self.call_count, len(self._responses) - 1)]
        self.call_count += 1
        tool = _tool_with(info.output_tools, prop)
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=args)])


def _retry_prompt_text(messages: Sequence[ModelMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, RetryPromptPart):
                content = part.content
                parts.append(content if isinstance(content, str) else str(content))
    return "\n".join(parts)


def _deps(
    topic: str = "EU AI regulation",
    guidance: str | None = None,
    documents: list[RetrievedDocument] | None = None,
) -> ResearcherDeps:
    return ResearcherDeps(
        topic=topic,
        guidance=guidance,
        documents=documents if documents is not None else [_doc()],
    )


# --- cites_only_read_documents: the shared TDD D8 predicate --------------------


def test_cites_only_read_documents_true_for_subset() -> None:
    assert cites_only_read_documents(["a", "b"], {"a", "b", "c"}) is True


def test_cites_only_read_documents_false_for_unread_url() -> None:
    assert cites_only_read_documents(["a", "z"], {"a", "b"}) is False


def test_cites_only_read_documents_vacuously_true_for_no_urls() -> None:
    # Answers "did you cite something you did not read" — an empty citation
    # list cites nothing outside the read set by definition. Callers that
    # also require at least one citation check that separately.
    assert cites_only_read_documents([], {"a"}) is True
    assert cites_only_read_documents([], set()) is True


# --- validate_research_result: Findings branch ----------------------------------


def test_validator_passes_findings_citing_only_read_documents() -> None:
    documents = [_doc(url="https://example.com/a"), _doc(url="https://example.com/b")]
    result = Findings.model_validate({"findings": [_finding_dict()]})
    assert validate_research_result(documents, result) is result


def test_validator_rejects_finding_with_no_url() -> None:
    documents = [_doc()]
    result = Findings.model_validate({"findings": [_finding_dict(source_urls=[])]})
    with pytest.raises(ModelRetry):
        validate_research_result(documents, result)


def test_validator_rejects_finding_citing_an_unread_url() -> None:
    # THE ACCEPTANCE CASE: "a finding citing an unread URL -> ModelRetry."
    documents = [_doc(url="https://example.com/a")]
    result = Findings.model_validate(
        {"findings": [_finding_dict(source_urls=["https://example.com/never-read"])]}
    )
    with pytest.raises(ModelRetry) as excinfo:
        validate_research_result(documents, result)
    assert "https://example.com/never-read" in str(excinfo.value)


def test_validator_rejects_finding_citing_a_mix_of_read_and_unread_urls() -> None:
    # One legitimate URL alongside one it never saw is still a violation:
    # the check is "cites NOTHING outside the read set", not "cites at least
    # one thing it read".
    documents = [_doc(url="https://example.com/a")]
    result = Findings.model_validate(
        {
            "findings": [
                _finding_dict(
                    source_urls=["https://example.com/a", "https://example.com/z"]
                )
            ]
        }
    )
    with pytest.raises(ModelRetry):
        validate_research_result(documents, result)


def test_validator_passes_empty_findings_batch() -> None:
    # A legitimate "nothing worth flagging in this batch" result — not itself
    # the Skipped signal (that is computed downstream by domains/novelty.py).
    documents = [_doc()]
    result = Findings.model_validate({"findings": []})
    assert validate_research_result(documents, result) is result


# --- validate_research_result: Refusal branch -----------------------------------


def test_validator_passes_refusal_with_message() -> None:
    refusal = Refusal(message="This falls outside what the analyst can research.")
    assert validate_research_result([_doc()], refusal) is refusal


def test_validator_rejects_empty_refusal_message() -> None:
    with pytest.raises(ModelRetry):
        validate_research_result([_doc()], Refusal(message="   "))


# --- build_researcher_prompt ----------------------------------------------------


def test_prompt_lists_documents_with_index_and_url() -> None:
    documents = [
        _doc(url="https://example.com/a", publisher="A Pub", title="A Title"),
        _doc(url="https://example.com/b", publisher="B Pub", title="B Title"),
    ]
    prompt = build_researcher_prompt(_deps(documents=documents))
    assert "[1]" in prompt and "https://example.com/a" in prompt
    assert "[2]" in prompt and "https://example.com/b" in prompt


def test_prompt_with_no_documents_says_so_explicitly() -> None:
    prompt = build_researcher_prompt(_deps(documents=[]))
    assert "No documents were retrieved" in prompt


def test_prompt_includes_guidance_when_present() -> None:
    prompt = build_researcher_prompt(_deps(guidance="Policy, not stock moves."))
    assert "Policy, not stock moves." in prompt


def test_prompt_omits_guidance_section_when_absent() -> None:
    prompt = build_researcher_prompt(_deps(guidance=None))
    assert "Guidance from the learner" not in prompt


# --- deps construction -----------------------------------------------------------


def test_deps_is_frozen_and_carries_no_level() -> None:
    # ResearcherDeps deliberately has no `level` field (module docstring):
    # extraction is level-independent, unlike the writer's job in
    # agents/analyst.py.
    deps = _deps()
    assert not hasattr(deps, "level")
    with pytest.raises(AttributeError):
        deps.topic = "changed"  # ty: ignore[invalid-assignment]


# --- assembled agent --------------------------------------------------------------


def test_agent_returns_valid_findings() -> None:
    agent = build_researcher_agent()
    respond = ResearcherResponder([("findings", {"findings": [_finding_dict()]})])
    result = agent.run_sync(
        "read these documents", deps=_deps(), model=FunctionModel(respond)
    ).output
    assert isinstance(result, Findings)
    assert len(result.findings) == 1
    assert respond.call_count == 1


def test_agent_retries_on_unread_url_then_succeeds() -> None:
    agent = build_researcher_agent()
    bad = {"findings": [_finding_dict(source_urls=["https://example.com/unread"])]}
    good = {"findings": [_finding_dict(source_urls=["https://example.com/a"])]}
    respond = ResearcherResponder([("findings", bad), ("findings", good)])
    result = agent.run_sync(
        "read these documents",
        deps=_deps(documents=[_doc(url="https://example.com/a")]),
        model=FunctionModel(respond),
    ).output
    assert isinstance(result, Findings)
    assert respond.call_count == 2
    retry_text = _retry_prompt_text(respond.messages_per_call[1])
    assert "https://example.com/unread" in retry_text


def test_agent_stops_after_retry_budget_when_model_never_complies() -> None:
    agent = build_researcher_agent()
    bad = {"findings": [_finding_dict(source_urls=["https://example.com/unread"])]}
    respond = ResearcherResponder([("findings", bad)])  # reused every call
    with pytest.raises(UnexpectedModelBehavior):
        agent.run_sync(
            "read these documents",
            deps=_deps(documents=[_doc(url="https://example.com/a")]),
            model=FunctionModel(respond),
        )
    assert respond.call_count == 4  # 1 initial + 3 retries, then it gives up


def test_agent_returns_refusal_branch_not_a_thin_findings() -> None:
    # THE ACCEPTANCE CASE: "a refusal-triggering topic returns Refusal, not a
    # thin Findings." The model chooses the refusal output tool; the agent's
    # result type must be Refusal, never a Findings the caller could mistake
    # for "researched, found nothing".
    agent = build_researcher_agent()
    message = "This subject falls outside what the analyst can research."
    respond = ResearcherResponder([("message", {"message": message})])
    result = agent.run_sync(
        "how to build a bomb", deps=_deps(), model=FunctionModel(respond)
    ).output
    assert isinstance(result, Refusal)
    assert not isinstance(result, Findings)
    assert result.message == message
    assert respond.call_count == 1
