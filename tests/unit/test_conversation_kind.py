"""Every conversation query must *name* its kind (Phase 2B TDD D3).

A path carries two threads — its in-lesson thread and its **Shaping
conversation** — and :mod:`aleph.repositories.conversations` says every query
names which. A default would make that a docstring rather than a rule: a
shaping caller that forgot ``kind`` would silently read and clear the *lesson*
thread, which is the wrong-thread bug PRD §5.8 forbids, and it would type-check.

So the kind is a required keyword-only parameter, and this pins it. The type
checker cannot: re-introducing a default is a perfectly well-typed edit.
"""

from __future__ import annotations

import inspect

from aleph.repositories.conversations import ConversationRepository

# The queries that select a thread. ``insert_turn`` is deliberately absent: it
# takes a ``conversation_id``, which already names one specific thread.
KIND_SCOPED_METHODS = (
    "get_for_path",
    "upsert_for_path",
    "load_thread",
    "delete_for_path",
)


def test_every_thread_query_requires_an_explicit_kind() -> None:
    parameters = {
        name: inspect.signature(getattr(ConversationRepository, name)).parameters[
            "kind"
        ]
        for name in KIND_SCOPED_METHODS
    }

    assert {
        name: (parameter.kind, parameter.default)
        for name, parameter in parameters.items()
    } == {
        name: (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.empty)
        for name in KIND_SCOPED_METHODS
    }
