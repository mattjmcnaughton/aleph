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

import json
import subprocess
import sys

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
