#!/usr/bin/env bash
# Generate the iOS app's Swift API client from clients/openapi.json (iOS TDD §4.2).
#
#   clients/ios/scripts/generate-api.sh          write Packages/API/Sources/API/Generated/
#   clients/ios/scripts/generate-api.sh --check  regenerate to a temp dir; fail on any drift
#
# Two steps, both from Tools/GenerateAPI:
# 1. prepare-contract rewrites a temp copy of the contract into the nullability form
#    swift-openapi-generator 1.x supports (clients/openapi.json is never changed).
# 2. The swift-openapi-generator CLI, pinned exactly, generates from that copy with
#    Packages/API/openapi-generator-config.yaml.
# Either mode fails if the generator reports a schema as "not supported", because the
# generator then leaves that schema or property out of the Swift client silently.
# Needs a Swift 6.2 toolchain (CI uses the swift:6.2 container). The output is
# committed; never edit it by hand.
set -euo pipefail

ios_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
contract="$ios_dir/../openapi.json"
config="$ios_dir/Packages/API/openapi-generator-config.yaml"
generated="$ios_dir/Packages/API/Sources/API/Generated"
tool_pkg="$ios_dir/Tools/GenerateAPI"

check=false
case "${1:-}" in
  "") ;;
  --check) check=true ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

generate() {
  local out="$1"
  swift run --package-path "$tool_pkg" prepare-contract "$contract" "$work/openapi.json"
  if ! swift run --package-path "$tool_pkg" swift-openapi-generator generate \
      "$work/openapi.json" --config "$config" --output-directory "$out" \
      > "$work/generator.log" 2>&1; then
    cat "$work/generator.log" >&2
    exit 1
  fi
  if grep -q "not supported" "$work/generator.log"; then
    grep "not supported" "$work/generator.log" >&2
    echo "swift-openapi-generator skipped an unsupported schema (above); the Swift client would silently miss it" >&2
    exit 1
  fi
}

if [ "$check" = false ]; then
  rm -rf "$generated"
  mkdir -p "$generated"
  generate "$generated"
  echo "Generated $generated"
  exit 0
fi

mkdir "$work/generated"
generate "$work/generated"
if ! diff -ru "$generated" "$work/generated"; then
  echo "clients/ios/Packages/API/Sources/API/Generated/ is stale: run clients/ios/scripts/generate-api.sh and commit the result" >&2
  exit 1
fi
echo "Generated Swift client is up to date."
