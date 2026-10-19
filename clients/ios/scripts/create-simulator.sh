#!/usr/bin/env bash
# Create the pinned CI simulator and print its UDID (iOS TDD §17.1, D3).
#
#   udid="$(clients/ios/scripts/create-simulator.sh)"
#
# The device and runtime are pinned, not picked from whatever the runner image has
# newest, so an image update cannot silently change every snapshot. If the image
# lacks either, this fails and lists what it does have. Overrides (for a local run):
#   SIM_DEVICE   device type name   (default "iPhone 17")
#   SIM_RUNTIME  runtime name       (default "iOS 26.5")
#   SIM_NAME     new device's name  (default "aleph-ci")
# Diagnostics go to stderr; only the UDID goes to stdout.
set -euo pipefail

export SIM_DEVICE="${SIM_DEVICE:-iPhone 17}"
export SIM_RUNTIME="${SIM_RUNTIME:-iOS 26.5}"
sim_name="${SIM_NAME:-aleph-ci}"

ids="$(
  xcrun simctl list -j devicetypes runtimes | python3 -c '
import json, os, sys

data = json.load(sys.stdin)
want_device, want_runtime = os.environ["SIM_DEVICE"], os.environ["SIM_RUNTIME"]
phones = [t for t in data["devicetypes"] if t["name"].startswith("iPhone")]
runtimes = [r for r in data["runtimes"] if r.get("isAvailable") and r["name"].startswith("iOS")]
device = next((t for t in phones if t["name"] == want_device), None)
runtime = next((r for r in runtimes if r["name"] == want_runtime), None)
problems = []
if device is None:
    problems.append("no device type %r; this image has: %s"
                    % (want_device, ", ".join(t["name"] for t in phones) or "none"))
if runtime is None:
    problems.append("no available runtime %r; this image has: %s"
                    % (want_runtime, ", ".join(r["name"] for r in runtimes) or "none"))
if problems:
    sys.exit("\n".join(problems))
print(device["identifier"], runtime["identifier"])
'
)"
read -r device_id runtime_id <<<"$ids"

udid="$(xcrun simctl create "$sim_name" "$device_id" "$runtime_id")"
echo "created $sim_name: $SIM_DEVICE / $SIM_RUNTIME ($udid)" >&2
echo "$udid"
