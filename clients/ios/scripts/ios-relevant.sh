#!/usr/bin/env bash
# The iOS workflow's path filter (iOS TDD §17.1). Reads changed paths, one per line,
# on stdin and prints `true` when any of them can affect the iOS client, else `false`.
# Home-written on purpose: no third-party filter action (PRD §9.4).
#
#   git diff --name-only "$base" HEAD | clients/ios/scripts/ios-relevant.sh
set -euo pipefail

# Keep in step with docs/ci.md and clients/ios/README.md.
patterns=(
  '^clients/ios/'                # the app, its packages, tools and these scripts
  '^clients/openapi\.json$'      # the contract the Swift client is generated from
  '^scripts/e2e_backend\.py$'    # the stub backend the UI tests run against
  '^\.github/workflows/ios\.yml$'
)

args=()
for p in "${patterns[@]}"; do
  args+=(-e "$p")
done

# grep exits 0 on a match and 1 on none; anything else is an error, which must fail
# the job rather than read as "nothing changed" and skip the iOS checks.
status=0
grep -Eq "${args[@]}" || status=$?
case "$status" in
  0) echo true ;;
  1) echo false ;;
  *) exit "$status" ;;
esac
