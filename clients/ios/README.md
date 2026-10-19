# aleph iOS

The native SwiftUI iPhone client (iOS TDD, `docs/tdds/ios-app.md`; PRD
`docs/prds/ios-app.md`). iOS 26, dark only, Swift 6 language mode. Screens arrive
ticket by ticket; AL-603 laid the skeleton every screen copies.

## Layout

| Path | Holds |
| ---- | ----- |
| `project.yml` | The XcodeGen spec. `xcodegen generate` writes `Aleph.xcodeproj`, which is never committed. |
| `App/` | The thin `Aleph` app target: `AlephApp`, `AppDependencies+Live.swift` (builds the live services once), `UITesting/UITestAuthSession.swift`, Info plists, `PrivacyInfo.xcprivacy`, assets. |
| `Packages/Core` | App-owned models (`Account`, `FeatureFlags`, ids), `AppError`, the service protocols (`AuthSession`, `TokenProvider`, `SessionAPI`, ...), `AppDependencies`, `TokenRefresher`. A dynamic library, so the app and the app-hosted snapshot tests share one copy of its types. Linux. |
| `Packages/CoreTestSupport` | In-memory fakes of the `Core` protocols and `ManualClock`, for every test target. Its own package because a static library holding `Core`'s target would duplicate `Core` beside the app's copy. Linux. |
| `Packages/API` | The generated client (`Generated/`), adapters to `Core` models, the bearer/refresh middleware, error mapping, `LiveSessionAPI`. Linux. |
| `Packages/Auth` | `LiveAuthSession` (AppAuth 3.0.0), `KeychainTokenStore`, the reinstall purge. iOS only. |
| `Packages/Markdown` | Module `AlephMarkdown` (swift-markdown's module is already called `Markdown`): the link allowlist now, the parser with AL-605. Linux. |
| `Packages/Design` | (Dynamic, like `Features`.) Nocturne tokens (`Palette`, `Spacing`, `Radius`) and shared components (`AlephMark`, `StateCard`, `.alephPrimary`). iOS only. |
| `Packages/Features` | Screens and their `@Observable` models, `SessionStore`, `Router`, `Route`, `AlephRootView`. iOS only. |
| `AlephSnapshotTests/` | App-hosted snapshot tests; references in `__Snapshots__/`. |
| `AlephUITests/` | XCUITest against the stub backend in fake-issuer mode; `Fixtures/tokens.json`. |

**Layering is the manifests.** `Features` depends on `Core` and `Design` only, so it
cannot import `API`, `Auth`, AppAuth or OpenAPIRuntime, and never sees a generated type.
Screens take protocol-typed dependencies through their initialiser (TDD §3.4, §6.1).
`App`, `Features` and `Design` default to the main actor; `Core`, `API`, `Auth` and
`Markdown` are nonisolated. Pure state enums are marked `nonisolated` so their
`Equatable`/`Codable` conformances need no actor.

## Build configurations

| Configuration | API | Sign-in | Notes |
| ------------- | --- | ------- | ----- |
| Debug | `http://127.0.0.1:8000` (`just dev-be`) | dev Keycloak (`127.0.0.1:18080`, the issuer the backend expects), client `aleph-ios` | `NSAllowsLocalNetworking` |
| UITest | `http://127.0.0.1:8000` (the e2e stub backend) | the fake issuer | Debug plus `ALEPH_UI_TESTING` |
| Release | production | Auth0 Native app (values arrive with AL-621) | |

The values are build settings in `project.yml`, read at runtime from Info.plist keys
(`AlephAPIBaseURL`, `AlephOIDCIssuer`, `AlephOIDCClientID`, `AlephOIDCAudience`).

**UI-test auth (TDD §5.5, D22).** `UITestAuthSession` lives in the app target, not in
`Auth`, because only a target the `UITest` configuration builds directly can see the
`ALEPH_UI_TESTING` condition; a package cannot. It reads `-AlephTestTokens <json array>`
at launch. `ios-gate` builds Release and fails if the binary contains `AlephTestTokens`.
The tokens are signed by the e2e fake issuer's committed test-only key; see
`docs/ci.md` ("Fake-issuer mode") and `scripts/mint_e2e_tokens.py`.

**Running it on a Mac.** `cd clients/ios && xcodegen generate && open Aleph.xcodeproj`,
with `just dev-be` and dev Keycloak (`just compose-keycloak-up`) running for the Debug
configuration.

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
| `swift-linux` | `swift:6.2` container | `swift format lint --strict` (all tracked Swift here except `Generated/`, with `.swift-format`), `swift test` in `Tools/GenerateAPI`, `scripts/generate-api.sh --check`, `swift test` in `Packages/Core`, `Packages/CoreTestSupport`, `Packages/Markdown`, `Packages/API`. |
| `ios-gate` | `macos-26`, Xcode 26.6 | XcodeGen, the pinned *iPhone 17 / iOS 26.5* simulator, build (warnings are errors), unit and snapshot tests, the Release-binary `AlephTestTokens` check. |
| `ios-e2e` | `macos-26`, Xcode 26.6 | Postgres 16, the stub backend in fake-issuer mode, `AlephUITests` in the `UITest` configuration. |

A change outside `clients/ios/`, `clients/openapi.json`, the e2e backend and its fake
issuer (`scripts/e2e_backend.py`, `scripts/e2e_fake_issuer.py`,
`tests/fixtures/e2e_oidc/`) and the workflow file skips the last three, which counts as
passing.

Run the lint locally (Swift 6.2):

```sh
git ls-files -z -- 'clients/ios/*.swift' ':(exclude)clients/ios/Packages/API/Sources/API/Generated/**' \
  | xargs -0 swift format lint --strict
```

### What the macOS jobs expect from the Xcode project

The jobs assume:

- `project.yml` generates `Aleph.xcodeproj` with two schemes. `Aleph`'s test action
  (Debug) holds the iOS-only packages' unit tests (`AuthTests`, `FeaturesTests`),
  `AlephSnapshotTests` and `AlephUITests`; `ios-gate` runs it with
  `-skip-testing:AlephUITests`. `AlephUITests` holds only the UI tests, and `ios-e2e`
  runs it with `-configuration UITest -only-testing:AlephUITests`: Xcode builds local
  packages in a custom configuration optimised and without testability, so the
  `@testable` package and snapshot tests cannot build in `UITest`.
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
