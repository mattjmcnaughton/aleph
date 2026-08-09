"""The researcher agent's `Deps` contract — currently just `RetrievedDocument`.

**AL-512 scope note.** This ticket (epic #163's retrieval seam) builds only
`RetrievedDocument` — the frozen shape a retrieved document takes once it
reaches an agent. The researcher agent itself (`documents -> Findings |
Refusal`, TDD §5.3) is AL-520's job; this module exists now because
`RetrievedDocument` is part of *its* `Deps` contract (TDD §3, §5.2) and the
shape has to live where the contract lives, even though the contract's other
half does not exist yet.

**Why this shape lives in `agents/`, not `services/`.** TDD §3's structural
claim: "`agents/` reaches no provider." The retrieval seam
(`services/retrieval.py`) is what talks to a `Retriever`; an agent only ever
sees documents as plain frozen dataclasses handed to it in `Deps`. So
`RetrievedDocument` is *declared* here — it is part of the researcher agent's
contract — and *populated* by `services/retrieval.py`, which imports it. That
import direction (services -> agents, never back) is `agents/flashcard.py`'s
`FlashcardCaps` precedent exactly: the shape belongs to the agent, the
population belongs to the service.

Consequently this module binds **no model** and imports **nothing** from any
application layer (no `services`, `routers`, `config`, `repositories`,
`models`, `db`, `fastapi`, or `sqlalchemy`) — the same purity rule every other
`agents/*.py` module follows, verbatim. `tests/unit/test_agents_layering.py`
auto-discovers every module under `aleph.agents` and asserts this by running a
fresh-interpreter import probe, so no test file needed editing to cover it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RetrievedDocument:
    """One document a `Retriever` returned, on its way into an agent's `Deps`.

    Every field is metadata the *service* fills in from what the retrieval
    provider returned — never a model output (TDD §5.5: "a Source's metadata
    is never model-written"). `published_on` is `None` until
    `services/retrieval.py`'s `retrieve()` has run: it drops any document with
    no publication date (PRD §4.4 requires one to show and reason about), so
    by the time a document reaches an agent's `Deps` this field is always set
    — the type stays `date | None` because the raw, pre-filter shape a
    `Retriever.search()` may return can legitimately lack one.

    Frozen and stdlib-only (no Pydantic), matching `FlashcardCaps`'s
    precedent for a value that crosses the services -> agents boundary as
    plain data.
    """

    url: str
    publisher: str
    title: str
    published_on: date | None
    text: str
