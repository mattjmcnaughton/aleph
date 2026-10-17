#!/usr/bin/env bash
# Print the UDID of an iPhone simulator on the newest installed iOS runtime that
# is at least MIN_IOS (default: the deployment target in project.yml, 26.0).
# Prefers a plain "iPhone NN" model (not Pro/Plus/Air/SE); if the runtime has no
# iPhone device yet, creates one from the newest iPhone device type.
# Diagnostics go to stderr; only the UDID goes to stdout.
set -euo pipefail

MIN_IOS="${MIN_IOS:-26.0}"

xcrun simctl list -j | MIN_IOS="$MIN_IOS" python3 -c '
import json, os, re, subprocess, sys

data = json.load(sys.stdin)
want = tuple(int(x) for x in os.environ["MIN_IOS"].split(".")[:2])

def ver(s):
    m = re.search(r"(\d+)[-.](\d+)", s)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

runtimes = sorted(
    (r for r in data["runtimes"]
     if r.get("isAvailable") and r.get("platform", "iOS") == "iOS"
     and r["identifier"].startswith("com.apple.CoreSimulator.SimRuntime.iOS-")
     and ver(r["version"]) >= want),
    key=lambda r: ver(r["version"]), reverse=True)
if not runtimes:
    seen = ", ".join(r["identifier"] for r in data["runtimes"]) or "none"
    sys.exit("no available iOS runtime >= %s (installed: %s)" % (os.environ["MIN_IOS"], seen))

def model(d):
    return tuple(int(n) for n in re.findall(r"\d+", d["name"])) or (0,)

def plain_first(phones):
    plain = [d for d in phones if re.fullmatch(r"iPhone \d+", d["name"])]
    return sorted(plain or phones, key=model)[-1]

for rt in runtimes:
    devices = data["devices"].get(rt["identifier"], [])
    phones = [d for d in devices if d.get("isAvailable") and d["name"].startswith("iPhone")]
    if phones:
        pick = plain_first(phones)
        print(pick["udid"], rt["identifier"], pick["name"], file=sys.stderr)
        print(pick["udid"])
        sys.exit(0)

rt = runtimes[0]
supported = {t["identifier"] for t in rt.get("supportedDeviceTypes", [])}
types = [t for t in data["devicetypes"]
         if t["name"].startswith("iPhone") and (not supported or t["identifier"] in supported)]
if not types:
    sys.exit("runtime %s has no iPhone device types" % rt["identifier"])
dt = plain_first(types)
udid = subprocess.check_output(
    ["xcrun", "simctl", "create", dt["name"] + " (ci)", dt["identifier"], rt["identifier"]],
    text=True).strip()
print(udid, rt["identifier"], dt["name"], "(created)", file=sys.stderr)
print(udid)
'
