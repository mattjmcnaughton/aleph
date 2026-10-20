# PRD — The Home Screen app (aleph on the phone, without a native client)

**Status:** Accepted for provisional feature-branch implementation; T0 and release acceptance outstanding · **Owner:** solo builder · **Roadmap item:** Home Screen app (§2.3)
**References:** [`CONSTITUTION.md`](../../CONSTITUTION.md) · [`CLAUDE.md` — One Client](../../CLAUDE.md#one-client-web-and-home-screen-app) · [`CONTEXT.md`](../CONTEXT.md) · [`api.md`](../api.md) · [`deploy.md`](../deploy.md) · [`metrics.md`](../metrics.md) · [iOS app PRD](ios-app.md) · [iOS app TDD](../tdds/ios-app.md) · [ADR 0001 — store the last-seen time zone](../adrs/0001-store-last-seen-time-zone.md) · [Phase 5 streaks PRD](phase-5-streaks.md) · [Phase 6 PRD](phase-6-analyst.md)
**Companion:** [Home Screen app TDD](../tdds/home-screen-app.md), which owns the mechanics and the tickets.

## Execution amendment (2026-10-20)

**This amendment takes precedence over the historical draft below**, including its
proposed/if-accepted wording, spike-first phasing and release language.
The owner [approved in epic #257](https://github.com/mattjmcnaughton/aleph/issues/257#issuecomment-6280580393)
P1–P6 and the constitution §8 amendment (already recorded on 2026-10-19).
P1/P2 adopt one web client and **conditionally park** native; P3 accepts **90 days
since last visit**, renewed on responses, with signed cookies and **no server-side
revocation** (logout clears the client cookie). P4 retains the three Notification
kinds/permission moments without provisional permission; P5 approves the named
Apple/Google/Mozilla/Microsoft endpoint allowlist; P6 approves `web`, `home_screen`,
reserved `ios`, and `unknown` event values. These are not open owner proposals.

The owner explicitly deferred **T0 to the END**, accepting provisional/rework risk.
T0 is not passed; native remains the fallback if feasibility fails. Findings belong
in TDD §13 after the real-iPhone check, not in an invented acceptance record.
Delivery is ticket PRs into **`feature/home-screen-app` only**, never `main` in this
run. No deployment, Auth0 changes, production secrets/data/infrastructure changes,
paid evals or remote branch deletion are authorized. Human gates are deferred, not
waived: real-iPhone sign-in, force-quit/reopen, delivery/tap-through for all three
kinds, week-long use, session return and operational job acceptance remain unverified.
See the [TDD execution amendment](../tdds/home-screen-app.md#execution-amendment-2026-10-20)
for the issue mapping and actual implementation dependencies.

**Unresolved, before Notifications UI prep:** §5.4 says install instructions replace
the toggles in an iPhone Safari tab; TDD §8.8 permits account-wide preferences to be
edited when the current Device is off. Must an uninstalled iPhone Safari tab show
only installation instructions, or also editable account-wide preferences (with
Device enablement unavailable)? Does the latter rule cover `needs_home_screen`,
not just `off`? No owner choice is recorded; do not silently select either design.
The Reminder toggle/hour and its pre-prompt belong to T9, not T7b.

> **This PRD proposes a cheaper answer to the problem the iOS app PRD set out to solve.** That
> PRD's diagnosis stands (§2.1 there): a phone browser tab is a weak place to return to. Its
> answer was a second, native client. This PRD's answer is to make the one client we have
> installable on the iPhone Home Screen and give it Web Push. Most of the iOS PRD's backend
> work (the Learner time zone, the three Notification kinds, the hourly scheduled job) carries
> over; the SwiftUI app, its CI, TestFlight and the two-client parity rule do not.
>
> **If accepted, the iOS app PRD and TDD are parked, not deleted.** They stay the plan for a
> native app if the measurements in §7 show the Home Screen app is not enough (§11, P2).

## 1. Summary

aleph is mobile-first, but today a learner reaches it through a browser tab. This PRD makes the
existing React app a **Home Screen app**: a learner adds aleph to their iPhone Home Screen, it
opens full-screen with its own icon, and it can send **Notifications** through Web Push. Nothing
about paths, lessons, the tutor, shaping, streaks, Beats, Briefs or Flow changes, because the
phone runs the same app the browser does.

What is new, all of it small:

- **Installable.** A web app manifest, Home Screen icons, and an **install card** that tells an
  iPhone learner how to add aleph (iOS has no install prompt).
- **Stays signed in.** The session cookie lasts long enough that a learner returning after a
  quiet month does not hit the sign-in screen (§5.3).
- **Notifications, through Web Push.** The same three kinds the iOS PRD settled (D6 there):
  **Path ready**, the opt-in daily **Reminder**, and **Brief published**. On iPhone they work only
  from the Home Screen app; on Android and desktop browsers they also work in a tab.
- **The backend half of the iOS plan, unchanged in substance:** the stored **Learner time zone**
  (ADR 0001), the hourly scheduled job (iOS PRD §5.14), and Brief generation moved into that job.
  The push sender speaks Web Push instead of APNs.

**The core claim:** this delivers the iOS PRD's return levers (an icon, a session that lasts,
Notifications) for roughly a third of the tickets, with no Mac, no Xcode, no Apple developer
account and no second client to keep in step.

## 2. Context & goals

### 2.1 Why this instead of the native app

The iOS PRD lists what makes a tab a weak door (iOS PRD §2.1). How the Home Screen app answers
each:

| The tab's problem | Home Screen app |
| --- | --- |
| No Home Screen icon | Solved. Since iOS 26, every site added to the Home Screen opens as a web app by default. Adding still takes Share → Add to Home Screen; the install card teaches it. |
| Sign-in expires with the cookie | Solved on the server: a longer session (§5.3). |
| No native navigation | Mostly solved. Full-screen with no browser bar; every signed-in surface already has breadcrumbs, so no screen depends on a browser back button. It will not feel like SwiftUI. |
| No notifications | Solved on iOS 16.4+ for Home Screen apps, through Web Push. Less reliable than APNs (§11, R2). |
| Nothing to tap at 7am | Solved: the icon, and the Reminder. |

What it saves, against the iOS TDD's 17 tickets: the SwiftUI app (tickets 4–10, 12), the Swift
contract tooling and macOS CI (most of 3), the `clients/` move (1), and TestFlight and code
signing (17). It also removes the standing cost of the *Two Clients* rule: every user-facing
change written, tested and reviewed twice.

What it gives up is in §4 (Out of scope) and §11 (Risks). Nothing given up is in the iOS PRD's v1
scope except provisional (quiet) notification permission and APNs-grade delivery.

### 2.2 Goals

- The owner can add aleph to their iPhone Home Screen and do a day's learning there, with no
  browser chrome and no reason to open Safari.
- A learner who returns after weeks away lands on Your paths, not the sign-in screen.
- The three Notification kinds work end to end from the Home Screen app on iOS 26, opt-in where
  the iOS PRD says so, and open the right screen when tapped.
- **One client.** No rule is written twice, and no feature waits on a second implementation.
- The Home Screen app is **at least as safe** as the tab: the same Markdown handling, the same
  answer-hiding, and no new way for the server to be pointed at an arbitrary URL (§5.4, push
  endpoints).

**Non-goals**

- **No native app in this PRD.** The iOS app PRD and TDD are parked (§2.3).
- **No offline mode.** No service-worker caching of pages, data or the app shell. The service
  worker exists for Notifications only. A dropped connection stays an error state with a retry,
  as today.
- **No App Store or TestFlight presence.**
- **No widgets, Live Activities, Siri, haptics or badges.**
- **No change to any existing `/api/v1` payload.** New routes only, as in the iOS PRD.

### 2.3 What accepting this changes about the plan

- **[iOS app PRD](ios-app.md) and [TDD](../tdds/ios-app.md)** — marked **Parked** at the top,
  with a pointer here. Their backend decisions that this PRD adopts (time zone, Notification
  kinds, the job) are restated in the Home Screen app TDD so an implementer never has to read a
  parked document to build live work.
- **[`CONSTITUTION.md`](../../CONSTITUTION.md)** — §8 assumed two clients and named push as
  iOS-only. It is amended with this PRD (amendment record, 2026-10-19): Notifications reach a
  learner on every client able to receive them, and the parity rule applies whenever more than
  one learner client exists. The TDD's §1A checks the design against every principle.
- **[`CLAUDE.md`](../../CLAUDE.md)** — the push exception in *Two Clients* is reconciled with
  §8 alongside this PRD. Once the spike passes, the section becomes a short note: there is one
  client; the native app is parked; its rules return if it does. The `(ios)` commit-scope
  guidance goes with it (the `.releaserc.json` rule never landed).
- **[`CONTEXT.md`](../CONTEXT.md)** — one new term, **Home Screen app**. **Notification** and
  **Device** are reworded for Web Push (a Notification can reach a phone or a browser; a Device
  is one browser or Home Screen app that allowed Notifications). **Learner time zone** stops
  naming "web or iOS".
- **[`roadmap.md`](../roadmap.md)** — the *iOS app* item is replaced by *Home Screen app*, with
  §10's slices as its status line.
- **[`api.md`](../api.md)** — new sections for Devices, notification preferences and the push
  key route; the time zone header in the conventions section; the analyst section amended for
  the job replacing the arrival drain (as the iOS PRD already required).
- **[`metrics.md`](../metrics.md)** — the `client` dimension, with Home Screen app values (§7).
- **[`deploy.md`](../deploy.md)** — VAPID secrets, `PUBLIC_BASE_URL`, scheduled machines.
- **[Phase 5 streaks PRD](phase-5-streaks.md)** and **[Phase 6 PRD](phase-6-analyst.md)** — the
  same supersessions the iOS PRD made (opt-in Reminders; the job replaces the arrival drain),
  now pointing here.

## 3. Target user & user stories

The learner is the owner first, then a few friends, all on iPhone; some will also use a desktop
browser.

- *As a learner on an iPhone,* I want aleph on my Home Screen so I can open it in one tap, full
  screen, like my other apps.
- *As a learner who has never installed a web app,* I want aleph to tell me how, once, and then
  leave me alone.
- *As a learner who comes back after three weeks,* I want to land on my paths, not sign in again.
- *As a learner who started a path and walked away,* I want to know when it is ready.
- *As a learner building a habit,* I want one reminder at an hour I choose, and none on a day I
  have already studied.
- *As a learner with a Beat,* I want to know when a new Brief is published, even if I have not
  opened aleph that week.
- *As a learner on a desktop browser,* I want the same Notifications there if I turn them on.

## 4. Scope

**In scope**

- Web app manifest, icons, Home Screen metadata, and the install card (§5.1, §5.2).
- A longer, sliding session (§5.3).
- Notifications: the three kinds, permission flow, Notifications settings, Devices, the Web
  Push sender (§5.4).
- The Learner time zone header from the web app (§5.6).
- The hourly scheduled job, Reminders, and Briefs published from the job (§5.5).
- The `client` dimension on product events (§7).
- Two small items carried over from the iOS TDD because they were never iOS work: Flow on by
  default with its W32 → W34 renumber (iOS TDD ticket 3), and **Delete Beat** on the web (iOS TDD
  ticket 11, web half).

**Out of scope**

- Everything in the iOS PRD's Slices 1–7 that exists only to rebuild the web app natively.
- Offline reading, background sync, periodic background work in the service worker.
- Provisional (quiet) notification permission. iOS offers it only to native apps.
- Badges on the Home Screen icon. Possible later through the Badging API; not needed for v1.
- Sign in with Apple, account deletion, and anything else that only App Store submission needs.
- An Android-specific install prompt (`beforeinstallprompt`). Android Chrome's own install UI is
  enough; the install card targets iOS Safari only.

## 5. Functional requirements

### 5.1 Installable

- aleph ships a **web app manifest**: name *aleph*, `display: standalone`, start and scope at the
  site root, Nocturne's night color as theme and background, and Home Screen icons (including a
  maskable icon and an `apple-touch-icon`).
- Opened from the Home Screen, aleph runs **full-screen** with no browser bar. The status bar
  area is drawn in Nocturne's night color, and content clears the notch and the home indicator
  (the safe-area handling already in `index.html` and the sheets).
- **Every screen can be left without a browser back button.** Signed-in surfaces already carry
  breadcrumbs to their ancestors; the TDD audits the rest (sign-in error, Flow screens, the
  install card's own dismissal) and adds a way back where one is missing.
- Deep links that open the Home Screen app (from a Notification) land on the named screen, not
  on Your paths.

### 5.2 The install card

- Shown **only on iPhone Safari in a tab**, never inside the Home Screen app, never on other
  browsers. It sits at the top of **Your paths**.
- Copy: **"Add aleph to your Home Screen"** with the one instruction iOS needs: tap **Share**,
  then **Add to Home Screen**. A second line says why: "Reminders and other Notifications only
  work from there."
- Dismissible with **Not now**; dismissed means hidden for 30 days on that browser. Never shown
  more than once per visit.
- The same instruction appears in **Notifications settings** in place of the toggles when the
  learner is in iPhone Safari outside the Home Screen app (§5.4).

### 5.3 Sign-in and staying signed in

- **The Home Screen app has its own sign-in.** iOS keeps a Home Screen app's cookies separate
  from Safari's, so a learner signs in once more after adding it. The sign-in screen is the
  existing one; the OIDC redirect must complete **inside** the Home Screen app and land the
  learner on Your paths. This is the riskiest assumption in the PRD and is proven before any
  other work (§10, Slice 0).
- **The session lasts 90 days since the learner's last visit** (today: 14 days, Starlette's
  default). Every response already renews the cookie, so a learner who visits weekly never signs
  in again. The trade-off (a leaked cookie stays valid for up to 90 days with no server-side
  revocation) is recorded in the TDD as a decision for the owner (§11, P3).
- Sign-out works as today and also removes this browser's **Device** (§5.4).

### 5.4 Notifications

**Kinds (unchanged from iOS PRD D6, and nothing else is ever sent):**

- **(A) Path ready.** When a path's outline generation settles (ready, refused or failed, each
  with its own plain wording). Tapping opens the path view.
- **(B) The Reminder.** Opt-in, off until turned on, at one local hour the learner picks. At most
  one per local day, none on an **Active day**; worded for the **Daily streak** when one is at
  stake. Tapping opens the resume target's lesson, else Your paths. Supersedes the streaks PRD's
  "never a push" for opt-in Reminders only.
- **(C) Brief published.** When a Beat publishes a Brief, never for a Skipped period. Tapping
  opens the Brief.

**Where Notifications work**

- **iPhone:** only in the Home Screen app (iOS 16.4+; the floor is whatever the owner's phone
  runs, iOS 26 today).
- **Android and desktop browsers:** in a tab or an installed app, wherever the browser supports
  Web Push.
- Elsewhere, Notifications settings says they are not available in this browser.

**Permission**

- **Never asked at launch.** The system prompt is shown only after the learner taps **Yes** on a
  one-line in-app pre-prompt, in one of two moments: turning on the Reminder, or after their first
  path creation ("Want to know the moment a path is ready?"). iOS requires that tap.
- A declined system prompt is respected: Notifications settings shows that they are off for
  aleph and how to turn them on in the system Settings. aleph never prompts again on that Device.
- There is no quiet (provisional) stage; that is native-only.

**Notifications settings** (in the account menu, both on phone and desktop)

- The Reminder toggle and its hour; per-kind toggles for Path ready and Brief published. These
  are the learner's preferences, stored on the server, shared by all their Devices.
- Whether *this* browser or Home Screen app receives Notifications, with a button to turn that on
  (which runs the permission flow) and the states above (needs Home Screen, not supported, blocked
  in Settings).

**Devices**

- A **Device** is one browser or Home Screen app where the learner allowed Notifications. An
  Account can have several; each receives every Notification the learner's preferences allow.
- A Device re-confirms itself each time aleph opens, so one whose subscription iOS silently
  dropped recovers on the next visit. A Device the push service reports as gone is removed.
- Signing out removes that Device.

**Content rules (unchanged):** plain words, no Markdown; an id or a same-origin link to open,
never a Read passage, Brief body or tutor content; another learner's id simply `404`s when
opened. A Notification's link always points at this site and nothing else.

**Server safety:** the server sends Notifications only to push-service addresses on a fixed
allowlist of known push services (Apple, Google, Mozilla, Microsoft). A Device registration
naming any other address is rejected. This keeps "register a Device" from becoming a way to make
the server call an arbitrary URL.

### 5.5 The scheduled job

Adopted from the iOS PRD §5.14 without product changes: **one hourly Fly scheduled Machine**
running one job entry point; schedules in a committed file and recreated by the release
workflow on every deploy; each run sends due Reminders, then researches due Beats once per
learner-local Anchor day, moving Brief generation out of `GET /beats` draining so Briefs publish
(and notify) while the learner is away. Idempotent and interruption-safe; the idle-Beat rule and
the daily research cap bound model spend; Neon waking hourly is the accepted standing cost. The
TDD restates the mechanics (from iOS TDD §14) so they no longer depend on a parked document.

### 5.6 The Learner time zone

Adopted from ADR 0001 and iOS TDD §11, web half only: the web app reports the browser's IANA time
zone on every API request; the server stores it when it changes. No screen shows it.

## 6. System design (product view)

- **One client, one API.** The Home Screen app *is* the web app; there is no separate build, URL
  or deploy. A deploy that ships the web app ships the Home Screen app.
- **A service worker for Notifications only.** It receives a push and shows it, and opens the
  right screen when tapped. It caches nothing and does not intercept page loads, so a deploy can
  never leave a learner on a stale bundle.
- **Web Push, standard protocol.** VAPID-signed, encrypted payloads posted to the push service
  named in the Device's subscription. The payload uses the declarative Web Push format (a title,
  a body, and a link to open), which Safari can show without running the service worker and which
  the service worker shows in browsers that need it to.
- **Server-side preferences and Devices.** The web app holds nothing push-related of its own
  beyond the browser's subscription.
- **Bearer auth (PR #218) is left in place.** Unused by the Home Screen app; harmless, and the
  first thing a parked native app would need.

## 7. Success metrics

The north star stays **Activation rate**. The Home Screen app's job is the iOS app's job: more
returns.

**Primary — Return from the Home Screen app.**
> Among learners who use the Home Screen app, the **Return** rate (a second distinct Day) and
> Active days per week, against learners who only use a tab.

If the Home Screen app does not raise Return for the learners who have it, the native app is
the next thing to argue for (§11, P2).

**The `client` dimension.** Product events gain `client`: `web` for a browser tab,
`home_screen` for the Home Screen app (or any installed web app), `ios` reserved for a bearer
client mapped as the parked native app, `unknown` otherwise. Unlike the iOS PRD's version, the
`web`/`home_screen` split comes from the client (it reports its display mode), not from how the
request authenticated. That is acceptable for a metric that nothing grants access on.

**Supporting metrics**

- **Install rate:** share of iPhone learners with any `home_screen` event within 14 days of
  their first visit.
- **Install card:** shown, dismissed, followed by a `home_screen` visit.
- **Notifications opt-in:** share of `home_screen` learners with at least one Device; Reminder
  opt-in rate.
- **Notification effect:** share of Reminders and Path ready Notifications followed by a lesson
  completion that local day.
- **Delivery health:** per kind, sends by outcome (delivered to the push service, Device gone,
  error), and how often a Device re-registers with a new subscription (iOS dropping
  subscriptions shows up here).

**Guardrails**

- **Push restraint:** never more than one Reminder per learner per local day; Reminder opt-outs
  watched.
- **Sign-in failure rate** in the Home Screen app (`auth_callback_failed` by `client`).
- **Brief spend** stays within the analyst's per-learner budget after the job takes over.
- **No polling drift:** `lesson_viewed` per completed lesson in `home_screen` stays within the
  tab's band.

## 8. Core workflows

The Playwright journeys stay the proof. Workflow numbers follow the iOS PRD, because these are
the same product behaviours: **a workflow is one behaviour, whichever client proves it.**

- **W38 — Path ready brings the learner back.** With Notifications on for this browser, start a
  path and leave → the backend's fake push sender records one Path ready Notification for that
  path, with a link to its path view → opening that link shows the path.
- **W39 — A Reminder is opt-in and arrives once.** Turn on the Reminder at a chosen hour → the
  preference is stored → a job run at that local hour records one Reminder; a second run the
  same day records none; a day with a completion records none. (The scheduling half is a
  backend integration test; the journey proves the settings.)
- **W40 — A published Brief is pushed and opens.** A Brief published by a job run → one
  Notification recorded, none for a Skipped period → its link opens the Brief reader.

W35–W37 stay reserved for the parked native app's own workflows. Flow's journey becomes W34
(carried from the iOS TDD).

**What no automated test can prove** (iOS Safari as a Home Screen app is not drivable from CI):
sign-in inside the Home Screen app, a real Notification arriving on an iPhone, and a tap opening
the right screen. These are a **release checklist** run on the owner's iPhone (§9).

## 9. Testing & CI

- **No new CI job.** Everything runs in the existing gate, integration and e2e jobs.
- **Backend:** unit and integration tests for Devices, preferences, the sender (against an
  in-memory fake and fixed test vectors for the encryption), the job, Reminders and Briefs, with a
  fake clock (as iOS TDD §14 specified).
- **Web:** unit tests for the install card's decision, the Notifications state machine, and the
  service worker's payload handling; Playwright journeys W38–W40 with the browser's push
  subscription faked at the edge.
- **Release checklist on a real iPhone** before calling a slice done: add to Home Screen, sign
  in, receive and tap one Notification of each kind that slice adds.

## 10. Phasing

| Slice | Contents | Exit |
| --- | --- | --- |
| **0 — Spike (go / no-go)** | A throwaway build: manifest, a service worker, a hard-coded test push. Deployed, added to the owner's iPhone Home Screen. | Sign-in through Auth0 completes inside the Home Screen app; a test Notification arrives and opens the app at a given link. **If sign-in fails with no clean fix, stop and un-park the native app.** |
| **1 — Installable** | Manifest, icons, Home Screen metadata, install card, navigation audit, 90-day session. Flow on by default and Delete Beat on the web (independent; can land any time). | The owner uses aleph from the Home Screen for a week without opening Safari. |
| **2 — Groundwork** | Learner time zone; the `client` dimension. | Events carry `client`; the owner's zone is stored. |
| **3 — Notifications and Path ready** | Devices, preferences, the sender, the service worker, Notifications settings, permission flow; Path ready. | W38 passes; a real Path ready Notification arrives on the owner's iPhone and opens the path. |
| **4 — The job, Reminders and Briefs** | The hourly job; Brief generation moved into it; Reminders; Brief published. | W39, W40 pass; job idempotency tests pass; one real Reminder and one real Brief published Notification on the iPhone. |

## 11. Decisions, risks and open items

### 11.1 Proposed decisions (owner to confirm)

| # | Question | Proposal | Why |
| --- | --- | --- | --- |
| **P1** | Home Screen app instead of the native app for v1? | **Yes**, gated on Slice 0. | Same return levers, about a third of the work, one client. |
| **P2** | What happens to the iOS PRD and TDD? | **Parked**, with a pointer here. Revisited if Return from the Home Screen app is flat after it has been in use for a month, or if Web Push delivery health is poor. | Keeps the work reusable without implying it is planned. |
| **P3** | Session length? | **90 days since last visit**, still renewed on every response. | Covers a learner who drifts away for weeks. The no-revocation trade-off already exists at 14 days; 90 widens it. |
| **P4** | Notification kinds and permission moments? | **Unchanged from the iOS PRD** (D6 there), minus provisional permission. | Already settled; nothing about Web Push changes the product rules. |
| **P5** | Which push services are allowed? | **Apple, Google (FCM), Mozilla, Microsoft (WNS)** by host suffix; anything else is rejected at registration. | Prevents the server being pointed at arbitrary URLs. |
| **P6** | `client` values? | `web`, `home_screen`, `ios` (reserved), `unknown`. | Measures what this PRD is for. |

### 11.2 Risks

- **R1 — Sign-in inside the Home Screen app (high).** Older reports describe OAuth redirects in
  iOS Home Screen apps escaping to Safari or failing their state check. aleph's flow is a plain
  full-page redirect with state in a first-party cookie, the style most likely to work, but
  nothing confirms iOS 26 behaviour either way. Some social login providers also refuse embedded
  browsers. *Mitigation:* Slice 0 proves it before anything else is built.
- **R2 — Web Push reliability on iOS (medium).** There are WebKit bug reports and developer
  reports of subscriptions silently becoming invalid, and iOS does not fire
  `pushsubscriptionchange`. *Mitigation:* re-confirm the subscription on every launch; remove
  gone Devices; watch delivery health (§7).
- **R3 — Install friction (medium).** No install prompt on iOS, and Notifications need the Home
  Screen app. Fine for the owner and friends; worse for strangers. *Mitigation:* the install
  card; the install-rate metric.
- **R4 — Untestable in CI (low).** The iOS-specific half lives on a manual checklist.
  *Mitigation:* keep it short; keep everything else in Playwright and backend tests.
- **R5 — Inherited from the iOS PRD:** the job is new infrastructure; Brief spend without
  arrival as a bound; Neon waking hourly; push fatigue. Same mitigations (iOS PRD §13.2).

### 11.3 Open items

1. **Auth0 connections.** Which logins production has enabled decides how much of R1 applies
   (a username/password or passwordless login is safest in an embedded browser). Slice 0
   records the answer.
2. ~~**The production origin.**~~ *Resolved:* `https://aleph.mattjmcnaughton.com`. Notification
   links and the VAPID subject use it, and learners add aleph to the Home Screen from it (a Home
   Screen app belongs to the origin it was added from; TDD D15).

## 12. Release criteria

- [ ] Slice 0 passed on the owner's iPhone and its findings are recorded in the TDD.
- [ ] aleph installs to the iPhone Home Screen, opens full-screen with its icon, and every screen
      can be left without a browser back button.
- [ ] A learner who has not visited for 60 days opens aleph and lands on Your paths.
- [ ] W34, W38, W39, W40 pass in CI; the job's idempotency tests pass.
- [ ] Each Notification kind is opt-in where §5.4 says so, sent at most as often as it says,
      arrives on the owner's iPhone and opens the right screen.
- [ ] Device registration rejects push endpoints outside the allowlist.
- [ ] Product events carry `client`.
- [ ] The docs in §2.3 are updated in the same PRs as the code they describe.
