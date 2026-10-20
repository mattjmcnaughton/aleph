"""Exercise the real script against a stateful fake flyctl executable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parents[2] / "scripts/recreate_schedules.py"
IMAGE = "registry.fly.io/test-app:deployment-exact"


@pytest.fixture
def fake_fly(tmp_path: Path):
    cli = tmp_path / "flyctl"
    cli.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ["FAKE_STATE"])
s = json.loads(p.read_text())
a = sys.argv[1:]
s["calls"].append(a)
p.write_text(json.dumps(s))
if os.environ.get("FAKE_FAIL") == a[1]:
    sys.exit(7)
if a[:2] == ["machine", "list"]:
    print(json.dumps(s["machines"]))
elif a[:2] == ["machine", "destroy"]:
    s["machines"] = [m for m in s["machines"] if m["id"] != a[2]]
elif a[:2] == ["machine", "run"]:
    s["machines"].append({"id": "new123", "config": {
        "metadata": {"aleph_schedule": "tick"}, "image": a[a.index("--") + 1]}})
else:
    sys.exit(8)
p.write_text(json.dumps(s))
""")
    cli.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "calls": [],
                "machines": [
                    {
                        "id": "web123",
                        "config": {"metadata": {"fly_process_group": "app"}},
                    },
                    {"id": "old123", "config": {"metadata": {"aleph_schedule": "old"}}},
                    {"id": "safe123", "metadata": {"aleph_schedule": "wrong-level"}},
                ],
            }
        )
    )
    (tmp_path / "fly.toml").write_text("""app = "test-app"
primary_region = "ewr"
[env]
ENV = "production"
SPACES = "a value with spaces"
""")
    (tmp_path / "schedules.toml").write_text("""[[schedule]]
name = "tick"
schedule = "hourly"
command = ["python", "-m", "aleph.jobs.tick"]
memory = "512mb"
""")
    log = tmp_path / "deploy.log"
    log.write_text(f"build done\nimage: {IMAGE}\nSuccessfully deployed\n")
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_STATE": str(state),
    }
    command = [
        sys.executable,
        str(SCRIPT),
        "--fly-config",
        str(tmp_path / "fly.toml"),
        "--schedules",
        str(tmp_path / "schedules.toml"),
        "--deploy-log",
        str(log),
    ]
    return command, env, state, log


def test_recreate_exact_image_scoped_ids_env_and_repeat_run(fake_fly) -> None:
    command, env, state, _ = fake_fly
    for _ in range(2):
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    data = json.loads(state.read_text())
    assert {m["id"] for m in data["machines"]} == {"web123", "safe123", "new123"}
    destroys = [c for c in data["calls"] if c[1] == "destroy"]
    assert destroys == [
        ["machine", "destroy", "old123", "--app", "test-app", "--force"],
        ["machine", "destroy", "new123", "--app", "test-app", "--force"],
    ]
    runs = [c for c in data["calls"] if c[1] == "run"]
    assert (
        runs[0]
        == runs[1]
        == [
            "machine",
            "run",
            "--app",
            "test-app",
            "--region",
            "ewr",
            "--schedule",
            "hourly",
            "--restart",
            "no",
            "--vm-memory",
            "512",
            "--metadata",
            "aleph_schedule=tick",
            "--detach",
            "--env",
            "ENV=production",
            "--env",
            "SPACES=a value with spaces",
            "--",
            IMAGE,
            "python",
            "-m",
            "aleph.jobs.tick",
        ]
    )


@pytest.mark.parametrize("failure", ["list", "destroy", "run"])
def test_cli_failure_is_visible_and_stops_execution(fake_fly, failure) -> None:
    command, env, state, _ = fake_fly
    result = subprocess.run(
        command, env={**env, "FAKE_FAIL": failure}, capture_output=True
    )
    assert result.returncode != 0
    assert json.loads(state.read_text())["calls"][-1][1] == failure


@pytest.mark.parametrize(
    "log_text", ["no image", "image: -bad", "image: one\nimage: two"]
)
def test_bad_deploy_output_never_mutates_machines(fake_fly, log_text) -> None:
    command, env, state, log = fake_fly
    log.write_text(log_text)
    result = subprocess.run(command, env=env, capture_output=True)
    assert result.returncode != 0
    assert json.loads(state.read_text())["calls"] == []


def test_release_recreation_stays_behind_main_only_fences() -> None:
    workflow = yaml.load(
        (SCRIPT.parents[1] / ".github/workflows/release.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["on"]["workflow_run"]["branches"] == ["main"]
    release = workflow["jobs"]["release"]
    assert "head_branch == 'main'" in release["if"]
    assert "conclusion == 'success'" in release["if"]
    deploy = workflow["jobs"]["deploy"]
    assert "new-release-published == 'true'" in deploy["if"]
    assert "github.event_name == 'workflow_run'" in deploy["if"]
    assert deploy["concurrency"] == "deploy-aleph-prod-mattjmcnaughton"
    deploy_step = next(s for s in deploy["steps"] if s.get("name") == "Deploy")
    assert deploy_step["shell"] == "bash"
    assert "set -euo pipefail" in deploy_step["run"]
    assert 'tee "$RUNNER_TEMP/aleph-deploy.log"' in deploy_step["run"]
    recreate_index = next(
        i
        for i, s in enumerate(deploy["steps"])
        if "recreate_schedules.py" in s.get("run", "")
    )
    assert recreate_index > deploy["steps"].index(deploy_step)
