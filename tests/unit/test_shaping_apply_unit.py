"""The apply/undo service's two pure-ish pieces (AL-321): the lock, the kinds.

Everything else about apply and undo is a transaction and belongs in the
integration suite (``tests/integration/test_shaping_apply.py``). These two do
not need a database and are worth pinning here because both are quiet-failure
shaped: a lock that does not exclude reads as "it worked", and a history sheet
that names the wrong edit shape looks like a rendering nit rather than a lost
fact.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from aleph.models import PathChangeKind
from aleph.services.shaping import PathApplyLock, change_kinds


def _change(payload: dict[str, Any], kind: PathChangeKind) -> Any:
    """A stand-in for a ``PathChange`` row — ``change_kinds`` reads two fields."""
    return SimpleNamespace(payload=payload, kind=kind)


# --------------------------------------------------------------------------- #
# The per-path apply lock (D11)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_lock_serializes_two_applies_on_one_path() -> None:
    """ "Concurrent applies — one wins" is built on this waiting, not on refusing."""
    locks = PathApplyLock()
    path_id = uuid.uuid4()
    order: list[str] = []

    async def hold(name: str, pause: float) -> None:
        async with locks.hold(path_id):
            order.append(f"{name}:enter")
            await asyncio.sleep(pause)
            order.append(f"{name}:exit")

    await asyncio.gather(hold("first", 0.01), hold("second", 0))

    assert order == ["first:enter", "first:exit", "second:enter", "second:exit"]


@pytest.mark.anyio
async def test_two_paths_do_not_contend() -> None:
    """It guards a *path*, not the app: shaping one path never blocks another."""
    locks = PathApplyLock()
    entered = asyncio.Event()

    async def outer() -> None:
        async with locks.hold(uuid.uuid4()):
            entered.set()
            await asyncio.sleep(0.05)

    async def inner() -> None:
        await entered.wait()
        async with locks.hold(uuid.uuid4()):
            pass

    await asyncio.wait_for(asyncio.gather(outer(), inner()), timeout=1)


@pytest.mark.anyio
async def test_the_registry_is_the_contended_set_not_every_path_ever_applied() -> None:
    """It prunes on the way out, so a long-lived process does not accumulate."""
    locks = PathApplyLock()
    first, second = uuid.uuid4(), uuid.uuid4()

    async with locks.hold(first):
        assert locks._locks.keys() == {first}
        async with locks.hold(second):
            assert locks._locks.keys() == {first, second}
        assert locks._locks.keys() == {first}
    assert locks._locks == {}


@pytest.mark.anyio
async def test_a_waiter_keeps_the_lock_alive_for_its_holder() -> None:
    """Pruning must not hand a *second* lock out while the first is still held.

    The entry is dropped only when the last waiter leaves; dropping it eagerly
    would let a queued coroutine construct a fresh ``asyncio.Lock`` for a path
    somebody is mid-transaction on — two applies inside one "lock".
    """
    locks = PathApplyLock()
    path_id = uuid.uuid4()
    inside = asyncio.Event()
    release = asyncio.Event()
    overlapped = False

    async def holder() -> None:
        async with locks.hold(path_id):
            inside.set()
            await release.wait()

    async def waiter() -> None:
        nonlocal overlapped
        await inside.wait()
        task = asyncio.create_task(_enter(locks, path_id))
        await asyncio.sleep(0)
        overlapped = task.done()
        release.set()
        await task

    async def _enter(registry: PathApplyLock, target: uuid.UUID) -> None:
        async with registry.hold(target):
            pass

    await asyncio.gather(holder(), waiter())

    assert overlapped is False, "a waiter got in while the holder was inside"
    assert locks._locks == {}


@pytest.mark.anyio
async def test_a_failure_inside_the_lock_still_releases_it() -> None:
    """A stale apply raises a ``409`` from inside the block; the path must free."""
    locks = PathApplyLock()
    path_id = uuid.uuid4()

    with pytest.raises(RuntimeError):
        async with locks.hold(path_id):
            raise RuntimeError("stale")

    assert locks._locks == {}
    async with locks.hold(path_id):
        pass


# --------------------------------------------------------------------------- #
# Derived edit shapes for the history sheet (§6)
# --------------------------------------------------------------------------- #


def test_kinds_are_derived_structurally_from_the_stored_operations() -> None:
    """An Addition carries ``lessons``, a Revision carries ``lesson_id`` (D1)."""
    change = _change(
        {
            "operations": [
                {"insert_at_position": 2, "lessons": [{"title": "New"}]},
                {"lesson_id": "abc", "instruction": "Re-teach it."},
            ]
        },
        PathChangeKind.ADD_LESSONS,
    )

    assert change_kinds(change) == [
        PathChangeKind.ADD_LESSONS,
        PathChangeKind.REVISE_LESSON,
    ]


def test_kinds_are_deduplicated_and_keep_payload_order() -> None:
    change = _change(
        {
            "operations": [
                {"lesson_id": "a", "instruction": "x"},
                {"insert_at_position": 1, "lessons": [{"title": "T"}]},
                {"lesson_id": "b", "instruction": "y"},
            ]
        },
        PathChangeKind.ADD_LESSONS,
    )

    assert change_kinds(change) == [
        PathChangeKind.REVISE_LESSON,
        PathChangeKind.ADD_LESSONS,
    ]


def test_an_unreadable_payload_falls_back_to_the_rows_own_column() -> None:
    """The history is the learner's record; a blank kind is worse than a coarse one."""
    assert change_kinds(_change({}, PathChangeKind.REVISE_LESSON)) == [
        PathChangeKind.REVISE_LESSON
    ]
    assert change_kinds(
        _change({"operations": ["not an object"]}, PathChangeKind.ADD_LESSONS)
    ) == [PathChangeKind.ADD_LESSONS]
