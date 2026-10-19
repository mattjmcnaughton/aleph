#!/usr/bin/env bash
# "Build with warnings as errors" for the app's own Swift (iOS TDD §17.1).
#
#   clients/ios/scripts/check-warnings.sh build/xcodebuild.log
#
# Fails (exit 1) when the xcodebuild log holds a Swift compiler warning in a source
# file under clients/ios/, listing each one once. Exempt, deliberately:
# - Packages/API/Sources/API/Generated/: the committed generated client. Under Swift
#   6.2 every generated file warns "package import of ... was not used in package
#   declarations", and the file is never edited by hand.
# - Anything outside clients/ios/ or under clients/ios/build/: third-party package
#   checkouts, files Xcode generates into DerivedData, and tool noise without a
#   source location.
# A log that does not exist is an error (exit 2), never a pass.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <xcodebuild log>" >&2
  exit 2
fi
log="$1"
if [ ! -f "$log" ]; then
  echo "no build log at $log" >&2
  exit 2
fi

ios_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
generated="$ios_dir/Packages/API/Sources/API/Generated/"
build_dir="$ios_dir/build/"

warnings="$(
  awk -v root="$ios_dir/" 'index($0, root) == 1' "$log" \
    | grep -E '\.swift:[0-9]+(:[0-9]+)?: warning: ' \
    | grep -vF -e "$generated" -e "$build_dir" \
    | sort -u \
    || true
)"

if [ -n "$warnings" ]; then
  echo "Swift warnings are errors in clients/ios (Generated/ is exempt):"
  echo "$warnings"
  exit 1
fi
echo "No Swift warnings in clients/ios sources."
