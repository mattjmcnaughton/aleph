"""The iOS workflow's helper scripts (iOS TDD §17.1).

``.github/workflows/ios.yml`` delegates three decisions to small scripts under
``clients/ios/scripts/`` so they can be tested here, on Linux, without a macOS
runner:

- ``ios-relevant.sh``: the home-written path filter. A backend-only change must
  skip the iOS jobs; anything that can affect the iOS client must run them.
- ``check-warnings.sh``: "build with warnings as errors", for the app's own Swift
  only. The committed generated client warns under Swift 6.2 and is exempt.
- ``create-simulator.sh``: the pinned simulator, created rather than picked, and a
  loud failure naming what exists when the runner image lacks it.
- ``commit-back.sh``: record mode on ``ios-dev/*`` commits the PNGs back.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "clients" / "ios"
SCRIPTS = IOS / "scripts"
GENERATED = IOS / "Packages" / "API" / "Sources" / "API" / "Generated"


def _run(
    script: str,
    *args: str,
    stdin: str = "",
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
        check=False,
    )


# --- ios-relevant.sh ------------------------------------------------------------


@pytest.mark.parametrize(
    "changed",
    [
        "clients/ios/Packages/API/Package.swift",
        "clients/ios/project.yml",
        "clients/openapi.json",
        "scripts/e2e_backend.py",
        # The UI tests' fake issuer and its committed key (iOS TDD D22).
        "scripts/e2e_fake_issuer.py",
        "tests/fixtures/e2e_oidc/signing_key.jwk",
        ".github/workflows/ios.yml",
    ],
)
def test_an_ios_relevant_change_runs_the_ios_jobs(changed: str) -> None:
    result = _run("ios-relevant.sh", stdin=f"src/aleph/app.py\n{changed}\n")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"


@pytest.mark.parametrize(
    "changed",
    [
        "src/aleph/app.py\ntests/unit/test_app.py\n",
        "clients/web/src/main.tsx\n",
        ".github/workflows/ci.yml\n",
        "docs/ci.md\n",
        # Near misses: each pattern is anchored.
        "clients/iosx/README.md\n",
        "clients/openapi.json.bak\n",
        "docs/clients/ios/notes.md\n",
        "scripts/e2e_backend.pyc\n",
        "scripts/e2e_fake_issuer.pyc\n",
        "tests/fixtures/other/key.jwk\n",
        "",
    ],
)
def test_a_change_that_cannot_affect_ios_skips_the_ios_jobs(changed: str) -> None:
    result = _run("ios-relevant.sh", stdin=changed)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"


# --- check-warnings.sh ----------------------------------------------------------


def _log(tmp_path: Path, *lines: str) -> Path:
    log = tmp_path / "xcodebuild.log"
    log.write_text(
        "\n".join(["CompileSwift normal arm64", *lines, "** BUILD SUCCEEDED **"]) + "\n"
    )
    return log


def test_a_clean_build_log_passes(tmp_path: Path) -> None:
    result = _run("check-warnings.sh", str(_log(tmp_path)))

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_swift_warning_in_app_code_fails(tmp_path: Path) -> None:
    line = (
        f"{IOS}/Packages/Core/Sources/Core/Path.swift:12:9: "
        "warning: variable 'x' was never used"
    )
    result = _run("check-warnings.sh", str(_log(tmp_path, line, line)))

    assert result.returncode == 1
    # Reported once, even though xcodebuild repeats it.
    assert result.stdout.count("Path.swift:12:9") == 1


def test_generated_client_warnings_are_exempt(tmp_path: Path) -> None:
    line = (
        f"{GENERATED}/Types.swift:10:1: warning: package import of 'Foundation' "
        "was not used in package declarations"
    )
    result = _run("check-warnings.sh", str(_log(tmp_path, line)))

    assert result.returncode == 0, result.stdout + result.stderr


def test_warnings_outside_the_ios_client_are_ignored(tmp_path: Path) -> None:
    result = _run(
        "check-warnings.sh",
        str(
            _log(
                tmp_path,
                # A third-party package checkout and a DerivedData-generated file.
                f"{IOS}/build/SourcePackages/checkouts/AppAuth-iOS/Sources/AppAuth.swift:3:1:"
                " warning: deprecated",
                "/Users/runner/Library/Developer/Xcode/DerivedData/Aleph/Build/"
                "GeneratedAssetSymbols.swift:1:1: warning: something",
                "warning: Metadata extraction skipped. "
                "No AppIntents.framework dependency found.",
            )
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_missing_log_is_an_error_not_a_pass(tmp_path: Path) -> None:
    result = _run("check-warnings.sh", str(tmp_path / "missing.log"))

    assert result.returncode == 2


# --- create-simulator.sh --------------------------------------------------------

SIMCTL_LIST = {
    "devicetypes": [
        {
            "name": "iPhone 17",
            "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17",
        },
        {
            "name": "iPhone 17 Pro",
            "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
        },
    ],
    "runtimes": [
        {
            "name": "iOS 26.5",
            "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-26-5",
            "isAvailable": True,
            "platform": "iOS",
        },
        {
            "name": "iOS 26.6",
            "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-26-6",
            "isAvailable": True,
            "platform": "iOS",
        },
    ],
}


@pytest.fixture
def fake_xcrun(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """An ``xcrun`` on PATH: answers ``simctl list -j``, records ``simctl create``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps(SIMCTL_LIST))
    calls = tmp_path / "create-calls.txt"
    xcrun = bin_dir / "xcrun"
    xcrun.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2" = "simctl list" ]; then cat "$FAKE_LIST"; exit 0; fi\n'
        'if [ "$1 $2" = "simctl create" ]; then shift 2;'
        ' printf "%s\\n" "$@" > "$FAKE_CALLS"; echo FAKE-UDID-1234; exit 0; fi\n'
        "exit 64\n"
    )
    xcrun.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LIST": str(listing),
        "FAKE_CALLS": str(calls),
    }
    return env, calls


def test_creates_the_pinned_simulator_and_prints_its_udid(
    fake_xcrun: tuple[dict[str, str], Path],
) -> None:
    env, calls = fake_xcrun

    result = _run("create-simulator.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FAKE-UDID-1234"
    assert calls.read_text().splitlines() == [
        "aleph-ci",
        "com.apple.CoreSimulator.SimDeviceType.iPhone-17",
        "com.apple.CoreSimulator.SimRuntime.iOS-26-5",
    ]


def test_a_missing_runtime_fails_and_names_what_exists(
    fake_xcrun: tuple[dict[str, str], Path],
) -> None:
    env, calls = fake_xcrun

    result = _run("create-simulator.sh", env={**env, "SIM_RUNTIME": "iOS 27.0"})

    assert result.returncode == 1
    assert "iOS 27.0" in result.stderr
    assert "iOS 26.5" in result.stderr and "iOS 26.6" in result.stderr
    assert not calls.exists()


def test_a_missing_device_type_fails_and_names_what_exists(
    fake_xcrun: tuple[dict[str, str], Path],
) -> None:
    env, calls = fake_xcrun

    result = _run("create-simulator.sh", env={**env, "SIM_DEVICE": "iPhone 99"})

    assert result.returncode == 1
    assert "iPhone 99" in result.stderr
    assert "iPhone 17 Pro" in result.stderr
    assert not calls.exists()


# --- commit-back.sh -------------------------------------------------------------

BRANCH = "ios-dev/topic"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def ios_dev_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A clone of a bare remote, on an ``ios-dev`` branch, as CI checks it out."""
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-q", str(remote), cwd=tmp_path)
    work = tmp_path / "work"
    _git("clone", "-q", str(remote), str(work), cwd=tmp_path)
    _git("checkout", "-q", "-b", BRANCH, cwd=work)
    (work / "README.md").write_text("aleph\n")
    _git("add", "README.md", cwd=work)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "init",
        cwd=work,
    )
    _git("push", "-q", "origin", BRANCH, cwd=work)
    return work, remote


def test_recorded_pngs_are_committed_back_to_the_branch(
    ios_dev_checkout: tuple[Path, Path],
) -> None:
    work, remote = ios_dev_checkout
    snapshots = work / "clients" / "ios" / "AlephSnapshotTests" / "__Snapshots__"
    snapshots.mkdir(parents=True)
    (snapshots / "PathView.dark.png").write_bytes(b"png")

    result = _run(
        "commit-back.sh",
        BRANCH,
        "clients/ios/AlephSnapshotTests/__Snapshots__",
        cwd=work,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    subject = _git("log", "-1", "--format=%s", BRANCH, cwd=remote)
    assert subject == (
        "chore(ios): record snapshot references and screenshots [skip ci]"
    )
    files = _git("show", "--name-only", "--format=", BRANCH, cwd=remote)
    assert files == "clients/ios/AlephSnapshotTests/__Snapshots__/PathView.dark.png"


def test_nothing_recorded_commits_nothing(ios_dev_checkout: tuple[Path, Path]) -> None:
    work, remote = ios_dev_checkout
    before = _git("rev-parse", BRANCH, cwd=remote)

    # A path that was never created is not an error.
    result = _run("commit-back.sh", BRANCH, "clients/ios/screenshots", cwd=work)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git("rev-parse", BRANCH, cwd=remote) == before


def test_a_build_that_touched_other_tracked_files_still_commits_back(
    ios_dev_checkout: tuple[Path, Path],
) -> None:
    """The build can modify tracked files outside the recorded paths (Xcode rewrites
    String Catalogs, for one); they are neither committed nor allowed to block the
    rebase onto a concurrent commit-back from the other macOS job."""
    work, remote = ios_dev_checkout
    # The other job's commit-back lands first.
    other = work.parent / "other"
    _git("clone", "-q", "--branch", BRANCH, str(remote), str(other), cwd=work.parent)
    (other / "screenshot.png").write_bytes(b"png")
    _git("add", "screenshot.png", cwd=other)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "other job",
        cwd=other,
    )
    _git("push", "-q", "origin", BRANCH, cwd=other)
    # This job's build modified a tracked file and recorded a snapshot.
    (work / "README.md").write_text("rewritten by the build\n")
    snapshots = work / "clients" / "ios" / "AlephSnapshotTests" / "__Snapshots__"
    snapshots.mkdir(parents=True)
    (snapshots / "PathView.dark.png").write_bytes(b"png")

    result = _run(
        "commit-back.sh",
        BRANCH,
        "clients/ios/AlephSnapshotTests/__Snapshots__",
        cwd=work,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    files = _git("show", "--name-only", "--format=", BRANCH, cwd=remote)
    assert files == "clients/ios/AlephSnapshotTests/__Snapshots__/PathView.dark.png"
    assert _git("show", f"{BRANCH}:README.md", cwd=remote) == "aleph"
