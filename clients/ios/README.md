# aleph iOS (proof of concept)

A trivial SwiftUI app (iOS 26+, dark mode only, matching the dark-only web app) whose
only screen is a stub **Your paths** home: the app title "aleph", three hard-coded
sample paths (title, Level, progress), and a **New path** button that does nothing.
No networking, no auth.

Its real purpose is to prove a loop: an agent writes Swift on Linux, a GitHub Actions
macOS runner builds and tests it on a simulator, and images come back where an agent
can look at them.

## Layout

```
clients/ios/
  project.yml                 # XcodeGen spec (the .xcodeproj is generated, never committed)
  Aleph/                      # App sources: AlephApp (pins .dark), HomeView, SamplePath
  AlephSnapshotTests/
    HomeViewSnapshotTests.swift  # swift-snapshot-testing: dark, default + AX3 Dynamic Type
    __Snapshots__/            # Committed reference PNGs (the compare-mode baseline)
  AlephUITests/               # XCUITest: launches the app, captures an XCUIScreen screenshot
  scripts/pick-simulator.sh   # iPhone simulator UDID on the newest iOS >= 26.0 runtime
  screenshots/                # UI-test screenshot committed back by CI (review aid, never compared)
```

## Run locally (macOS + Xcode 26+)

```sh
brew install xcodegen
cd clients/ios
xcodegen generate
open Aleph.xcodeproj        # or build and test from the command line:
xcodebuild test -project Aleph.xcodeproj -scheme Aleph \
  -destination "id=$(./scripts/pick-simulator.sh)" CODE_SIGNING_ALLOWED=NO
```

Run `xcodegen generate` again after adding or removing source files. Record new
references locally with `TEST_RUNNER_SNAPSHOT_RECORD=1 xcodebuild test ...`, but prefer
recording in CI (below) so references come from the same runner image that compares
them.

Test knobs (set with a `TEST_RUNNER_` prefix on the `xcodebuild` command line, which
strips the prefix before the test process sees it):

| Variable | Effect |
| -------- | ------ |
| `SNAPSHOT_RECORD=1` | Record references instead of comparing; recording never fails the test. |
| `SNAPSHOT_DIR` | Where references are read/written (default: `AlephSnapshotTests/__Snapshots__/`). |
| `SNAPSHOT_ARTIFACTS` | Where a failing comparison writes its image (default: the temp dir). |
| `SCREENSHOT_DIR` | Also write the XCUITest screenshot there as a PNG. |
| `MIN_IOS` | (`pick-simulator.sh`) minimum runtime, default `26.0`. |

## Iterating from an agent: `ios-dev/*` branches

Agents cannot build iOS on Linux, so they push to an `ios-dev/<topic>` branch and
let CI build. `.github/workflows/ios.yml` runs in **record** mode there: it regenerates
the snapshot references and the UI-test screenshot and commits them back to the same
branch as `chore(ios): record snapshot references and screenshots [skip ci]`.

1. Commit Swift changes and `git push origin HEAD:ios-dev/<topic>`.
2. Watch the `iOS` run (`ios-gate` job). Read the build log from the job, or download
   the `ios-gate-record` artifact (PNGs, `.xcresult` attachments, `xcodebuild.log`).
3. `git pull` to get the bot commit, then look at the PNGs in
   `AlephSnapshotTests/__Snapshots__/` and `screenshots/`.
4. Repeat. Bring the bot commits onto your PR branch (merge or fast-forward; the
   references must be in the PR, or compare mode has no baseline).

Files:

- `AlephSnapshotTests/__Snapshots__/HomeView.home-dark.png`: default text, iPhone 13
  size (390x844 @3x).
- `AlephSnapshotTests/__Snapshots__/HomeView.home-dark-ax3.png`: accessibility Dynamic
  Type (AX3, `.accessibilityExtraLarge`) on a double-height 390x1688 canvas so the
  whole screen is visible.
- `screenshots/uitest-home-dark.png`: the full simulator screen from the UI test.

## Pull requests: compare mode

On `pull_request` to `main` (and on `workflow_dispatch`) the snapshot tests compare
against the committed references and **fail on a mismatch or a missing reference**
(tolerance: 99.5% of pixels at 98% perceptual precision, to absorb anti-aliasing
noise). The failing images and the reference/failure/difference attachments are
uploaded as the `ios-gate-compare` artifact. Fix the view, or re-record on an
`ios-dev/*` branch and bring the new references into the PR.

The UI-test screenshot is never compared: it contains the status bar. CI still
overrides the status bar (`xcrun simctl status_bar ... --time 9:41`) so the committed
screenshot doesn't churn on every record run.

## Path filter

The workflow has no trigger-level `paths:` filter (a workflow that never starts leaves
a required check pending). Its first job, `changes` (ubuntu), diffs
`git diff --name-only "$base" HEAD`, where `base` is the PR base SHA, or the push's
`before` SHA (a new branch or a missing commit runs everything; so does
`workflow_dispatch` unless you pass a `base` input). `ios-gate` runs only when a changed
path matches:

- `^clients/ios/`
- `^\.github/workflows/ios\.yml$`
- `^scripts/e2e_backend\.py$`
- `^docs/openapi\.json$` (placeholder; the committed OpenAPI document doesn't exist yet)

Otherwise `ios-gate` is skipped, which counts as passing for a required check. Keep
the job names (`changes`, `ios-gate`) stable for branch protection.
