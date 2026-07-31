"""Fixtures shared across the backend unit suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aleph.config import Settings


@pytest.fixture
def restored_live_settings() -> Iterator[Settings]:
    """The live ``config.settings`` singleton, put back after the test.

    ``scripts/e2e_backend.create_stub_app`` — the factory the Playwright harness
    boots — mutates the module-level singleton *in place*, so any test that boots
    it has to restore it: an escaped ``stub`` would silently reconfigure the rest
    of this worker's tests. The whole singleton is snapshotted rather than the
    handful of fields under assertion, so whatever fields the factory grows next
    are restored too.
    """
    from aleph.config import settings as live_settings

    snapshot = live_settings.model_dump()
    try:
        yield live_settings
    finally:
        for name, value in snapshot.items():
            setattr(live_settings, name, value)
