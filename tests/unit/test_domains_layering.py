"""Guard the domains-package layering rule (`domains/__init__.py`'s contract,
TDD D9's "the layering test covers it for free").

``aleph.domains`` modules are pure derivation — stdlib only, no ORM, no I/O, no
session, and no application layer above or beside them (`services/`,
`repositories/`, `agents/`, `routers/`), nor the frameworks those layers are
built on (FastAPI, SQLAlchemy) or the model library `agents/` depends on
(pydantic). The rule is otherwise enforced only by docstrings — every
`domains/` module's own module docstring asserts it verbatim — so this test
catches a future convenience import silently regressing it, mirroring
``test_agents_layering.py``'s import probe for the sibling package.

The probe runs in a fresh interpreter: importing anything inside the pytest
process would see modules pre-loaded by conftest/other tests and prove
nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys

# Module prefixes the domains package must never pull in: the application
# layers above/beside it, plus the frameworks/model library those layers are
# built on. Mirrors `test_agents_layering.py`'s `_FORBIDDEN_PREFIXES`, with
# `aleph.agents` added (domains/ must not import agents/ either, per
# `domains/novelty.py`'s own docstring) and `pydantic` added (the model
# library `domains/__init__.py`'s "no third-party model library" contract
# names explicitly).
_FORBIDDEN_PREFIXES = (
    "aleph.services",
    "aleph.routers",
    "aleph.config",
    "aleph.repositories",
    "aleph.models",
    "aleph.agents",
    "aleph.db",
    "fastapi",
    "sqlalchemy",
    "pydantic",
)

# Auto-discover every module in ``aleph.domains`` and import them all, so a
# future ``domains/whatever.py`` is covered without editing this test — the
# same "guard the whole package" shape as the agents probe.
_PROBE = f"""\
import importlib
import json
import pkgutil
import sys

import aleph.domains

for _mod in pkgutil.iter_modules(aleph.domains.__path__, aleph.domains.__name__ + "."):
    importlib.import_module(_mod.name)

prefixes = {_FORBIDDEN_PREFIXES!r}
loaded = sorted(name for name in sys.modules if name.startswith(prefixes))
print(json.dumps(loaded))
"""


def test_domains_package_imports_stdlib_only() -> None:
    """Every module in ``aleph.domains`` imports no application layer, no
    FastAPI/SQLAlchemy, and no pydantic — verified against the *existing*
    modules (`changes`, `engagement`, `grading`, `progression`, `scheduling`,
    `streaks`) as well as the two this ticket added (`cadence`, `novelty`).
    All of them pass today: every one already declares (and keeps) a
    stdlib-only import list.
    """
    # ``check=True`` would raise CalledProcessError and swallow the probe's
    # stderr; assert on returncode explicitly so an import error in the probe
    # shows its traceback.
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"domains-import probe failed (rc={result.returncode}):\n{result.stderr}"
    )
    forbidden = json.loads(result.stdout)
    assert forbidden == [], (
        f"aleph.domains pulled in forbidden application-layer modules: {forbidden}"
    )
