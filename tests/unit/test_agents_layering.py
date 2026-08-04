"""Guard the agents-package layering rule (CLAUDE.md, TDD §5.1).

``aleph.agents`` modules assemble pydantic-ai agents with **no bound model** and
must stay importable with no FastAPI, configuration, or database anywhere in
their import graph — that purity is what lets services inject a model at run
time and eval harnesses import them directly. The rule is otherwise enforced
only by docstrings, so this test catches a future convenience import silently
regressing it (mirrors habagou's ``test_agents_layering``).

The probe runs in a fresh interpreter: importing anything inside the pytest
process would see modules pre-loaded by conftest/other tests and prove nothing.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys

from aleph.agents.flashcard import FlashcardDeps
from aleph.agents.lesson import LessonDeps
from aleph.agents.outline import OutlineDeps
from aleph.agents.shaper import ShaperDeps
from aleph.agents.tutor import TutorDeps

# Module prefixes the agents package must never pull in: the application layers
# above/beside it, plus the frameworks those layers are built on.
_FORBIDDEN_PREFIXES = (
    "aleph.services",
    "aleph.routers",
    "aleph.config",
    "aleph.repositories",
    "aleph.models",
    "aleph.db",
    "fastapi",
    "sqlalchemy",
)

# Auto-discover every module in ``aleph.agents`` and import them all, so a
# future ``agents/judge.py`` (or any new agent) is covered without editing this
# test (thermo-3). Hand-enumerating outline+lesson would let a new agent smuggle
# in a forbidden import unguarded.
_PROBE = f"""\
import importlib
import json
import pkgutil
import sys

import aleph.agents

for _mod in pkgutil.iter_modules(aleph.agents.__path__, aleph.agents.__name__ + "."):
    importlib.import_module(_mod.name)

prefixes = {_FORBIDDEN_PREFIXES!r}
loaded = sorted(name for name in sys.modules if name.startswith(prefixes))
print(json.dumps(loaded))
"""


def test_agents_package_imports_without_app_layers() -> None:
    # ``check=True`` would raise CalledProcessError and swallow the probe's
    # stderr; assert on returncode explicitly so an import error in the probe
    # shows its traceback (thermo-4).
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"agents-import probe failed (rc={result.returncode}):\n{result.stderr}"
    )
    forbidden = json.loads(result.stdout)
    assert forbidden == [], (
        f"aleph.agents pulled in forbidden application-layer modules: {forbidden}"
    )


# --- the display title is never a generation input (CONTEXT.md: Path title) ----
#
# A different flavour of layering violation from the import guard above: not a
# forbidden import, but a display-layer *value* — the learner-editable Path
# title — leaking into a generation-layer input. Lives here (not
# ``test_outline_agent.py``) because it is a whole-package invariant, not one
# agent's: it inspects every ``*Deps`` dataclass across outline/lesson/tutor/
# shaper/flashcard in one place, the same "guard the whole package" role the
# import probe above plays for imports.

# Fields that legitimately carry "title" in their name: real per-lesson/unit
# titles threaded through as generation context (``agents/lesson.py``'s
# ``LessonDeps.unit_title``/``lesson_title``, mirrored on ``TutorDeps``) — never
# the path's own display title. Extend this, deliberately, rather than loosen
# the substring check below.
_ALLOWED_TITLE_FIELDS = frozenset({"unit_title", "lesson_title"})


def test_no_agent_deps_carries_a_path_title_field() -> None:
    """Structural pin: the Path's display ``title`` never reaches an agent prompt.

    ``Path title`` (CONTEXT.md) is a *display* label, never a generation input
    — unlike ``topic``/``guidance``/``level``, which every agent's deps do
    carry. This is enforced by construction (the field is simply absent from
    every ``*Deps`` dataclass, so no prompt-building code has anywhere to read
    it from), and this test is what keeps that true: a future change that adds
    a title-shaped field to any of these would fail here first, forcing the
    decision into the open rather than quietly leaking a display label into a
    model prompt.

    Matches any field name **containing** ``"title"`` (not just a field named
    exactly ``title``), against an explicit allowlist of the legitimate ones —
    an exact-match check would let a future ``path_title``/``display_title``
    field slip through unnoticed.
    """
    for deps_cls in (OutlineDeps, LessonDeps, TutorDeps, ShaperDeps, FlashcardDeps):
        for f in dataclasses.fields(deps_cls):
            if "title" not in f.name:
                continue
            assert f.name in _ALLOWED_TITLE_FIELDS, (
                f"{deps_cls.__name__}.{f.name} carries 'title' in its name and is "
                "not on the allowlist — if this is the path's display title, it "
                "must never be a generation input (CONTEXT.md: Path title); if it "
                "is a legitimate per-lesson/unit title, add it to "
                "_ALLOWED_TITLE_FIELDS explicitly."
            )
