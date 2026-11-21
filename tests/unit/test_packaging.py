"""Prove the wheel ships only ``src/aleph`` — no tests, scripts, or docs.

`tests/` and `scripts/` are development tooling that imports the application
package but must never ride along into the production image. That guarantee
is one line of build config, which is exactly the kind of line a future
refactor changes without noticing — so it is asserted twice: once against the
declared config (instant, unconditional) and once against a real built wheel
(~0.4s, the thing that actually ships).

The wheel build is the honest check; the config assertion is the one that still
runs, with a legible failure, in an environment without ``uv`` on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Every dev-only directory that sits beside `src/` and must stay out of the wheel.
_NEVER_PACKAGED = ("tests", "scripts", "docs", "alembic")


def _pyproject() -> dict:
    return tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_wheel_build_config_packages_only_the_application() -> None:
    """hatch is told explicitly which package to ship — nothing else can slip in."""
    wheel_config = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel_config["packages"] == ["src/aleph"]


def test_built_wheel_contains_no_dev_only_directories() -> None:
    """Build the real wheel and inspect it — the check that cannot be fooled."""
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv is always present in the gate
        pytest.skip("uv is not on PATH; the build-config assertions still apply")

    out_dir = _REPO_ROOT / ".artifacts" / "packaging-check"
    shutil.rmtree(out_dir, ignore_errors=True)
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, f"uv build failed:\n{build.stderr}"

    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()

    assert names, "the wheel is empty"
    for name in names:
        top_level = name.split("/", 1)[0]
        assert top_level not in _NEVER_PACKAGED, f"dev-only file shipped: {name}"
        # Belt and braces: everything that ships is either the application
        # package or wheel metadata.
        assert top_level == "aleph" or top_level.endswith(".dist-info"), (
            f"unexpected top-level entry in the wheel: {name}"
        )

    shutil.rmtree(out_dir, ignore_errors=True)
