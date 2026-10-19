# aleph iOS

The native SwiftUI iPhone client (iOS TDD, `docs/tdds/ios-app.md`). Its packages, app
target and CI arrive ticket by ticket (AL-603, AL-617). So far this directory holds the
generated API client.

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

CI runs `--check` and `swift test` in the iOS workflow's Linux `swift-linux` job (AL-617).
