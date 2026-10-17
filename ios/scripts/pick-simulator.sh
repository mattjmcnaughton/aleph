#!/usr/bin/env bash
# Print the UDID of an available iPhone simulator on the newest installed iOS
# runtime, preferring a plain "iPhone NN" (non Pro/Plus/SE) model.
set -euo pipefail

xcrun simctl list devices available -j | python3 -c '
import json, re, sys
data = json.load(sys.stdin)["devices"]
def ver(rt):
    m = re.search(r"iOS-(\d+)-(\d+)", rt)
    return (int(m.group(1)), int(m.group(2))) if m else None
runtimes = sorted((r for r in data if ver(r)), key=ver, reverse=True)
for rt in runtimes:
    phones = [d for d in data[rt] if d.get("isAvailable") and d["name"].startswith("iPhone")]
    if not phones:
        continue
    plain = [d for d in phones if re.fullmatch(r"iPhone \d+", d["name"])]
    pick = sorted(plain or phones, key=lambda d: d["name"])[-1]
    print(pick["udid"], rt, pick["name"], file=sys.stderr)
    print(pick["udid"])
    sys.exit(0)
sys.exit("no available iPhone simulator found")
'
