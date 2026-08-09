"""Unit tests for the assembled analyst agent (AL-520, TDD §5.4/§5.5).

No network and no database: the agent binds no model, so every test injects a
``FunctionModel`` at run time and supplies the run inputs through
``AnalystDeps``. Mirrors ``test_researcher_agent.py``'s / ``test_outline_agent
.py``'s shape. Layers exercised:

- the pure ``validate_brief_result`` function (layer 2) — branch-matches-state,
  non-empty ``cited_urls``, and ``cited_urls`` a subset of ``Deps.documents``
  via the *same* ``cites_only_read_documents`` predicate
  ``agents/researcher.py`` exports (TDD §5.5: "the same check's degenerate
  case" — imported, never re-implemented, checked here with an identity test);
- **the padding test** — this phase's signature case (TDD §5.4, §11): with
  empty ``documents``/``survivors`` in ``AnalystDeps``, no ``BriefBody`` can
  pass validation, no matter what it says, and this is demonstrated by
  hammering the pure validator with adversarial ``BriefBody`` payloads and
  separately by running the real assembled agent with a model that always
  tries to pad;
- prompt assembly (``build_analyst_prompt``);
- the real assembled agent — a valid Brief passes, an out-of-Deps citation
  forces a retry, a branch-mismatched ``SkippedNote`` forces a retry, and the
  retry budget bounds a persistently bad model.

``SkippedNote.detail`` fixtures throughout this file are lower-case,
no-terminal-period fragments (e.g. ``"the consultation is still open,
closing 11 Sept"``) — the register the class docstring and the system
prompt specify: ``detail`` reads as a continuation after an em dash, never
a sentence of its own (TDD §5.4; see
``test_skipped_note_detail_exemplar_matches_the_fragment_register``).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import ModelRetry, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from aleph.agents.analyst import (
    AnalystDeps,
    BriefBody,
    SkippedNote,
    build_analyst_agent,
    build_analyst_prompt,
    validate_brief_result,
)
from aleph.agents.researcher import (
    Finding,
    RetrievedDocument,
    cites_only_read_documents,
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


def _finding(
    claim: str = "Something changed.",
    detail: str = "More detail a writer could draw on.",
    source_urls: list[str] | None = None,
    happened_on: date | None = date(2026, 7, 30),
) -> Finding:
    return Finding.model_validate(
        {
            "claim": claim,
            "detail": detail,
            "source_urls": source_urls
            if source_urls is not None
            else ["https://example.com/a"],
            "happened_on": happened_on,
        }
    )


def _brief_dict(
    title: str = "The ambient-documentation backlash arrived.",
    body_markdown: str = "Northlake published a review.",
    cited_urls: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "body_markdown": body_markdown,
        "cited_urls": cited_urls
        if cited_urls is not None
        else ["https://example.com/a"],
    }


def _deps(
    topic: str = "AI in healthcare",
    level: str = "intermediate",
    guidance: str | None = None,
    documents: list[RetrievedDocument] | None = None,
    survivors: list[Finding] | None = None,
    open_threads: list[str] | None = None,
) -> AnalystDeps:
    return AnalystDeps(
        topic=topic,
        level=level,  # ty: ignore[invalid-argument-type]
        guidance=guidance,
        documents=documents if documents is not None else [_doc()],
        survivors=survivors if survivors is not None else [_finding()],
        open_threads=open_threads if open_threads is not None else [],
    )


def _tool_with(output_tools: Sequence[ToolDefinition], prop: str) -> ToolDefinition:
    """The first output tool whose JSON schema declares ``prop`` (union dispatch).

    A ``BriefBody | SkippedNote`` agent registers two output tools; the one
    carrying ``cited_urls`` is the Brief, the one carrying ``detail`` the
    skipped note.
    """
    for tool in output_tools:
        if prop in tool.parameters_json_schema.get("properties", {}):
            return tool
    raise AssertionError(f"no output tool declares {prop!r}")


class AnalystResponder:
    """FunctionModel callback emitting ``(prop, args)`` pairs, one per call.

    Mirrors ``ResearcherResponder``/``OutlineResponder``: ``prop`` selects the
    output tool (``cited_urls`` -> BriefBody, ``detail`` -> SkippedNote).
    """

    __name__ = "analyst_responder"

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


# --- cites_only_read_documents is the identical predicate researcher exports ---


def test_analyst_reuses_researchers_predicate_object_unchanged() -> None:
    # TDD §5.5: "the same check's degenerate case" — imported, never a second
    # spelling. Pin the identity, not just behavioural equivalence.
    from aleph.agents import analyst as analyst_module
    from aleph.agents import researcher as researcher_module

    assert (
        analyst_module.cites_only_read_documents
        is researcher_module.cites_only_read_documents
    )
    assert cites_only_read_documents is researcher_module.cites_only_read_documents


# --- validate_brief_result: BriefBody branch ------------------------------------


def test_validator_passes_brief_citing_only_deps_documents() -> None:
    documents = [_doc(url="https://example.com/a")]
    survivors = [_finding(source_urls=["https://example.com/a"])]
    result = BriefBody.model_validate(_brief_dict())
    assert validate_brief_result(documents, survivors, result) is result


def test_validator_rejects_brief_with_no_cited_urls() -> None:
    # ACCEPTANCE CASE: "a Brief with no cited_urls -> ModelRetry."
    documents = [_doc()]
    survivors = [_finding()]
    result = BriefBody.model_validate(_brief_dict(cited_urls=[]))
    with pytest.raises(ModelRetry) as excinfo:
        validate_brief_result(documents, survivors, result)
    assert "cited_urls" in str(excinfo.value)


def test_validator_rejects_brief_citing_outside_its_deps() -> None:
    # ACCEPTANCE CASE: "a Brief citing outside its Deps -> ModelRetry."
    documents = [_doc(url="https://example.com/a")]
    survivors = [_finding(source_urls=["https://example.com/a"])]
    result = BriefBody.model_validate(
        _brief_dict(cited_urls=["https://example.com/never-read"])
    )
    with pytest.raises(ModelRetry) as excinfo:
        validate_brief_result(documents, survivors, result)
    assert "https://example.com/never-read" in str(excinfo.value)


def test_validator_rejects_brief_when_no_survivors() -> None:
    documents: list[RetrievedDocument] = []
    survivors: list[Finding] = []
    result = BriefBody.model_validate(_brief_dict())
    with pytest.raises(ModelRetry):
        validate_brief_result(documents, survivors, result)


def test_validator_rejects_empty_title() -> None:
    documents = [_doc()]
    survivors = [_finding()]
    result = BriefBody.model_validate(_brief_dict(title="   "))
    with pytest.raises(ModelRetry):
        validate_brief_result(documents, survivors, result)


def test_validator_rejects_empty_body() -> None:
    documents = [_doc()]
    survivors = [_finding()]
    result = BriefBody.model_validate(_brief_dict(body_markdown="   "))
    with pytest.raises(ModelRetry):
        validate_brief_result(documents, survivors, result)


# --- validate_brief_result: SkippedNote branch ----------------------------------


def test_validator_passes_skipped_note_when_no_survivors() -> None:
    result = SkippedNote(detail="the consultation is still open, closing 11 Sept")
    assert validate_brief_result([], [], result) is result


def test_validator_passes_skipped_note_with_empty_detail() -> None:
    # A Beat's very first quiet run may have nothing true to add (module
    # docstring) — an empty detail is legitimate, not itself a violation.
    result = SkippedNote(detail="")
    assert validate_brief_result([], [], result) is result


def test_validator_rejects_skipped_note_when_survivors_present() -> None:
    # ACCEPTANCE CASE: "a SkippedNote returned while survivors are present ->
    # ModelRetry."
    documents = [_doc()]
    survivors = [_finding()]
    result = SkippedNote(detail="Nothing happened.")
    with pytest.raises(ModelRetry):
        validate_brief_result(documents, survivors, result)


# --- THE PADDING TEST: this phase's signature case (TDD §5.4, §11) -------------
#
# With EMPTY `documents`/`survivors` in AnalystDeps, NO `BriefBody` can pass
# validation — `SkippedNote` is the only output that survives. This is proven
# two ways below: (a) hammering the pure `validate_brief_result` function
# directly with adversarial `BriefBody` payloads, and (b) driving the real
# assembled agent with a `FunctionModel` that always tries to pad, past its
# retry budget, to prove the agent-level wiring enforces the same thing.
#
# **This test would still pass if every instruction in `agents/analyst.py`'s
# SYSTEM_PROMPT were deleted.** `validate_brief_result` and
# `build_analyst_agent`'s `@agent.output_validator` read only `ctx.deps.documents`
# / `ctx.deps.survivors` — never the prompt text, never anything the model
# said about why it chose a branch. The prompt tells a *cooperative* model
# what to do; this test is about what happens to an *uncooperative* one, and
# the answer does not depend on a single word of prompt. (The "gut the
# prompt and re-run" experiment described in the ticket is a manual step
# outside this file, run once against this exact test and reverted — see the
# implementation report.)


def _adversarial_brief_dicts() -> list[dict]:
    """A battery of `BriefBody` payloads a padding model might try."""
    return [
        _brief_dict(),  # the "obviously fine-looking" one
        _brief_dict(cited_urls=[]),  # no citations at all
        _brief_dict(cited_urls=["https://example.com/a"]),  # a plausible-looking URL
        _brief_dict(cited_urls=["https://example.com/fabricated-source"]),
        _brief_dict(
            title="Nothing material happened, but here is padding anyway.",
            body_markdown="A confident-sounding paragraph invented from priors.",
            cited_urls=["https://totally-fake.example/article"],
        ),
    ]


def test_padding_test_no_brief_body_passes_with_empty_deps() -> None:
    documents: list[RetrievedDocument] = []
    survivors: list[Finding] = []
    for brief_dict in _adversarial_brief_dicts():
        result = BriefBody.model_validate(brief_dict)
        with pytest.raises(ModelRetry):
            validate_brief_result(documents, survivors, result)
    # The only shape that survives is SkippedNote.
    skipped = SkippedNote(detail="")
    assert validate_brief_result(documents, survivors, skipped) is skipped


def test_padding_test_agent_level_a_padding_model_never_gets_a_brief_through() -> None:
    # A model that ALWAYS emits a plausible-looking BriefBody, never a
    # SkippedNote, no matter how many times it is told to retry: with empty
    # Deps it must exhaust the retry budget and raise, never return a Brief.
    agent = build_analyst_agent()
    padding = _brief_dict(
        title="AI continues to transform healthcare at a rapid pace.",
        body_markdown="Studies show strong accuracy across widespread adoption.",
        cited_urls=["https://example.com/a"],
    )
    respond = AnalystResponder([("cited_urls", padding)])  # reused every call
    with pytest.raises(UnexpectedModelBehavior):
        agent.run_sync(
            "write the brief",
            deps=_deps(documents=[], survivors=[], open_threads=["still open"]),
            model=FunctionModel(respond),
        )
    assert respond.call_count == 4  # 1 initial + 3 retries, then it gives up


def test_padding_test_agent_level_eventually_emits_skipped_note() -> None:
    # The same padding model, this time correcting itself into the only
    # shape that can pass — proving the escape route genuinely exists and is
    # reachable, not merely that everything else is blocked.
    agent = build_analyst_agent()
    padding = _brief_dict()
    skip = {"detail": "the consultation is still open, closing 11 Sept"}
    respond = AnalystResponder([("cited_urls", padding), ("detail", skip)])
    result = agent.run_sync(
        "write the brief",
        deps=_deps(documents=[], survivors=[], open_threads=["still open"]),
        model=FunctionModel(respond),
    ).output
    assert isinstance(result, SkippedNote)
    assert respond.call_count == 2
    assert result.detail == skip["detail"]


# --- build_analyst_prompt -------------------------------------------------------


def test_prompt_lists_survivors_with_index_and_urls() -> None:
    survivors = [
        _finding(claim="First thing.", source_urls=["https://example.com/a"]),
        _finding(claim="Second thing.", source_urls=["https://example.com/b"]),
    ]
    prompt = build_analyst_prompt(_deps(survivors=survivors))
    assert "[1]" in prompt and "First thing." in prompt
    assert "[2]" in prompt and "Second thing." in prompt


def test_prompt_with_no_survivors_says_so_and_omits_findings_block() -> None:
    prompt = build_analyst_prompt(_deps(survivors=[]))
    assert "No findings survived this run" in prompt
    assert "Findings surviving this run" not in prompt


def test_prompt_includes_open_threads_when_present() -> None:
    prompt = build_analyst_prompt(
        _deps(open_threads=["The consultation is still open, closing 11 Sept."])
    )
    assert "The consultation is still open, closing 11 Sept." in prompt


def test_prompt_omits_open_threads_section_when_absent() -> None:
    prompt = build_analyst_prompt(_deps(open_threads=[]))
    assert "Open threads" not in prompt


def test_prompt_includes_guidance_when_present() -> None:
    prompt = build_analyst_prompt(_deps(guidance="Policy, not stock moves."))
    assert "Policy, not stock moves." in prompt


# --- deps construction -----------------------------------------------------------


def test_deps_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="wizard"):
        AnalystDeps(
            topic="t",
            level="wizard",  # ty: ignore[invalid-argument-type]
            guidance=None,
            documents=[],
            survivors=[],
            open_threads=[],
        )


def test_deps_accepts_each_valid_level() -> None:
    for level in ("beginner", "intermediate", "advanced"):
        deps = _deps(level=level)
        assert deps.level == level


def test_deps_is_frozen() -> None:
    deps = _deps()
    with pytest.raises(AttributeError):
        deps.topic = "changed"  # ty: ignore[invalid-assignment]


# --- assembled agent --------------------------------------------------------------


def test_agent_returns_valid_brief_when_survivors_present() -> None:
    agent = build_analyst_agent()
    respond = AnalystResponder([("cited_urls", _brief_dict())])
    result = agent.run_sync(
        "write the brief", deps=_deps(), model=FunctionModel(respond)
    ).output
    assert isinstance(result, BriefBody)
    assert respond.call_count == 1


def test_agent_returns_skipped_note_when_no_survivors() -> None:
    agent = build_analyst_agent()
    respond = AnalystResponder([("detail", {"detail": "still open, closing 11 Sept"})])
    result = agent.run_sync(
        "write the brief",
        deps=_deps(documents=[], survivors=[]),
        model=FunctionModel(respond),
    ).output
    assert isinstance(result, SkippedNote)
    assert respond.call_count == 1


def test_agent_retries_on_unread_citation_then_succeeds() -> None:
    agent = build_analyst_agent()
    bad = _brief_dict(cited_urls=["https://example.com/never-read"])
    good = _brief_dict(cited_urls=["https://example.com/a"])
    respond = AnalystResponder([("cited_urls", bad), ("cited_urls", good)])
    result = agent.run_sync(
        "write the brief",
        deps=_deps(
            documents=[_doc(url="https://example.com/a")],
            survivors=[_finding(source_urls=["https://example.com/a"])],
        ),
        model=FunctionModel(respond),
    ).output
    assert isinstance(result, BriefBody)
    assert respond.call_count == 2
    retry_text = _retry_prompt_text(respond.messages_per_call[1])
    assert "https://example.com/never-read" in retry_text


def test_agent_system_prompt_is_level_scoped() -> None:
    agent = build_analyst_agent()
    prompts: dict[str, str] = {}
    for level in ("beginner", "intermediate", "advanced"):
        respond = AnalystResponder([("cited_urls", _brief_dict())])
        agent.run_sync(
            "write the brief", deps=_deps(level=level), model=FunctionModel(respond)
        )
        system_parts = [
            part.content
            for message in respond.messages_per_call[0]
            for part in getattr(message, "parts", [])
            if part.__class__.__name__ == "SystemPromptPart"
        ]
        prompts[level] = "\n".join(system_parts)
    assert len({prompts["beginner"], prompts["intermediate"], prompts["advanced"]}) == 3
