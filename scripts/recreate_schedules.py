"""Recreate only aleph_schedule-tagged Machines after a successful deployment.

The caller must first check flyctl deploy's exit status. Its captured output is
the image source: a 'latest release' lookup can return a failed newer release.
Uses Python's standard library only so the release runner needs no app install.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path


def recreate(fly_config: Path, schedules_file: Path, deploy_log: Path) -> None:
    config = tomllib.loads(fly_config.read_text())
    schedules = tomllib.loads(schedules_file.read_text())["schedule"]
    app, region = config["app"], config["primary_region"]
    images = set(re.findall(r"^\s*image: (\S+)\s*$", deploy_log.read_text(), re.M))
    if len(images) != 1:
        raise ValueError(
            "Expected one exact image from successful flyctl deploy output"
        )
    image = images.pop()
    if not re.fullmatch(
        rf"registry\.fly\.io/{re.escape(app)}:deployment-[\w-]+", image
    ):
        raise ValueError(
            "Deploy image must be an immutable Fly deployment tag for this app"
        )

    # Validate every entry before any destructive operation, including removals
    # of schedules no longer present in the file.
    names = set()
    commands = []
    for schedule in schedules:
        name = schedule["name"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or name in names:
            raise ValueError("Schedule names must be unique safe identifiers")
        names.add(name)
        if schedule["schedule"] not in {"hourly", "daily", "weekly", "monthly"}:
            raise ValueError("Unsupported Fly schedule")
        command = schedule["command"]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(arg, str) and arg for arg in command)
            or command[0].startswith("-")
        ):
            raise ValueError("Schedule command must be a nonempty argument array")
        memory = re.fullmatch(r"([1-9][0-9]*)mb", schedule["memory"])
        if memory is None:
            raise ValueError("Schedule memory must be specified in whole mb")
        args = [
            "machine",
            "run",
            "--app",
            app,
            "--region",
            region,
            "--schedule",
            schedule["schedule"],
            "--restart",
            "no",
            "--vm-memory",
            memory[1],
            "--metadata",
            f"aleph_schedule={name}",
            "--detach",
        ]
        for key, value in config.get("env", {}).items():
            args.extend(["--env", f"{key}={value}"])
        # Cobra otherwise interprets python's -m as flyctl --metadata.
        args.extend(["--", image, *command])
        commands.append(args)

    result = subprocess.run(
        ["flyctl", "machine", "list", "--app", app, "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    machines = json.loads(result.stdout)
    if not isinstance(machines, list):
        raise ValueError("Expected machine list JSON array")
    ids = []
    for machine in machines:
        if "aleph_schedule" not in (
            (machine.get("config") or {}).get("metadata") or {}
        ):
            continue
        machine_id = machine["id"]
        if not isinstance(machine_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", machine_id
        ):
            raise ValueError("Invalid tagged Machine ID")
        ids.append(machine_id)
    for machine_id in ids:
        subprocess.run(
            ["flyctl", "machine", "destroy", machine_id, "--app", app, "--force"],
            check=True,
        )
    for args in commands:
        subprocess.run(["flyctl", *args], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fly-config", type=Path, default=Path("fly.toml"))
    parser.add_argument("--schedules", type=Path, default=Path("deploy/schedules.toml"))
    parser.add_argument("--deploy-log", type=Path, required=True)
    args = parser.parse_args()
    recreate(args.fly_config, args.schedules, args.deploy_log)
