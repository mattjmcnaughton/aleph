# aleph iOS (proof of concept)

A trivial SwiftUI app (iOS 17+) whose only screen is a stub **Your paths** home:
the app title "aleph", three hard-coded sample paths (title, level, progress), and a
**New path** button that does nothing. No networking, no auth.

Its real purpose is to prove a loop: an agent writes Swift on Linux, a GitHub Actions
macOS runner builds and tests it on a simulator, and screenshots come back where an
agent can look at them.

## Layout

```
ios/
  project.yml              # XcodeGen spec (the .xcodeproj is generated, never committed)
  Aleph/                   # App sources: AlephApp, HomeView, SamplePath
  AlephSnapshotTests/      # swift-snapshot-testing: home screen, light + dark, iPhone 13 size
  AlephUITests/            # XCUITest: launches the app, captures XCUIScreen screenshots
  scripts/pick-simulator.sh  # Picks an available iPhone simulator UDID on the newest iOS runtime
  screenshots/             # PNGs committed back by CI
```

## Run locally (macOS + Xcode 16+)

```sh
brew install xcodegen
cd ios
xcodegen generate
open Aleph.xcodeproj        # or build and test from the command line:
xcodebuild test -project Aleph.xcodeproj -scheme Aleph \
  -destination "id=$(./scripts/pick-simulator.sh)" CODE_SIGNING_ALLOWED=NO
```

Run `xcodegen generate` again after adding or removing source files.

Test knobs (set with a `TEST_RUNNER_` prefix on the `xcodebuild` command line, which
strips the prefix before the test process sees it):

| Variable | Effect |
| -------- | ------ |
| `SNAPSHOT_DIR` | Where snapshot PNGs are read/written (default: `__Snapshots__/` beside the test). |
| `SNAPSHOT_RECORD=1` | Record snapshots instead of comparing; recording never fails the test. |
| `SCREENSHOT_DIR` | Also write the XCUITest screenshots there as PNGs. |

The app honours `ALEPH_COLOR_SCHEME=light|dark` in its environment, which is how the
UI test pins the appearance.

## CI

`.github/workflows/ios.yml` runs on `macos-15` for pushes to
`claude/ios-homescreen-poc`, pushes and PRs to `main` that touch `ios/**` or the
workflow, and `workflow_dispatch`. It selects the latest stable Xcode, installs
XcodeGen, generates the project, picks a simulator, and runs both test targets with
recording on (there are no committed references yet, so this run captures rather
than compares).

## Where screenshots land

- **Artifact** `ios-screenshots` on every run: the PNGs, the raw `.xcresult`
  attachments (`xcrun xcresulttool export attachments`), and the full `xcodebuild`
  log.
- **Commit** on pushes to `claude/ios-homescreen-poc` only: CI commits
  `ios/screenshots/*.png` back to the branch as
  `chore(ios): update simulator screenshots [skip ci]`. Run `git pull` before pushing
  again.

Files:

- `HomeView.home-light.png`, `HomeView.home-dark.png`: snapshot tests (390x844 @3x).
- `uitest-home-light.png`, `uitest-home-dark.png`: full-simulator screenshots from the UI test.
