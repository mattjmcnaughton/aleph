#!/usr/bin/env bash
# Commit recorded PNGs back to the ios-dev branch CI is building (iOS TDD §17.3).
#
#   clients/ios/scripts/commit-back.sh <branch> <path>...
#
# Used only in record mode (a push to ios-dev/**). Commits the given paths as the
# GitHub Actions bot with "[skip ci]" and pushes, rebasing over a concurrent
# commit-back from the other macOS job. Does nothing when the paths are unchanged.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <branch> <path>..." >&2
  exit 2
fi
branch="$1"
shift

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A -- "$@"
if git diff --cached --quiet; then
  echo "Recorded files unchanged; nothing to commit."
  exit 0
fi
git commit -m "chore(ios): record snapshot references and screenshots [skip ci]"
for i in 1 2 3; do
  if git pull --rebase origin "$branch" && git push origin "HEAD:$branch"; then
    exit 0
  fi
  sleep $((i * 5))
done
exit 1
