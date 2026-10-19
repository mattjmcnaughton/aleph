# aleph iOS

The native SwiftUI iPhone client (iOS TDD, `docs/tdds/ios-app.md`). Its packages and app
target arrive ticket by ticket (AL-603 onward). So far this directory holds the
generated API client, the CI helper scripts and `.swift-format`.

## The generated API client

`Packages/API/Sources/API/Generated/` is generated from the contract,
`clients/openapi.json`, by [swift-openapi-generator](https://github.com/apple/swift-openapi-generator)
and committed. Never edit it by hand.

| File | Role |
| ---- | ---- |
| `scripts/generate-api.sh` | Regenerates `Generated/`. With `--check`, regenerates to a temp directory and fails on any difference. Both modes fail if the generator reports any schema as "not supported". |
| `Tools/GenerateAPI/Package.swift` | Pins the generator CLI **exactly** (1.14.2); `Package.resolved` pins its dependencies. Also holds `prepare-contract` (below). |
| `Packages/API/openapi-generator-config.yaml` | `generate: [types, client]`, `accessModifier: package`, idiomatic naming, and a `filter` that keeps the learner tags only. Admin routes (`/api/v1/admin/`, tag `feature-flags`) stay web-only. `tests/unit/test_swift_generator_config.py` checks the tag list against the contract. |
| `Packages/API/Package.swift` | The `API` library: the generated client over swift-openapi-runtime 1.13.2 and swift-openapi-urlsession 1.3.2. |

### The nullability rewrite (`prepare-contract`)

FastAPI writes a nullable field as `anyOf: [X, {"type": "null"}]` (OpenAPI 3.1).
swift-openapi-generator 1.14.2 does not support the `null` schema: it warns
`not supported, skipping` and leaves the whole property out of the Swift type. Without a
fix, 40 fields would be missing, among them `next_lesson`, `read_passage` and
`quick_check`.

So `generate-api.sh` first runs `prepare-contract` (`Tools/GenerateAPI/Sources/ContractRewrite`)
on a temp copy of the contract. `clients/openapi.json` and the web client are unchanged.
The rewrite is narrow:

- `anyOf: [X, {"type": "null"}]` on a property, in either order, becomes `X`, and the
  property leaves `required`. Swift sees an Optional, and a JSON `null` decodes as `nil`.
- A property that is only `{"type": "null"}` (always null) is dropped.
- Any other `null` in the document fails the rewrite. Extend it deliberately.

One consequence for request bodies: Swift leaves a `nil` Optional out of the JSON
instead of sending `null`. A field that the server requires but accepts as `null`
(today only `UpdateTeachingStyleRequest.teaching_style`) cannot be sent as an explicit
`null` through the generated client.

`swift test --package-path clients/ios/Tools/GenerateAPI` runs the rewrite's tests.

After any `/api/v1` change, regenerate the contract and both clients in the same PR:

```sh
just openapi && just openapi-ts
clients/ios/scripts/generate-api.sh
```

The script needs a Swift 6.2 toolchain: Xcode 26 on macOS, or Swift 6.2 on Linux (for
example the `swift:6.2` container image). Run it from any directory. The first run
builds the generator (about a minute).

To check for drift, and to build and test the package:

```sh
clients/ios/scripts/generate-api.sh --check
(cd clients/ios/Packages/API && swift test)
```

CI runs `--check` and `swift test` in the iOS workflow's Linux `swift-linux` job (below).

## CI

`.github/workflows/ios.yml` runs four jobs; [`docs/ci.md`](../../docs/ci.md#the-ios-workflow-iosyml)
has the full description and the required-check setup.

| Job | Runner | Does |
| --- | ------ | ---- |
| `changes` | ubuntu | The path filter: `scripts/ios-relevant.sh` over the diff. |
| `swift-linux` | `swift:6.2` container | `swift format lint --strict` (all tracked Swift here except `Generated/`, with `.swift-format`), `swift test` in `Tools/GenerateAPI`, `scripts/generate-api.sh --check`, `swift test` in `Packages/Core`, `Packages/Markdown`, `Packages/API`. |
| `ios-gate` | `macos-26`, Xcode 26.6 | XcodeGen, the pinned *iPhone 17 / iOS 26.5* simulator, build (warnings are errors), unit and snapshot tests, the Release-binary `AlephTestTokens` check. |
| `ios-e2e` | `macos-26`, Xcode 26.6 | Postgres 16, the stub backend in fake-issuer mode, `AlephUITests` in the `UITest` configuration. |

A change outside `clients/ios/`, `clients/openapi.json`, `scripts/e2e_backend.py` and
the workflow file skips the last three, which counts as passing.

Run the lint locally (Swift 6.2):

```sh
git ls-files -z -- 'clients/ios/*.swift' ':(exclude)clients/ios/Packages/API/Sources/API/Generated/**' \
  | xargs -0 swift format lint --strict
```

### What the macOS jobs expect from the Xcode project (AL-603)

Until `project.yml` exists, both macOS jobs stop after the toolchain and simulator
checks with a "No Xcode project yet" notice. Once it does, they assume:

- `project.yml` generates `Aleph.xcodeproj` with an `Aleph` scheme. Its test action
  holds the iOS-only packages' unit tests and `AlephSnapshotTests`; `ios-gate` runs it
  with `-skip-testing:AlephUITests`. `ios-e2e` runs `-only-testing:AlephUITests` with
  `-configuration UITest`.
- A Release build produces one `.app` whose executable doesn't contain the string
  `AlephTestTokens` (TDD §5.5).
- The snapshot helpers read `SNAPSHOT_RECORD` and `SNAPSHOT_ARTIFACTS`, and the UI
  tests read `SCREENSHOT_DIR`. CI sets each one with a `TEST_RUNNER_` prefix, which
  `xcodebuild` strips. References live in `AlephSnapshotTests/__Snapshots__/`, and UI
  test screenshots in `screenshots/`.
- The e2e backend listens on `127.0.0.1:8000`, with `E2E_FAKE_ISSUER=1` and
  `E2E_SIMCTL_UDID` set.
- Warnings: `scripts/check-warnings.sh` fails the build on any Swift warning in a
  source under `clients/ios/`, except `Packages/API/Sources/API/Generated/` (which
  warns about unused `package import`s under Swift 6.2).

### Iterating without Xcode: `ios-dev/*` branches

Push to `ios-dev/<topic>`. There the workflow records: snapshot references and UI-test
screenshots are regenerated and committed back to the branch as `chore(ios): record
snapshot references and screenshots [skip ci]`. Pull, read the PNGs, repeat, then
bring the commits onto your PR branch, where CI only compares.
