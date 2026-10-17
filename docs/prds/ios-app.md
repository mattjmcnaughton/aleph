# PRD — The iOS app (native SwiftUI client)

**Status:** Draft, decisions recorded (§13) · **Owner:** solo builder · **Roadmap item:** none yet — this PRD proposes one (§2.3)
**References:** [`README.md`](../../README.md) · [`CLAUDE.md` — Two Clients](../../CLAUDE.md#two-clients-web-and-ios) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [`api.md`](../api.md) · [`architecture.md`](../architecture.md) · [`ci.md`](../ci.md) · [`deploy.md`](../deploy.md) · [`metrics.md`](../metrics.md) · [Phase 1 PRD](phase-1-path-generation.md) · [Phase 2 PRD](phase-2-tutor.md) · [Phase 2B PRD](phase-2b-shape-your-path.md) · [Phase 5 streaks PRD](phase-5-streaks.md) · [Phase 6 PRD](phase-6-analyst.md) · [Flow TDD](../tdds/flow.md) · mocks: [MVP mobile](../mocks/aleph-mvp-mobile.html), [phase-2 tutor](../mocks/aleph-phase-2-tutor.html), [path complete](../mocks/aleph-path-complete.html), [Flow mode](../mocks/aleph-flow-mode.html)

> **This document owns the product boundary, plus the client decisions settled with the owner.**
> Those decisions (§6, and the Decisions record in §13) are recorded here because they shape scope
> and phasing; their mechanics — module layout, the generated client's configuration, the SSE
> parser, the polling scheduler, the Keychain schema, the fastlane lanes, the push sender, the
> scheduled job — belong to a companion **iOS TDD** (`docs/tdds/ios-app.md`, not yet written).
> Where this PRD names a mechanism it is because the product rule is unintelligible without it.
>
> **The TDD opens with an iOS best-practices brainstorm** — for the code (project and module
> layout, Swift concurrency, Observation, dependency injection behind protocols, testing seams,
> Swift Package Manager, linting and formatting) and for the app (navigation, accessibility,
> Dynamic Type, backgrounding, push permission etiquette, Keychain use, signing and TestFlight
> hygiene) — **written for an owner who is new to iOS**: each practice says what it is, why iOS
> developers follow it, and what it would cost to ignore here. Design decisions come after it and
> cite it.
>
> **Bearer auth is delivered.** PR #218 merged it to `main`; §5.1's backend requirements are met
> as documented in [`api.md`](../api.md#authentication) and
> [`deploy.md`](../deploy.md#native-app-bearer-token-configuration). The **home-screen PoC** that
> proves the CI build-and-screenshot loop (Slice 0, §12) is still on another branch.

## 1. Summary

Aleph is mobile-first by design, but today it is a web app in a phone browser. This PRD specifies
a **native iPhone app**, written in SwiftUI, that is a second client of the **same `/api/v1`** —
same Account, same paths, same progress, same tutor, same Beats. A learner can start a path on the
web, read the next lesson on the train in the app, and come back to the web with nothing out of
step, because there is only ever one server and one source of truth.

**v1 is full feature parity with the web** (the rule is CLAUDE.md's
[Two Clients](../../CLAUDE.md#two-clients-web-and-ios) section): paths, lessons, the tutor,
shaping, streaks, **Beats and Briefs** (the analyst) and **Flow**, plus the one thing only a
native app can do — **push notifications**. It ships in slices. The first slice is the core loop
and nothing else: **sign in → Your paths → open a lesson → read the Read passage → answer the
Quick check → Mark complete.** Each later slice is self-contained.

**The core design claim: the existing API does not change shape.** The app reuses every
`/api/v1` route as documented. Authentication was the one change a native app forced —
`Authorization: Bearer <OIDC access token>` beside the session cookie — and it has shipped (PR
#218). The remaining backend work is **additive** and named here so nothing is a surprise:

- a server-derived **`client` dimension** on product events (§7, D5);
- **push**: device-token registration, reminder preferences, an APNs sender (§5.13, D6);
- an **hourly scheduled job** that sends reminders and runs Beat research, moving Brief generation
  off request-triggered draining so a Brief publishes — and pushes — while the learner is away
  (§5.14, D18).

Everything else the app needs, the web app already needed: trigger + poll for generation, an SSE
stream for the tutor and shaping, answer-hiding on the Quick check, untrusted Markdown at the edge.

The app lives in **`clients/ios/`** in this repository (D16), so an API change and its client
change land in the same PR, and it is distributed through **TestFlight** — internally to the
owner, then by public link to friends. App Store submission is **deferred**; everything only it
needs is listed apart in §10.3.

## 2. Context & goals

### 2.1 Why now

Every Phase 1 metric is a *return* metric: Activation is four lessons on one path inside seven
days, Return is a second distinct Day. A phone browser tab is the weakest possible place to
return to — no home-screen icon by default, sign-in that expires with the cookie, no native
navigation, no notifications, and nothing to tap at 7am except a bookmark. A native app is a
stronger door into the same loop, and the loop itself is already built and launched: Phases 1, 2,
2B, the streaks slice and the analyst are all shipped, and Flow is built behind its flag, so the
client has a complete, stable API to target.

It is also cheap in the way that matters. The backend has done the hard work: generation is
trigger + poll (so the client never holds a long request open), the tutor's stream is documented
event-by-event, answer-hiding is a property of the payload rather than the UI, and every route
already returns `404` rather than `403` for another learner's data. The app is a renderer and a
state machine over that contract.

### 2.2 Goals

- The owner can do a day's learning entirely in the app — read, check, complete, ask the tutor,
  shape a path, read a Brief, run a Flow — with no reason to open the web app.
- **One Account, two clients, zero drift.** Anything done in one client is visible in the other on
  its next read. The app keeps no second copy of anything the server owns. Feature parity is the
  standing rule (CLAUDE.md, *Two Clients*); push is the one platform-only capability (§5.13).
- The app is **at least as safe** as the web app with model output: generated Markdown is
  untrusted, the Quick check answer is never available before an Attempt, and a failed or refused
  generation has a defined state.
- The app is buildable, testable and screenshot-reviewable **from CI alone**, so an agent working
  in a Linux container (with no Xcode) can change Swift code, push, and see the result (§9.5).
- Friends can install it from a TestFlight public link without the owner touching their devices.

**Non-goals (v1)**

- **No API redesign.** No mobile-specific reshaping of existing endpoints, no GraphQL, no BFF. The
  additions in §1 are new routes and a new process entry point, not changes to existing payloads.
  If a screen seems to need anything else, that is a TDD question raised before it is built.
- **No offline mode.** The app reads and writes through the API; a dropped connection is an error
  state with a retry, not a sync queue.
- **No admin surfaces.** The admin model picker, the tutor's per-message model override and the
  feature-flag admin routes stay web-only (§4).
- **No iPad- or Mac-specific layout.** iPhone only; the app may run on iPad in compatibility mode,
  but no layout work targets it.
- **No third-party analytics, crash or state-management SDKs.**
- **Nothing that only App Store submission needs** (standing rule, §13): Sign in with Apple,
  in-app account deletion, a privacy policy, AI-content review prep and the rest are in §10.3, not
  in requirements.

### 2.3 What accepting this changes about the plan

The vocabulary and docs are authoritative, so the app lands with them, not after them. Accepting
this PRD includes:

- **[`roadmap.md`](../roadmap.md)** — a new section, *iOS app*, beside *Flow*, with this PRD as its
  spec and the §12 slices as its status line.
- **[`CONTEXT.md`](../CONTEXT.md)** — the app itself introduces **no new domain terms**: every
  screen here is named with an existing term. One clarifying line under *Design*: **Nocturne** also
  governs the iOS app, and the **Rail** on a phone is a SwiftUI sheet (one surface, now three
  presentations). **Push does add terms** — the daily learning reminder, the streak reminder and
  whatever the TDD names the reminder preference — and they go into CONTEXT.md before any code
  uses them. The scheduled job also changes two existing entries: **Anchor day** ("no stored
  timezone", "a day, never a time", arrival as the trigger) and **Brief prefetch** (deferred
  because "the app sleeps between visits") both stop being true once the job researches Beats
  (§5.14).
- **[`api.md`](../api.md)** — bearer auth is already documented (PR #218). New sections for device
  registration and reminder preferences (§5.13), and the analyst section updated for the job
  replacing the arrival drain (§5.14).
- **[Phase 6 PRD](phase-6-analyst.md)** — §4.2's "why this and not a scheduler" and its "no stored
  timezone" reasoning are superseded by §5.14; the TDD amends that PRD in place (or records the
  supersession at its top, as the streaks PRD now does for push).
- **[Phase 5 streaks PRD](phase-5-streaks.md)** — its "never a push" rule is superseded for
  **opt-in** push reminders (D6). Done alongside this PRD.
- **[`metrics.md`](../metrics.md)** — the `client` dimension on product events (D5).
- **[`architecture.md`](../architecture.md)** — `clients/ios/`, and the job entry point
  (e.g. `aleph.jobs.tick`) as a new top-level process beside the web app.
- **[`ci.md`](../ci.md)** — the `ios` workflow, its home-written path filter and its required
  checks (§9.4).
- **[`deploy.md`](../deploy.md)** — the TestFlight runbook, the APNs key, the scheduled-machine
  lifecycle (§5.14) and every new secret (§10).
- **[`development.md`](../development.md)** — Xcode setup, pointing the simulator at a local
  backend and Keycloak, running the job locally.

## 3. Target user & user stories

**User:** unchanged from Phase 1 — a self-directed adult learner on a phone. What is new is that
they have an iPhone and would rather tap an icon than find a tab. The first users are the owner
(TestFlight internal) and a handful of friends (TestFlight external).

**Core stories**

1. *As a learner,* I sign in once in the app with the same account I use on the web, and I see
   **my** paths — the ones I made on the web — with my progress intact.
2. *As a learner,* I tap *Continue* and land on the lesson I'm up to, read it, answer its Quick
   check, see the Outcome and explanation, and Mark complete — and the next lesson is waiting.
3. *As a learner,* I complete a lesson on my phone and the web app shows it complete the next time
   I look.
4. *As a learner,* I open a lesson that is still generating and the app tells me so and fills in
   when it is ready — and if generation failed, I can retry with one tap.
5. *As a learner (Slice 2),* I start a new path from the app: topic, level, optional Guidance and
   Teaching style.
6. *As a learner (Slice 3),* I'm stuck on a paragraph and ask the tutor from inside the lesson; the
   reply streams in about **this** Read passage.
7. *As a learner (Slice 4),* I ask the shaping rail to add two lessons on something I'm shaky on,
   see them as ghost rows, Apply, and Undo if I change my mind.
8. *As a learner (Slice 5),* I see my Daily streak on Your paths and it ticks over on my first
   completion of the day.
9. *As a learner (Slice 6),* I deploy an analyst on a moving subject, read its Briefs with their
   Sources, and see a Skipped week for what it is.
10. *As a learner (Slice 7),* I have twenty minutes, so I start a Flow of five lessons across two
    paths and let each completion carry me into the next.
11. *As a learner (Slice 8),* I start a path, put the phone away, and get a notification when it is
    ready; I opt in to a reminder at 8pm on days I haven't studied; and I'm told when a new Brief
    is published.
12. *As a friend of the owner,* I install from a TestFlight link, sign in, and make my first path
    without anyone walking me through it.

## 4. Scope

**In scope (v1 = Slices 1–8, §12)**

- Sign-in and sign-out via OIDC (PKCE, public client) — §5.1.
- **Your paths** — the Switcher as the home screen, with the Beats section and the Flow door — §5.2.
- The **path view** with its path rail, read-only in Slice 1; rename, Teaching style and delete in
  Slice 2 — §5.3.
- The **lesson view**: Read passage, Quick check, Attempt and Outcome, Mark complete, every
  Generation state, path completion — §5.4.
- **New path** (onboarding) — §5.5.
- The in-lesson **tutor** (Rail as a sheet, Suggestions, Tutor check, streamed replies) — §5.6.
- **Shape your path** (shaping rail, Proposals, ghost rows, Apply, Undo, Change history) — §5.7.
- **Streaks** — Daily streak, Best streak, the activity strip, Path streak chips — §5.8.
- Honouring the learner's **feature flags** from the session probe on every gated surface — §5.9.
- **Beats & Briefs** (the analyst): deploy an analyst, a Beat's page, the Brief reader — §5.10,
  §5.11.
- **Flow**: the door, setup, the flow bar and advance, the receipt — §5.12.
- **Push notifications**: "your path is ready", opt-in daily learning and streak reminders, "Brief
  published" — §5.13 — and the **scheduled job** that sends reminders and runs Beat research —
  §5.14.
- Unit, snapshot and full-stack UI tests; a macOS CI workflow; TestFlight distribution — §9, §10.

**Out of scope for v1 — and where it goes**

| Deferred | What it is in the web app today | Goes to |
| --- | --- | --- |
| **Admin surfaces** | The per-path model picker on New path (`model_outline`/`model_lesson`), the same picker on Deploy analyst (`model_research`/`model_brief`), the tutor's per-message `model` override, the feature-flag admin routes. | Web only, indefinitely — they are operator tools, not learner features. The app never sends a model override. |
| **Quote / selection to quote, path scope, Shaky badge** | Not built on the web either (CONTEXT.md phase boundaries). | With the web's later Q&A slice, in both clients at once. |
| **Sandboxed Mermaid rendering** | The web renders Mermaid diagrams. | After v1; the app shows Mermaid as labelled source (§5.4.1). |
| **Offline reading, widgets, Live Activities, Siri/App Intents, Universal Links** | — | Unscoped. Universal Links to `/lessons/{id}` are the most likely next ask. |
| **App Store submission** | — | Deferred; its requirements are collected in §10.3 and are not v1 requirements. |

## 5. Functional requirements

Requirements are per screen. Every screen has a **defined state for every server state it can
see** — loading, empty, generating, failed, refused, locked, error — and **never** a spinner that
cannot resolve, the same rule Phase 1 §5.6 set for the web.

**Cross-cutting rules (every screen)**

- **The server is the source of truth.** The app caches for display only; after any write it uses
  the write's response (e.g. `apply-proposal` returns the refreshed path) or re-reads.
- **Trigger + poll** for every generated thing. A poll runs in a SwiftUI `.task` bound to the
  screen, so **leaving the screen cancels it**, as does the app moving to the background; returning
  restarts it. Cadence matches the web's shared helper — first poll at 2s, backing off to 5s — so
  request volume (and the `lesson_viewed` event a `GET /lessons/{id}` emits, metrics.md) stays
  comparable between clients. A poll that has not resolved after ~45s stops and degrades to the
  screen's recovery state, as the web's `GENERATION_STALL_MS`/`LIST_STALL_MS` do.
- **Errors use the shared envelope.** `401` → the auth flow of §5.1 (refresh, then sign-in);
  `404` → "not found" (never "forbidden" — the API does not disclose existence and neither does
  the app); `409` with `details.reason` → the reason's specific state (§5.7); `429 rate_limited`
  → a plain "daily limit reached, try tomorrow" with nothing to retry; `503
  service_unavailable` → a retryable "can't reach sign-in right now"; anything else → a message
  and a retry that preserves what the learner typed or selected.
- **`tz_offset_minutes`** wherever a route takes it, as the web sends it: JavaScript's
  `getTimezoneOffset()` convention (minutes to *subtract* from UTC, so a zone ahead of UTC is
  negative). This is the **opposite sign** of Foundation's `secondsFromGMT()`; the conversion is
  one function, unit-tested at both signs and at a DST boundary.
- **Accessibility is a requirement, not a polish item.** Dynamic Type through the accessibility
  sizes without truncating a Read passage, a stem, an option or a Brief; VoiceOver labels and
  traits on every control (a Quick check option announces *selected*, *correct*, *incorrect*); no
  meaning carried by colour alone (unlock, generation and research states have text or a symbol as
  well as a tint).
- **Nocturne, dark only.** The app extends the visual system — night background, porcelain text,
  teal for the path, iris for the tutor and the analyst — using native controls rather than
  imitating web ones. Like the web (`color-scheme: dark`), the app is **dark-only in v1** (D13): it
  pins the dark appearance regardless of the system setting. No mock exists for iOS; the snapshot
  suite (§9.2) is the visual record, on the Phase 5 precedent of specifying against tokens
  directly.

### 5.1 Sign-in, the session, and bearer auth

**What the learner sees.** A launch screen with the Aleph mark and one *Sign in* button. Tapping
it opens the identity provider's page in an `ASWebAuthenticationSession` sheet; on success the app
lands on Your paths. On cancel the learner is back on the launch screen with no error; on failure
they see a short message and the button again. Relaunching the app never asks again while the
refresh token is valid. *Sign out* lives in a small account menu on Your paths (display name,
email, notification settings from Slice 8, *Sign out*).

The login options are **whatever the production Auth0 tenant offers today** (D1). The app adds no
connection; Sign in with Apple is an App Store item (§10.3).

**Client requirements**

- The app is a **public OIDC client** — no client secret ships in the binary — using the
  authorization-code flow with **PKCE (S256)** and a `state` check, through
  `ASWebAuthenticationSession`. **Auth0** in production (its *Native* application), **Keycloak** in
  development and CI (the `aleph-ios` public client in the dev realm), chosen by build
  configuration. The app discovers endpoints from the issuer's
  `.well-known/openid-configuration`, the same way the backend does.
- The OIDC flow is implemented with **AppAuth-iOS** behind an app-owned **`AuthSession`
  protocol** (D15), so screens and the API layer depend on the protocol and tests use a fake.
- The web-auth session is **ephemeral** (`prefersEphemeralWebBrowserSession = true`, D3): no
  "wants to use … to sign in" system prompt, no Safari cookie left behind, no SSO with Safari.
- The redirect URI is the custom scheme **`aleph://oauth/callback`** (D14), already registered on
  the Keycloak `aleph-ios` client (post-logout `aleph://oauth/logout`) and to be registered on the
  Auth0 Native application.
- It requests `openid profile email offline_access` **and the API audience** (D2): on Auth0, the
  `audience` parameter set to the Auth0 **API Identifier** — the value the server holds as
  `OIDC_API_AUDIENCE`; on Keycloak, nothing extra, because the `aleph-ios` client's audience mapper
  puts `aud=aleph-api` in every access token. `openid` is load-bearing: the server resolves the
  learner's identity and verified email from the provider's **userinfo** endpoint with the same
  token. Tokens are stored in the **Keychain**, device-only (`…ThisDeviceOnly`), never in
  `UserDefaults` or on disk.
- Every API request carries `Authorization: Bearer <access token>`. A `401` triggers **one**
  refresh-and-retry; concurrent `401`s share a single refresh. A failed refresh clears the tokens
  and returns to the launch screen — never a loop, never a crash, and never a request sent without
  the header.
- **Sign out** deletes the Keychain items and in-memory state and unregisters the device's push
  token (§5.13). It does not call `/auth/logout` (that route clears a browser cookie the app does
  not have) and, because the auth session is ephemeral, needs no provider logout.
- After sign-in the app calls **`GET /api/v1/auth/session`** and treats its `user` (display name,
  `is_admin`, `feature_flags`) as the signed-in state, exactly as the web's root `beforeLoad` does.

**Backend requirements — delivered by PR #218.** Recorded so the TDD can cite them; each is met
as documented in [`api.md`](../api.md#authentication).

1. ✅ **Every `/api/v1` route that accepts the session cookie also accepts
   `Authorization: Bearer <access token>`**, including both SSE send routes and
   `GET /api/v1/auth/session`. Cookie behaviour for the web app is unchanged.
2. ✅ **Same human, same Account.** A bearer token resolves to the Account keyed by the token's
   `(iss, sub)` — the key the cookie login provisions.
3. ✅ **First sign-in can happen in the app.** An unknown identity is provisioned exactly as a
   first web login. Display name and a **verified** email come from **userinfo**, never from the
   token body or from anything the app sends; an unverified email is dropped.
4. ✅ **Validation is strict.** Signature against the issuer's JWKS, an asymmetric `alg`
   allowlist, `iss` equal to `OIDC_ISSUER`, `aud` containing `OIDC_API_AUDIENCE`, `exp` required.
   Any malformed, expired or wrong-audience token is **`401` in the shared envelope** — it never
   falls back to a cookie, and `GET /auth/session` answers `401` rather than
   `authenticated: false`, so "refresh needed" is unambiguous to the app.
5. ✅ **No cookie side effects.** A bearer request neither reads nor sets the session cookie.
6. ✅ / ⏳ **Provider configuration for a public native client.** Keycloak's `aleph-ios` client
   (and the CI-only `aleph-api-test` password-grant client for fixtures) are in the dev realm. The
   Auth0 API and *Native* application are a runbook step in `deploy.md`, done before the first
   TestFlight build that targets production.

### 5.2 Your paths (home)

The Switcher, as the home screen — one `GET /api/v1/paths` payload, the same ordering the web
uses (most recently worked first, never-worked paths after).

- A **Continue** card at the top for the resume target — the path the web's `pickResumeTarget`
  would choose — showing the path title and its `next_lesson` title, opening that lesson directly.
- One row per path: **Path title**, Level label, a status line (*Generating…*, *Couldn't generate*,
  *Not available* for refused, or progress such as *4 of 22*), and a *Complete* marker when
  `next_lesson` is null on a ready path. Tapping a row opens the path view.
- **Polling** while any row is `pending`/`generating`, under the cross-cutting stall rule; the row
  itself links to the path view, where retry lives. **Pull to refresh** re-reads at any time.
- **Empty state.** In Slice 1 there is no path creation in the app, so an empty list says how to
  make a path on the web; from Slice 2 it is the *New path* call to action.
- **Slice 2:** a *New* menu (*New path*; *Deploy analyst* from Slice 6), and **swipe-to-delete**
  with a confirmation dialog (Delete is destructive and not undoable, Phase 1 §5.5). The confirm
  says nothing about streaks (Phase 5 §4.6).
- **Slice 5:** the streak line and activity strip above the list, and Path streak chips on rows
  (§5.8).
- **Slice 6:** the **Beats** section beside Your paths (§5.10).
- **Slice 7:** the **Flow door** directly under Continue (§5.12).

### 5.3 Path view

One `GET /api/v1/paths/{id}` payload, polled while the outline is not terminal **or** any lesson
is `generating` (the web's `isPathViewTerminal`).

- **Non-ready states:** *generating* (a waiting state, polling); **refused** (the
  `refusal_message`, styled as a graceful message — never as an error, Phase 1 D12); **failed**
  (a message and *Retry*, which calls `POST /paths/{id}/retry`); not found.
- **Ready:** the Path title and progress in the header, then the **path rail** — units in order,
  each with its lessons, each lesson showing its **Unlock state** (*complete* / *available* /
  *locked*; the available one labelled as current) and, where relevant, its **Generation state**
  (*generating*, *failed*). Available and complete lessons open the lesson view; locked ones do not
  respond to taps and say why to VoiceOver.
- A *Continue* affordance for the available lesson, and a **complete banner** when every lesson is
  complete.
- **Slice 2:** rename (**Path title**, `PATCH /paths/{id}`, 1–200 chars) and edit **Teaching style**
  (`PUT /paths/{id}/teaching-style`, 1–2000 chars or cleared). The Teaching style editor states
  that a change is **forward-only** — lessons already written keep their style. **Guidance** is
  shown read-only (it is frozen at creation and no route changes it); the **Topic** is never
  editable. Delete is also offered here.
- **Slice 4:** the shaping rail's entry point and ghost rows (§5.7).
- **Slice 7:** a quiet *Start a flow* link beside Continue, opening Flow setup scoped to this path
  (§5.12).

### 5.4 Lesson view

One `GET /api/v1/lessons/{id}` payload — the screen the first slice exists for.

**Generation states**

- *ungenerated* / *generating* → a waiting state naming the lesson, polling; the poll is itself
  the trigger (it spawns the idempotent resume and refills the prefetch window), so the app need not
  call `generate` to start it.
- *failed* → `generation_error` (already learner-safe) and *Retry* → `POST /lessons/{id}/generate`
  (`429` at the daily lesson cap → the limit message).
- **Stalled** (polling gave up) → a notice pointing back to the path view, where a failed chain
  head's retry lives (api.md's *completing past a failed head* dead-end — the app must not
  spin on it).
- *locked* → a notice and a way back; nothing else.

**The lesson (generated)**

- The title and position, then the **Read passage** rendered from Markdown (§5.4.1).
- The **Quick check**: the stem and 3–4 options as **plain text** (they are not Markdown), each a
  full-width tappable control. Selecting an option and confirming posts
  `POST /lessons/{id}/attempt {selected_index}`. The app holds **no answer** before that response:
  the pre-Attempt payload contains none (W6), and the app adds no client-side grading.
- **Outcome:** the response (or a later `attempt` on the payload) reveals *correct* / *incorrect*,
  marks the learner's option and the correct option, and renders the `explanation` as **inline**
  Markdown only. A second Attempt is impossible in the UI and harmless on the wire (first-wins).
- **Mark complete** — enabled whenever the lesson is *available*, whatever the Outcome, and even
  before an Attempt (completion, not correctness, is what counts; the Quick check is non-gating).
  An already-complete lesson shows its complete state instead; an Attempt is still allowed on a
  complete lesson (api.md).
- **After completion:** *Next lesson* (the lesson at the next Position in path, from the cached
  path) and *Back to your path*. When the response's `path_completion` is non-null, the
  **path-complete** card replaces them on the same frame — lesson count and the span of the run,
  with the Day arithmetic done on device in the learner's time zone, as the web does — with its
  doors: *Go deeper on <topic>* (New path prefilled with the topic) and, from Slice 6 and behind
  the `analyst` flag, *Follow it as a Beat* (Deploy analyst prefilled with the topic).
- **Slice 3:** the tutor's floating mark (§5.6). **Slice 5:** the *Day N 🔥* acknowledgement on
  the day's first completion (§5.8). **Slice 7:** inside a Flow, the flow bar and the flow's
  advance card replace the after-completion doors (§5.12).

**5.4.1 Rendering the Read passage (untrusted model output)**

The Read passage is GitHub-Flavored Markdown written by a model, served as source. The renderer is
the iOS equivalent of `components/markdown.tsx` — the **single** place the app turns generated
prose into views, and its security boundary.

- Rendered with **swift-markdown-ui** (or an equivalent native renderer chosen in the TDD) over the
  documented subset: `##`/`###` headings, lists, emphasis, inline code, fenced code blocks
  (horizontally scrollable, monospaced), GFM tables, blockquotes.
- **No raw HTML is ever interpreted.** HTML in the source renders as literal text or is dropped,
  never as markup, and nothing is handed to a web view.
- **Links:** only `http`/`https` (and `mailto` if the TDD finds a reason) are tappable, and open
  outside the app (Safari or `SFSafariViewController`); `javascript:`, `data:`, `file:`, custom
  app schemes (including the app's own `aleph:`) and anything else render as plain text.
- **No remote images** are fetched (the prompt asks for none; the renderer enforces it rather than
  trusting the prompt).
- **Mermaid:** a ` ```mermaid ` fence renders as **its source in a code block**, labelled as a
  diagram — the API's documented fallback, and lossless because the prompt requires the surrounding
  prose to explain the diagram. A sandboxed `WKWebView` renderer (no network, no navigation,
  mermaid at `securityLevel: "strict"`, falling back to source on a parse error) is a **later
  enhancement**, not v1 (§4).
- The same renderer is reused for the tutor's and shaping replies, the Outcome explanation (inline
  subset) and **Brief bodies** (§5.11).

### 5.5 New path (Slice 2)

The onboarding capture, as the web's `/new`.

- **Topic** (required, 1–500 chars after trimming), **Level** (*new to it · some experience · I
  work in it*), optional **Guidance** (≤ 4000) and optional **Teaching style** (≤ 2000), each with
  a one-line hint that matches the web's copy. Client-side limits mirror the server's; the server's
  `422` is still handled. A topic passed in (from the path-complete door) prefills the field.
- Submit → `POST /api/v1/paths` → `202 {id}` → poll `GET /paths/{id}`: **ready** navigates to the
  path view; **refused** shows the refusal message gracefully with the topic preserved for editing;
  **failed** keeps the learner on the form, inputs intact, with one-tap retry
  (`POST /paths/{id}/retry`); `429` shows the daily limit.
- Leaving the screen mid-generation cancels the poll, not the generation: the path appears on Your
  paths in its current state — and from Slice 8, a **"your path is ready"** push arrives when the
  outline lands (§5.13).
- No model picker, even for an admin (§4).

### 5.6 The tutor (Slice 3)

The Phase 2 in-lesson tutor, unchanged in behaviour; its PRD governs. Gated by the `tutor` flag.

- **Entry:** a floating mark on the lesson view opens the **Rail** as a sheet over the lesson
  (medium and large detents); the lesson stays visible behind it. No entry point anywhere but a
  lesson.
- **Context chip** naming the scope (*Reading · <lesson title>*), an empty state listing what the
  tutor can see, and the four **Suggestions** (*Explain this simpler · Go deeper · Quiz me on this
  · Show me a real example*), sent with `source: "suggestion"`.
- **Thread:** `GET /paths/{id}/conversation` on open — one Conversation per path, so earlier
  lessons' messages appear too. **New conversation** → `DELETE`, after a confirm.
- **Streaming:** sending posts `POST /paths/{id}/conversation/messages` and reads the
  `text/event-stream` body with `URLSession.bytes(for:)` — `EventSource`-style GET clients cannot
  POST a body, the same reason the web uses `fetch`. Frames: `delta` text appends; `tutor_check`
  attaches a **Tutor check** to the message (not to a position in the text); `done` settles the
  turn; `error` shows its learner-facing `message` and a retry with the question preserved; `: ping`
  is ignored. The composer is disabled while a reply is in flight, with a **Stop** that cancels the
  task (and so the request) — nothing is persisted and the partial text is discarded, matching
  "a turn exists whole or not at all." A pre-stream `409` (a reply already in flight) is handled
  as "wait for the current reply."
- **Tutor check:** options with immediate feedback from the delivered payload, the answer recorded
  via `POST /messages/{id}/tutor-check-answer`, and the plain statement that it does not count
  toward the lesson.
- **Never on the critical path.** No tutor state blocks reading, attempting or Mark complete.
- The stream survives the sheet being dismissed and re-opened within the lesson; leaving the
  lesson cancels it.
- **Backgrounding (D8).** When the app leaves the foreground mid-reply it requests extra time with
  `beginBackgroundTask`, so a short reply usually finishes and persists. If the stream is lost
  anyway (the time runs out or the system suspends the app), the partial reply is discarded as
  with Stop, and the learner's message is **back in the composer** with a one-tap resend on return.

### 5.7 Shape your path (Slice 4)

The Phase 2B shaping rail, unchanged in behaviour; its PRD governs. Gated by the `shaping` flag,
and offered only on a **ready** path.

- A sheet from the path view with its own thread (`/paths/{id}/shaping/conversation`), the same
  streaming as §5.6 (including the D8 backgrounding rule) plus the `proposal` frame.
- A **Proposal** card per proposal — summary, each Addition/Revision with rationale and cost —
  with **Apply** and **Not now**. While a Proposal is pending in the open thread, its **ghost rows**
  appear in the path rail in iris, not teal.
- **Apply** → `POST /messages/{id}/apply-proposal`; the response's `path` replaces the rail in one
  step. Every coded `409` renders its own state: the stale reasons (`insert_position_taken`,
  `revision_target_engaged`, `title_conflict`, `positions_shifted`, `path_cap_reached`,
  `invalid_proposal`) as *ask again*; `target_generating` as *try again in a moment*;
  `already_applied`/`already_undone` as the settled state.
- **Change history** (`GET /paths/{id}/changes`), newest first, with **Undo** on the newest live,
  un-engaged Change (`POST /changes/{id}/undo`); `not_latest` explains that newer Changes must be
  undone first, `engaged` that the Change is now permanent history.
- A **Declined edit** or safety refusal is just a reply — the app looks for no marker.

### 5.8 Streaks (Slice 5)

The Phase 5 streaks slice, unchanged in behaviour; its PRD governs. Gated by the `streaks` flag;
one read, `GET /progress/summary?tz_offset_minutes=…`, refreshed on appear and after each Mark
complete.

- The streak line above Your paths: `🔥 5-day streak · 1 lesson today`, with `best 12` only when
  the Best streak exceeds the current one, the trailing clause dropped on a day with nothing yet,
  and *Complete a lesson to start a streak* at zero.
- The activity strip below it (the payload's 49 days, oldest first, three teal intensities).
- A neutral Path streak chip (`3-day`) on rows with a Path streak ≥ 2 — no flame.
- On the day's first completion, a brief, non-blocking *Day N 🔥*; nothing on later completions.
  The count bumps optimistically from `completed_today`, as the web does.
- **In the app, never** a streak warning banner, an in-app nudge, a freeze or a leaderboard. The
  one exception to the streaks PRD's "never a push" is the **opt-in streak reminder** of §5.13
  (Slice 8, D6), which the learner turns on themselves; the streaks PRD records the supersession.
- A failed summary read renders nothing, not an error: the streak is decoration, never a blocker.

### 5.9 Feature flags

The app reads `user.feature_flags` from `GET /api/v1/auth/session` and treats an unknown or
not-yet-loaded key as **off**, as the web's `useFeatureFlag` does — a gate never flashes open. A
flagged surface whose flag is off renders **no entry point at all** (not a disabled one), and a
`404` from a flagged route mid-use (a kill switch flipped) collapses that surface quietly rather
than erroring the screen. The flags are re-read on foreground, so a kill switch reaches the app
without a reinstall. The app honours the same keys the web does — `tutor`, `shaping`, `streaks`,
`analyst`, `flow` — and no app-only flag exists unless the TDD finds one is needed for push.

### 5.10 Beats — deploy an analyst, and a Beat's page (Slice 6)

The Phase 6 analyst, unchanged in behaviour; its PRD governs (as amended by §5.14 for *when*
research runs). Gated by the `analyst` flag. The web's routes are `/beats/new` and `/beats/{id}`.

- **Beats on home.** A collapsible *Your beats* section beside (never merged into) Your paths,
  from `GET /beats?tz_offset_minutes=…`: one row per Beat, newest first, with its Topic, a line of
  research state (*Researching… · started 30s ago*, *Research didn't finish. Open to retry.*, the
  refusal) and an **unread count** (published Briefs not yet read; a Skipped entry is never
  counted). Tapping opens the Beat's page. Read on appear and pull to refresh, **never polled**
  (as on the web). With no Beats it shows the web's empty line pointing at *Deploy analyst* in the
  *New* menu.
- **Deploy analyst** (`/beats/new`). **Topic**, **Level**, the **Anchor day** it *Reports on*
  (a weekday picker; the wire value is Python's Monday = 0, which the app maps explicitly and
  tests), and optional **Guidance**, with the web's hints and limits. Submit →
  `POST /beats?tz_offset_minutes=…` → `202` → the Beat's page. `429` at the Beat cap
  (`MAX_BEATS_PER_LEARNER`) → a plain "you have the most analysts you can deploy" state. A topic
  passed in (from the path-complete door) prefills the field. No model picker (§4).
- **A Beat's page** (`GET /beats/{id}?tz_offset_minutes=…`). Its **standing orders** as the web's
  display-only one-line head (*Weekly · <Topic> · <Guidance>*) — changing them means deleting and
  redeploying; its **research state**:
  - *researching* → a waiting state, polling under the cross-cutting rule;
  - *failed* → the web's "We couldn't finish this run" and *Try again* →
    `POST /beats/{id}/retry` (`429` → the limit message);
  - *refused* → the `refusal_message`, graceful, never an error, and never hiding the Beat's
    existing history;
  - *idle* → nothing extra.
- **The rail of entries**, newest first, both kinds in one list: a **published** entry
  (*Brief #N*, its date, its title, and an unread marker until read) opens the Brief reader; a
  **Skipped** entry is a dated one-line row with its `skip_line`, links nowhere, and is styled as
  the feature working — never as a failure.
- **No Delete control yet.** `DELETE /beats/{id}` exists, but the web ships no control for it today
  (only an API helper), although the refused state's copy tells the learner to delete the Beat.
  Under the parity rule the app gets Delete (with a not-undoable confirm) in the same PR as the
  web, not before.
- **Unavailable** (`404`, or the flag turned off mid-use) → the web's "It may have been deleted"
  state with a way back.

### 5.11 The Brief reader (Slice 6)

The web's `/briefs/{id}`: one `GET /api/v1/briefs/{id}`.

- *Brief #N*, its `published_on` date and title, then the **body Markdown** through the one
  renderer (§5.4.1), on the lesson reading surface.
- The **Builds on Brief #N** line when `builds_on` is non-null, linking to that Brief.
- **Sources** listed at the foot — publisher, title, date, link — a first-class region of the
  page. Source links obey §5.4.1's scheme filter and open outside the app.
- **Read pings** as the web sends them: `POST /briefs/{id}/read {marker: "opened"}` on open, and
  `{marker: "sources"}` when the Sources region is first scrolled into view, each with
  `tz_offset_minutes`. Pings are fire-and-forget: a failed ping never errors the screen.
- No Quick check, no Unlock state, no Mark complete — a Brief is not a Lesson (CONTEXT.md).

### 5.12 Flow (Slice 7)

The web's Flow, behind the same `flow` flag (dark by default on the server today, on for admins),
re-implemented in Swift because Flow is **100% client-side** — no route, nothing stored on the
server. The [Flow TDD](../tdds/flow.md) governs behaviour; its pure transitions (the
`lib/flow-order.ts` rules: eligible paths, Interleave / Random / one-path-at-a-time, *next
available lesson* within a path) are a **rule written twice and tested twice** (CLAUDE.md).

- **The door.** A *Flow* row directly under Continue on Your paths, rendered only when at least one
  path has a `next_lesson` (the same test as the Continue card); and a quiet *Start a flow* link on
  the path view (§5.3) that preselects that path.
- **Setup** (`/flow/new`). **Flow length** (3, 5, 8, or *until I stop*), **Flow scope** (which
  paths — with *Select all* — and their order: **Interleave**, the default; **Random**; or one path
  at a time), a *First up* line naming the lesson the flow will open, and *Start flow*.
- **In the flow.** Each lesson is the ordinary lesson view under a **flow bar** showing progress
  (*k of n*) and an *End flow* control. Every completion is an ordinary Mark complete. After it, the
  flow's **advance card** replaces the usual doors and counts **5 seconds** into the next lesson,
  with a tap to go now; leaving the flow's lesson or setup screen **ends the flow** (leaving *is*
  ending), as on the web.
- **The receipt** (`/flow/done`): the lessons completed with their Quick check Outcomes, and — when
  the `streaks` flag is on — the day's streak. A celebration plays once per flow, never on a
  revisit. *Start another flow* returns to setup.
- **State lives in memory for the app's process only** — the iOS analogue of the web's
  `sessionStorage` choice: a flow never resumes itself after the app is terminated. Whether a flow
  survives a short backgrounding (the app returning without being terminated) is the TDD's call,
  stated against the Flow TDD's D5.
- Nothing about a flow is sent to the server beyond the ordinary lesson reads, Attempts and Mark
  completes.

### 5.13 Push notifications (Slice 8)

The one capability the web does not have and parity does not demand of it: push is native-only.
The preferences live on the server, so the web can show them later without new API.

**Use cases (D6) — and nothing else is ever pushed:**

- **(A) "Your path is ready."** Sent when a path's outline generation finishes (ready, refused or
  failed — each with its own plain wording), **event-driven from the backend** at the moment the
  outline settles; no scheduler involved. Tapping opens the path view. Not sent for lesson
  generation (the poll covers a learner who is looking).
- **(B) Reminders — opt-in, at most once per day, at a learner-chosen local hour.**
  - a **daily learning reminder** ("Pick up <path title> where you left off"), and
  - a **streak reminder** ("Keep your 5-day streak going"), only when the learner has a Daily
    streak to keep.
  Both are **off until the learner turns them on**; the learner picks one local hour for them. At
  most **one** reminder notification per learner per local day, whichever kinds are on — when both
  apply, the streak wording wins. **No reminder is sent on a day the learner has already completed
  a lesson** (an Active day). Tapping opens the resume target (or Your paths). This **supersedes**
  the streaks PRD's "never a push" for opt-in reminders only; e-mail, freezes, leaderboards and
  in-app warnings stay out ([Phase 5 streaks PRD](phase-5-streaks.md)).
- **(C) "Brief published."** Sent when a Beat publishes a Brief — normally from the scheduled job
  (§5.14) — naming the Beat and Brief number. Never for a Skipped period. Tapping opens the Brief.

**Client requirements**

- **The permission prompt is asked in context, never at launch:** when the learner first starts a
  path generating (for A), turns on a reminder (for B), or deploys an analyst (for C), with a
  one-line pre-prompt saying what will be sent. A declined prompt is respected: the app shows the
  state in settings with a link to the system Settings, and never re-prompts.
- **Notification settings** in the account menu: the two reminder toggles, the reminder hour, and
  per-kind toggles for (A) and (C). Changes are written to the server.
- The app registers its APNs device token after permission is granted, re-registers when the token
  changes, and **unregisters on sign-out**; the learner's IANA **time zone** is sent with the
  reminder preference and refreshed on foreground when it changes.
- A notification carries an **id to open, not a URL**; the app routes it through the same screens
  and the same API reads, so another learner's id simply `404`s. Payload text is plain (no
  Markdown) and carries no Read passage, Brief body or tutor content.
- A notification for a screen the learner is already on is suppressed in the foreground.

**Backend requirements (new, additive)**

1. **Device registration** — routes to register and remove an APNs device token for the
   authenticated Account (bearer only; one Account may have several devices). Tokens APNs reports
   as invalid are pruned.
2. **Reminder and notification preferences** — read and write the per-kind toggles, the reminder
   local hour and the learner's IANA time zone. This is the first **stored time zone** in the
   product (the streaks TDD's D3 kept it out); the TDD records the change.
3. **An APNs sender** behind a `Protocol` with an in-memory fake (fakes over mocks), using an APNs
   auth key (`.p8`) stored as a Fly secret. A send failure is logged and never fails the request
   or job that triggered it.
4. **(A) hooks into outline settlement**, (B) is sent by the scheduled job (§5.14), (C) is sent
   wherever a Brief is published.
5. Each push send emits a product event (kind, outcome) so §7 can see whether notifications bring
   learners back.

### 5.14 The scheduled job (Slice 8, backend)

**A requirement for the TDD and the implementation; not built by this PRD.**

- **One hourly Fly scheduled Machine** — `fly machine run --schedule hourly` — running **one job
  entry point** (e.g. `python -m aleph.jobs.tick`) from the app image. `fly.toml` cannot declare
  schedules (verified in the flyctl source), so:
  - schedules are declared in a **committed file** (e.g. `deploy/schedules.toml`): name, schedule,
    command;
  - on **every deploy** the release workflow **deletes and recreates all scheduled machines** from
    that file with the just-deployed image, `--restart no`, and identifying metadata (a tag) so the
    workflow finds and deletes exactly its own machines and nothing else;
  - the lifecycle, the file's format and how to run the job by hand are documented in
    [`deploy.md`](../deploy.md).
- **Each run:**
  1. **Reminders.** Sends every opted-in reminder whose learner-local hour has arrived and that has
     not yet been sent for that learner's local day (§5.13 B). A run that is late or skipped
     catches up on the next run within the same local day; it never sends yesterday's reminder.
  2. **Beats and Briefs, once per day.** Runs Beat research and Brief generation **once per day**,
     guarded by a durable "already ran today" record, so the hourly cadence never multiplies model
     spend. This **moves Brief generation out of request-triggered draining** (the arrival drain on
     `GET /beats` and `GET /beats/{id}`) **into the job**, so Briefs publish — and push (§5.13 C) —
     while the learner is away. `POST /beats` keeps its immediate first run (Phase 6 PRD §3:
     "researched immediately"), and `POST /beats/{id}/retry` is unchanged.
- **Idempotent and interruption-safe.** Every step can be killed mid-run and re-run without a
  duplicate reminder, a duplicate Brief or a double-billed research claim: sends are recorded
  before or atomically with delivery, Beat claims stay the existing atomic `UPDATE`, and the "ran
  today" guard is written so a crashed run is retried by the next one.
- **Cost, stated:** Fly bills the job machine **per second of run time** only; **Neon wakes
  hourly** (roughly 5 minutes of compute per wake before it suspends again), which is the main
  standing cost and is accepted; **model spend for Briefs is bounded** — no model work for a Beat
  that is not due under its Cadence floor, and no work for an **idle Beat** whose learner is not
  reading it (the TDD defines the test; the PRD's suggestion is "a Beat whose latest published
  Brief is still unread is not researched again"), replacing the bound the arrival drain gave for
  free. The existing daily research cap still applies.
- **What it changes elsewhere:** the Phase 6 PRD's §4.2 trigger model and its "no stored timezone"
  reasoning, and CONTEXT.md's **Anchor day** and **Brief prefetch** entries (§2.3). The job reads
  the learner's stored time zone (§5.13) to decide whether an Anchor day has opened; how a learner
  with no stored zone is treated is the TDD's.
- **Proven by backend tests**, not by the iOS suite: integration tests with a fake clock and the
  fake APNs sender for "sent once per local day", "late run catches up", "killed mid-run, re-run
  sends nothing twice", and "Beats ran once today".

## 6. System design (product view)

These are **settled** with the owner; the TDD owns their mechanics (and opens with the
best-practices brainstorm described at the top of this document).

- **Native SwiftUI, iOS 26 minimum** (D17). The owner, the first user, is on iOS 26; a current
  floor means current APIs and no availability branches. Observation (`@Observable`) for screen
  state and `NavigationStack` for navigation; **no third-party state-management library**.
- **Monorepo.** The Xcode project lives in **`clients/ios/`** in this repository (D16), so an API
  change and its client change are one PR, reviewed together, with both CIs green. A later step in
  the TDD's plan moves the web app from `src/aleph/web/frontend/` to **`clients/web/`**; that move
  must also update the production serving path — the **Dockerfile's frontend build stage** and the
  dist directory **`web/serve.py`** serves — and the TDD's plan owns it.
- **A generated API client.** `swift-openapi-generator` generates the Swift client and types from
  FastAPI's OpenAPI document. The document is exported to a committed file, and a check on the
  Linux `gate` job fails when the committed copy drifts from the app factory's — so a backend PR
  that changes a DTO shows its client-visible diff in review and cannot merge with a stale copy.
  The generated code sits behind small **protocols** the screens depend on (`PathsAPI`,
  `LessonsAPI`, `BeatsAPI`, `TutorStream`, …), which is what makes the in-memory fakes of §9.1
  cheap.
- **The SSE stream is hand-parsed.** FastAPI's OpenAPI describes a `text/event-stream` response
  only loosely, so the two send routes are called through `URLSession.bytes(for:)` and a small,
  fully unit-tested frame parser over the documented events plus the `: ping` comment.
- **Polling is structured concurrency.** Each poll loop is a `.task` on its screen: cancellation on
  disappear is the language's, not a timer the app must remember to invalidate.
- **An `AuthSession` protocol** owns sign-in and tokens (D15): the real one wraps **AppAuth-iOS**
  (ephemeral `ASWebAuthenticationSession`, PKCE, refresh) and the Keychain; the fake hands out a
  fixed token. The API layer asks it for a token per request and reports `401`s back to it.
  AppAuth-iOS is the one third-party dependency in security code, chosen over a hand-rolled flow
  because it is the standard, audited OIDC client.
- **Configuration** — API base URL, OIDC issuer, client id, audience, redirect URI — comes from
  build configurations (*Debug → local backend + Keycloak*, *Release → production + Auth0*). Any
  App Transport Security exception for plain-HTTP localhost is **Debug-only**.
- **Data model:** none of its own. The app persists only tokens (Keychain) and, at most, small UI
  preferences. There is no on-device database in v1. Notification preferences live on the server.

## 7. Success metrics

The app does not get its own north star. **Activation rate** stays the north star; the app's job
is to make returning easier, so its primary metric is about return, stated so it can fail.

**Primary — Return through the app.**
> Among accounts that use both clients, the share of **Active days** on which the app was used,
> and the **Return** rate (a second distinct Day) for learners whose first path was started in the
> app versus on the web.

If the app does not raise Return for the learners who have it, it is a second rendering of the
same loop and the later slices should be re-argued rather than built.

**The `client` dimension (D5).** Product events gain a **server-derived** `client` attribute,
taken from how the request authenticated, never from anything the client claims: a
**cookie-authenticated** request is `web`; a **bearer-authenticated** request is keyed on the
token's client id (`azp`, or `client_id` where a provider uses that claim), with the iOS client id
(the Auth0 Native application's, and `aleph-ios` in Keycloak) mapped to **`ios`** by
configuration; an unmapped client id records as `unknown` rather than guessing. Events emitted by
the scheduled job carry no client. This is a small backend follow-up to PR #218, specified in the
TDD and recorded in `metrics.md`.

**Supporting metrics**

- **App adoption:** % of active accounts with at least one `ios` event in a week.
- **First-lesson time** on a cold launch with a valid token: launch → Read passage visible, p50/p95
  (measured with `XCTMetric`/os_signpost in the UI suite, not with an SDK).
- **Slice uptake:** tutor messages, shaping Applies, streak-line views, Briefs read and Flows
  started from the app, once each slice ships (the same events the web already emits).
- **Push:** opt-in rate for reminders, and the share of reminder and "path ready" pushes followed
  by a lesson completion that local day.

**Guardrails**

- **≥ 99.5% of app launches crash-free** on TestFlight builds, from App Store Connect / Xcode
  Organizer crash reports — no third-party crash SDK. (Apple calls this "crash-free sessions";
  that is not a **Session** in the CONTEXT.md sense.)
- **No metric drift between clients.** `lesson_viewed` volume per completed lesson from the app
  stays within the web's band; a large excess means a poll loop is not cancelling.
- **Quick-check correctness** for app users stays in its Phase 1 band — a sharp rise suggests an
  answer leak and is investigated, not celebrated.
- **Sign-in failure rate** (provider errors and failed refreshes reaching the launch screen) —
  watched from structlog's `bearer_auth_rejected` events.
- **Push restraint:** never more than one reminder per learner per local day, and reminder opt-outs
  watched — a rising opt-out rate means the wording or timing is wrong.
- **Brief spend** stays within the analyst's existing per-learner budget after the job takes over
  research (§5.14).

## 8. Core workflows (UI tests)

The XCUITest suite **mirrors the Playwright journeys**, tagged with the **same W-numbers**: a
workflow is one product behaviour, proven on each client that offers it, not a new workflow per
client. Like Playwright, it runs on a phone (the CI simulator) against the **full stub-model
backend** (§9.3). Which slice brings each:

| Slice | Mirrored workflows |
| --- | --- |
| 1 | **W2** progress persists across sign-out/relaunch · **W3** reach four completions on one path · **W4** multiple paths keep independent progress (paths seeded by the fixture) · **W6** Quick-check Outcome, both branches, and no answer before an Attempt · **W8** lesson generation failure is recoverable (the lesson half) |
| 2 | **W1** first path, end to end · **W5** delete a path · **W7** an unsafe topic is refused gracefully · **W8** (the outline half) |
| 3 | **W9**, **W11**–**W16** — the tutor |
| 4 | **W17**–**W21** — shaping |
| 5 | **W22**, **W23** — streaks |
| 6 | **W29** deploy an analyst, get a cited Brief · **W31** a quiet period is Skipped, not padded (the two analyst workflows the web proves as Playwright journeys; W30, W32 and W33 stay backend integration tests) |
| 7 | The Flow journey — tagged **W32** in the Flow TDD and its Playwright spec. That number collides with the analyst's W32 integration test (`tests/integration/test_briefing.py`); the TDD resolves the collision (renumbering one of them) before the iOS test is tagged. |
| 8 | Push workflows (below). |

**New, iOS-only workflows.** W34 is the next free number at the time of writing (W1–W33 are taken;
confirm against `tests/` when the TDD lands, the Phase 6 TDD D16 precedent).

- **W34 — Sign in on a phone and see the web's paths.** With a path created through the API for an
  identity, sign in to the app as that identity → the path is on Your paths with its progress →
  relaunch → still signed in, no prompt.
- **W35 — An expired token recovers without losing the learner's place.** With the access token
  expired, open a lesson → the app refreshes once and renders the lesson; with the refresh token
  also revoked → the app returns to the launch screen cleanly, and after signing in again the same
  lesson state is there.
- **W36 — A hostile Read passage renders inert.** A lesson whose passage (from the stub model)
  contains raw HTML, a `javascript:` link and an image reference → the HTML shows as text or not at
  all, the link is not tappable, no image is fetched, and the Quick check still works.
- **W37 — "Your path is ready" brings the learner back.** Start a path, leave the app → the
  backend's fake APNs sender records one push for that path → delivering it to the simulator
  (`xcrun simctl push`) and tapping it opens that path's view.
- **W38 — A reminder is opt-in and arrives once.** Turn on the daily reminder at a chosen hour →
  the preference is stored with the device's time zone → a job run at that local hour records one
  reminder; a second run the same day records none; a day with a completion records none. (The
  scheduling half is a backend integration test; the iOS half proves the settings and tap-through.)
- **W39 — A published Brief is pushed and opens.** A Beat's Brief published by a job run → one
  push recorded, none for a Skipped period → tapping it opens the Brief reader.

## 9. Testing & CI

### 9.1 Unit tests — fakes over mocks

The repository's rule applies unchanged: **protocol-backed in-memory fakes, not mocks.** Every
screen model depends on the §6 protocols, so a test builds an `InMemoryPathsAPI` with the state it
needs (a path mid-generation, a failed lesson head, a `409 not_latest`, a Beat with a Skipped
entry) and asserts what the screen model exposes. A test that needs a mocking framework is a sign
the protocol is wrong. Pure logic — the SSE frame parser, the poll back-off and stall rule, the
`tz_offset_minutes` conversion, the Anchor-day weekday mapping, the Markdown link-scheme filter,
the resume-target choice, Flow's order rules, notification routing — is tested directly with no
fakes at all.

### 9.2 Snapshot tests

**swift-snapshot-testing** captures **every screen in every state** §5 defines (loading, empty,
generating, failed, refused, locked, the pre- and post-Attempt Quick check, path complete, a Beat
researching / failed / refused / with Skipped entries, a Brief with Sources, the flow bar and
receipt, and so on), each in **dark** and at an **accessibility (Dynamic Type) text size** — there
is no light variant (D13) — on one pinned simulator device and iOS version. Screens are rendered
from fakes, so snapshots are deterministic and need no network.

**The reference PNGs are committed** under `clients/ios/` and are the visual record of the app.
This is deliberate: it is how an agent with no Mac sees what it built, and a reviewer sees UI
changes as image diffs in the PR. **How references change (D11):**

- **On a pull request**, snapshots **compare against the committed references** and fail on any
  difference; the failing, expected and diff images are uploaded as a workflow artifact. CI never
  commits to a PR branch.
- **On an `ios-dev/*` branch** (§9.5), CI **records** the references and **commits them back** to
  that branch, so an agent can pull and read them. This is the only place CI writes to a branch.

### 9.3 UI tests — against the full backend

**XCUITest** runs the §8 workflows **against the full stack** on every qualifying PR (D10): the
stub-model app (`scripts/e2e_backend.py`), **Postgres** and **Keycloak**, installed **natively on
the macOS runner** because GitHub's macOS runners have no Docker. Fixtures create paths and Beats
through the API using a token minted from the dev realm's `aleph-api-test` client. Fakes are for
unit and snapshot tests only; there is no hermetic UI-test layer, because a UI test that passes
against fakes proves nothing about the contract the app actually depends on.

W34's interactive sign-in drives the system `ASWebAuthenticationSession` sheet, which is the
flakiest thing to automate; if it cannot be made reliable it becomes a scripted manual check in
the TestFlight checklist (§10.2) and the token-handling half is covered by unit tests. Push
delivery in W37–W39 is simulated with `xcrun simctl push` from what the fake APNs sender recorded;
real APNs delivery is a TestFlight checklist item.

### 9.4 CI

- A separate **`ios` workflow** on a GitHub **macOS** runner: build, unit tests, snapshot tests and
  full-stack UI tests, as **required checks** on PRs. Runs on the same ref cancel their
  predecessors, as `ci.yml` does.
- **A home-written path filter** gates it — no third-party action (such as `dorny/paths-filter`).
  A first, cheap job diffs the PR against its base and outputs whether any of these changed:
  `clients/ios/**`, the committed OpenAPI document, `scripts/e2e_backend.py`, or the iOS workflow
  file. The macOS jobs run only when it says so. **A skipped job counts as passing**, so the
  required checks never hang on a backend-only PR (the TDD picks the mechanism — e.g. jobs that
  always run and exit early, or a final aggregating job that is the required check).
  The reason: macOS minutes cost several times Linux minutes (10× on private repositories at
  current GitHub pricing), and the OpenAPI drift check already runs on Linux.
- **No nightly run on `main`** for now; add one if bugs slip through the path filter.
- Xcode (26.x) and simulator versions are pinned in the workflow, not inherited from the runner
  image, so an image update does not silently change every snapshot.
- The four existing jobs (gate / integration / e2e / compose-smoke) are unchanged except for the
  drift check added to `gate`.

### 9.5 Agent iteration branches (`ios-dev/*`)

An agent that cannot build iOS locally (a Linux container, no Xcode) pushes Swift changes to an
**`ios-dev/<topic>`** branch. There, the `ios` workflow builds, runs the tests, **records
snapshots and commits the PNGs back** to the branch (§9.2), so the agent pulls and reads what it
built. The loop is *push Swift → CI renders → PNGs land on the branch → read them*. When the work
is ready it goes to review as an ordinary PR, where snapshots only compare.

## 10. Distribution

### 10.1 Accounts and secrets

- An **Apple Developer Program** membership (annual fee), an App ID and bundle identifier with the
  **Push Notifications** capability, and an App Store Connect app record.
- **fastlane** drives build and upload, authenticating with an **App Store Connect API key**
  (issuer id, key id, `.p8`) stored in GitHub secrets, plus whatever signing material the signing
  choice needs — **deferred to TestFlight setup** (D12). An **APNs auth key** (`.p8`, key id, team
  id) is a Fly secret for the backend's sender. Every new secret is documented in `deploy.md`
  beside the existing production secrets, with no value in the repository.

### 10.2 TestFlight

- **Internal** testing for the owner first: no Beta App Review, available minutes after upload.
- **External** testing by **public link** for friends once Slice 2 ships (§12) — before that, a
  friend could sign in but not make a path. External builds go through **Beta App Review**
  (typically the first build of each version), which needs a beta description, a feedback contact,
  and sign-in instructions or a demo account for the reviewer. If Beta App Review demands an item
  from §10.3 (for example a privacy policy URL), that one item is pulled forward for that reason
  and no other. The export-compliance answer (HTTPS only — the standard exemption) is set in the
  build so uploads are not held.
- **Uploads (D7).** A TestFlight upload runs **automatically on every merge to `main` that touches
  iOS** (the same path filter as §9.4), on a **monthly schedule** so a current build always exists
  before the **90-day build expiry**, and by **manual `workflow_dispatch`**. The **build number is
  the CI run number**; the marketing version is set in the project and bumped by hand (TDD).
- **iOS-only commits never cut a backend release.** semantic-release versions and deploys the
  backend from commit types, so until `.releaserc.json` excludes an `ios` scope, iOS-only work is
  committed with non-releasing types (`chore(ios):`, `ci(ios):`, `test(ios):`, `docs(ios):`) as
  CLAUDE.md states.
- A release checklist covers what automation cannot: a real-device sign-in against Auth0, a real
  APNs push of each kind, and a pass through the Slice's workflows.

### 10.3 App Store submission (deferred)

**Standing rule:** anything needed only for App Store submission is out of scope until the owner
decides to submit. These are recorded so the earlier slices do not paint the app into a corner —
they are **not v1 requirements**.

- **Sign in with Apple** (Guideline 4.8) — added at App Store time (D1). Adding an Apple
  connection in Auth0 gives a learner who signs in with Apple a *different* `(issuer, subject)`
  from their web login, i.e. a second, empty Account; account linking in Auth0 (or equivalent) is
  part of that work.
- **In-app account deletion** (Guideline 5.1.1(v)) — the API has no account-deletion route today;
  it is specified for both clients at once at App Store time (D4).
- **A privacy policy URL** and the App Privacy "nutrition label" (identifiers and account data
  collected; no tracking).
- **AI-generated content review prep.** Reviewers may probe the refusal boundary through topic
  entry, the tutor and Beats. The existing boundary (Phase 1 §10) and its graceful refusals are the
  answer; the review notes say plainly that content is AI-generated; the age-rating questionnaire
  is answered honestly for an open-ended generator and chat; and a *report this* affordance may be
  asked for (there is none today — Phase 2 §5.7b deliberately has no flag UI).
- App Store listing material (screenshots, description, support URL) and the App Review demo
  account.

## 11. Guardrails & safety

- **Generated content is untrusted** (§5.4.1): no raw HTML, filtered link schemes, no remote images,
  Mermaid as source. The rule applies to every surface showing model output — Read passages,
  explanations, tutor and shaping replies, Briefs and their Source links.
- **Answer-hiding is the server's, and the app must not undo it.** The app never caches a
  `correct_index` it obtained from anywhere but an Attempt response (or a later payload carrying
  the Attempt), never logs payloads, and never prefetches in a way that grades.
- **Tokens:** Keychain, device-only; never logged, never in a URL, never in a crash report; the
  binary contains no client secret. APNs device tokens are stored server-side per Account and
  deleted on sign-out.
- **Push carries no content and no authority.** A notification holds an id and plain wording; the
  app opens it through the normal authenticated reads, so a forged or stale id is a `404`.
- **Push restraint.** Only the three use cases in §5.13; reminders opt-in, at most once per local
  day, never on a day already studied.
- **Kill switches reach the app.** Flags are re-read on foreground (§5.9); a flagged route's `404`
  degrades the surface quietly.
- **No new data leaves the device** beyond the push token and the reminder time zone. No analytics
  SDK, no crash SDK beyond Apple's own.
- **The refusal boundary is unchanged.** The app adds no new generation input; it sends exactly the
  fields the web sends.

## 12. Phasing

Slices are numbered on their own to avoid colliding with the roadmap's phases. Each ends with its
§8 workflows green on CI and a TestFlight build. **v1 is Slices 1–8.**

| Slice | Contents | Exit |
| --- | --- | --- |
| **0 — PoC** *(another branch)* | A trivial home screen in `clients/ios/`, built on a macOS runner, with one committed snapshot PNG an agent can read (the `ios-dev/*` loop, §9.5). | The CI build + screenshot loop works end to end. Nothing user-facing. |
| **1 — Read and check (MVP)** | Bearer auth (✅ PR #218) configured for Auth0's Native app; the generated client and drift check; the path-filtered `ios` workflow with native Postgres + Keycloak; §5.1 sign-in/out; §5.2 Your paths (read-only, Continue); §5.3 path view (read-only); §5.4 lesson view with every state; the snapshot and full-stack UI suites; TestFlight **internal** with automatic uploads; the `client` event dimension (D5). | The owner does a day's lessons in the app; W2, W3, W4, W6, W8 (lesson), W34, W35, W36 pass. |
| **2 — Make paths** | §5.5 New path; delete, rename, Teaching style. TestFlight **external** public link opens. | W1, W5, W7, W8 (outline) pass; a friend makes and starts a path unaided. |
| **3 — The tutor** | §5.6, including D8 backgrounding. | W9, W11–W16 pass. |
| **4 — Shape your path** | §5.7. | W17–W21 pass. |
| **5 — Streaks** | §5.8. Small — one read and three views — and could move earlier without disturbing anything. | W22, W23 pass. |
| **6 — Beats & Briefs** | §5.10, §5.11; the path-complete *Follow it as a Beat* door. | W29, W31 pass. |
| **7 — Flow** | §5.12, behind the `flow` flag exactly as the web is. | The Flow journey (W32 or its renumbering, §8) passes. |
| **8 — Push & the scheduled job** | Backend: device registration, preferences, the APNs sender, the hourly job and its deploy lifecycle, Brief generation moved into the job (§5.13, §5.14). App: permission prompts, notification settings, routing. Shipped in order **A → B → C**. | W37, W38, W39 pass; the job's idempotency tests pass; one real push of each kind on a TestFlight device. |
| **Later** | Sandboxed Mermaid; Universal Links; App Store submission (§10.3). | Sequenced against usage. |

## 13. Decisions, risks and remaining open items

### 13.1 Decisions (owner-approved)

D1–D15 answer the fifteen open questions of the earlier draft, in order; D16–D20 were settled
alongside them. **Standing rule:** anything needed only for App Store submission is deferred and
listed in §10.3, not in requirements.

| # | Question | Decision | Rationale |
| --- | --- | --- | --- |
| **D1** | Which Auth0 connections are enabled? | Keep **whatever logins production has today**; add **Sign in with Apple only at App Store time** (§10.3). | Guideline 4.8 binds only App Store submission; TestFlight does not need it, and adding it now would create the second-Account problem early. |
| **D2** | How does each provider mint an API access token? | **As implemented in PR #218:** the app requests Auth0 `audience` = the Auth0 API Identifier (`OIDC_API_AUDIENCE`); Keycloak's `aleph-ios` public client has an audience mapper (`aud=aleph-api`); identity comes from **userinfo**. §5.1's backend requirements are delivered. | Already built, tested and documented; the app conforms to it. |
| **D3** | Ephemeral or shared browser session? | **Ephemeral** (`prefersEphemeralWebBrowserSession`). | No system prompt, no Safari cookie, and sign-out needs no provider logout. |
| **D4** | Account deletion? | **Deferred to App Store time** (standing rule), specified for both clients at once. | Required by App Review, not by TestFlight. |
| **D5** | A `client` dimension on product events? | **Yes, server-derived:** cookie → `web`; bearer → keyed on the token's client id (`azp`/`client_id`), iOS client id → `ios`. Small backend follow-up after #218. | Without it the primary metric (§7) cannot be computed; deriving it server-side means a client cannot misreport it. |
| **D6** | What is push for? | **In v1, its own slice (8):** (A) "your path is ready" when outline generation finishes, event-driven; (B) daily learning reminders **and** streak reminders, opt-in, at most once per day, at a learner-chosen local hour; (C) "Brief published". (B) supersedes the streaks PRD's "never push about streaks" for opt-in reminders. | These are the returns a phone app can create that a tab cannot; opt-in and once-a-day keep the streaks PRD's restraint in spirit. |
| **D7** | How is the iOS app versioned and released? | **Automatic TestFlight upload on every merge to `main` touching iOS**, plus a **monthly scheduled re-upload** (beats the 90-day expiry) and **manual `workflow_dispatch`**; **build number = CI run number**; iOS-only commits use non-releasing types until `.releaserc.json` excludes an `ios` scope. | A current build always exists, and the backend's semantic-release never deploys because of an iOS change. |
| **D8** | Should a streaming reply survive backgrounding? | Request **`beginBackgroundTask`** time; if the stream is still lost, keep the message **in the composer for one-tap resend**. | Most replies finish in the granted time; when one doesn't, nothing is lost but a tap. |
| **D9** | Should Your paths acknowledge Beats? | **Dropped — moot.** Beats are in v1 (§5.10). | — |
| **D10** | Where does the full-stack XCUITest stack run? | **On PRs, as required checks, against the full backend** (`scripts/e2e_backend.py` + Postgres + Keycloak installed natively on the macOS runner); gated by a **home-written path filter** (`clients/ios/**`, the OpenAPI document, `scripts/e2e_backend.py`, the iOS workflow); skipped jobs count as passing; **no nightly** on `main` for now. Fakes for unit and snapshot tests only. `ios-dev/*` branches for agents without Xcode. | Contract bugs are caught before merge, macOS minutes are spent only when iOS can be affected, and no third-party action is trusted with CI gating. |
| **D11** | How do recorded snapshots get back to the branch? | **CI commits recorded references only on `ios-dev/*`**; on PRs, snapshots compare against the committed references. | Agents get their PNGs without a Mac; PR branches are never rewritten by a bot. |
| **D12** | Code signing: `match` or cloud-managed? | **Deferred** — decided during TestFlight setup. | It is a setup detail with no product consequence; deciding with the Apple account in hand is cheaper. |
| **D13** | Is light mode real? | **Dark only for v1**, matching the web; **no light snapshots**; Dynamic Type (large-text) variants kept. | Nocturne is a dark system; a light theme is design work v1 does not need. |
| **D14** | Redirect URI form? | **Custom scheme `aleph://oauth/callback`**. | Already registered on the Keycloak `aleph-ios` client; simple, and needs no associated-domains setup. |
| **D15** | Auth library or hand-rolled? | **AppAuth-iOS behind an app-owned `AuthSession` protocol** (fakeable). | Security code from a standard, audited library; the protocol keeps it swappable and tests fake-backed. |
| **D16** | Where does the app live? | **`clients/ios/`**, not `ios/`. A later TDD step moves the web app to **`clients/web/`**, handling the production serving path (Dockerfile frontend stage, `serve.py` dist path). | One `clients/` home for both frontends. |
| **D17** | Minimum iOS version? | **iOS 26.** | The owner is the first user and is on iOS 26; a current floor removes availability branches. |
| **D18** | How do reminders and away-time Briefs run? | **An hourly Fly scheduled Machine** running one job entry point; reminders each run; Beats/Briefs once per day; schedules in a committed file, recreated by the release workflow on every deploy (§5.14). | Push needs work to happen while nobody is visiting; `fly.toml` cannot declare schedules, so the workflow owns them. |
| **D19** | v1 scope? | **Full feature parity with the web**, including Beats, Briefs and Flow (CLAUDE.md, *Two Clients*); admin surfaces stay web-only. | Parity is the repository's standing rule; a partial app would leave learners going back to the web. |
| **D20** | How does the TDD start? | With an **upfront iOS best-practices brainstorm** (code and app), explained for an owner new to iOS, before design decisions. | The owner is new to iOS; decisions should be made against stated practice, not assumed. |

### 13.2 Risks

- **Full-stack UI tests on macOS** depend on native Postgres and Keycloak installs on a runner
  image Apple and GitHub update often; a broken install blocks iOS PRs. Mitigation: pin versions,
  cache the installs, and keep the path filter tight so backend-only PRs are never blocked.
- **macOS CI cost** (§9.4). Mitigation: the path filter, cancellation, and no nightly run.
- **OpenAPI fidelity.** Pydantic v2's `anyOf`-with-`null` and discriminated unions (a Beat's two
  entry shapes, Proposal operations) can generate awkward Swift. Mitigation: wrap generated types
  behind the §6 protocols and app-owned models, so an awkward shape is fixed in one adapter.
- **Snapshot churn.** Two variants × every state × every screen is a large and growing set of PNGs
  in git. Mitigation: pinned device and toolchain, and a periodic look at repository size (Git LFS
  if it bites).
- **Two clients drift in behaviour, not data.** A rule implemented client-side on the web (the
  resume target, the stall timeouts, the streak line's copy, path-complete Day arithmetic, Flow's
  order rules) can diverge in Swift. Mitigation: each is named in §5 with its web source,
  unit-tested on both sides, and changed in the same PR (CLAUDE.md, *Two Clients*).
- **The scheduled job is new infrastructure.** Scheduled machines that are not recreated on deploy
  run a stale image; a job that is not idempotent double-sends or double-bills. Mitigation: the
  delete-and-recreate rule, tagged metadata, and the idempotency tests in §5.14.
- **Brief spend without arrival as a bound.** Research that no longer waits for a visit can run for
  Beats nobody reads. Mitigation: the idle-Beat rule and the existing research cap (§5.14).
- **Neon wakes hourly**, ending scale-to-zero for most of the day's hours. Accepted; watched on the
  Neon bill.
- **Push fatigue.** Reminders that feel like nagging drive opt-outs and uninstalls. Mitigation:
  opt-in only, once a day, never on a studied day, and the opt-out guardrail (§7).
- **TestFlight expiry** strands friends if uploads stop. Mitigation: the monthly scheduled upload
  (D7).
- **Anchor-day precision with a once-daily Beats pass.** A single daily pass at a fixed UTC time
  can land before a learner's local Anchor day has opened, delaying that Brief by up to a day. The
  TDD either accepts that (Cadence is a floor, not an appointment) or applies the "already ran
  today" guard per Beat per local day.

### 13.3 Remaining open items (for the TDD)

1. **Code signing** (D12) — `match` versus cloud-managed signing, at TestFlight setup.
2. **The idle-Beat test** (§5.14) — the PRD suggests "latest published Brief still unread"; the
   TDD confirms or replaces it, and states how a learner with no stored time zone is treated.
3. **Flow and backgrounding** (§5.12) — whether a flow survives a short backgrounding.
4. **The Flow W32 collision** (§8) — renumber before tagging.
5. **Required-check mechanics** for path-filtered jobs (§9.4).

## 14. Release criteria (v1 = Slices 1–8)

The app's v1 is shipped when:

- [ ] Every §8 workflow for Slices 1–8 passes on CI against the full stub-model backend, and
      W34/W35 pass against Auth0 at least in the release checklist.
- [ ] A learner can sign in with their web account and see exactly the paths, progress and Beats
      the web shows — and a completion or Brief read in either client shows in the other on its
      next read.
- [ ] The Quick check answer is never present in the app before an Attempt (W6), and no screen
      grades on the client.
- [ ] A hostile Read passage renders inert (W36); no surface renders model output through anything
      but the one Markdown renderer.
- [ ] Every screen state in §5 has a snapshot in dark and at an accessibility text size, committed
      and green.
- [ ] Leaving any generating screen stops its polling, verified by a unit test per poll loop and by
      the `lesson_viewed` guardrail on TestFlight usage.
- [ ] A flag turned off on the server removes its surface from the app on next foreground.
- [ ] Product events carry the server-derived `client` dimension (D5).
- [ ] Each push kind is opt-in where §5.13 says so, sent at most as often as it says, and opens the
      right screen; the scheduled job is idempotent under interruption and recreated on every
      deploy from the committed schedules file, with its runbook in `deploy.md`.
- [ ] A TestFlight build is available to internal and external testers, uploaded by CI from secrets
      on merge, monthly and on dispatch, with the runbook in `deploy.md`.
- [ ] The docs in §2.3 are updated in the same PRs as the code they describe.

---

### Appendix — traceability to the web app

| Web route / component | API | iOS screen | Slice |
| --- | --- | --- | --- |
| `/login`, `lib/auth.ts` | OIDC (bearer, PR #218) + `GET /auth/session` | Launch / sign-in (§5.1) | 1 |
| `/` — Your paths, Continue card | `GET /paths` | Your paths (§5.2) | 1 |
| `/` — New menu, delete confirm | `POST /paths`, `DELETE /paths/{id}` | Your paths (§5.2) | 2 |
| `/` — streak line, activity strip, chips | `GET /progress/summary` | Your paths (§5.8) | 5 |
| `/` — Beats section (`beat-card.tsx`) | `GET /beats` | Your paths (§5.10) | 6 |
| `/` — Flow door (`flow/flow-door.tsx`) | none (Flow is client-only) | Your paths (§5.12) | 7 |
| `/paths/{id}` — path rail, states | `GET /paths/{id}`, `POST …/retry` | Path view (§5.3) | 1 |
| `/paths/{id}` — title, Teaching style | `PATCH /paths/{id}`, `PUT …/teaching-style` | Path view (§5.3) | 2 |
| `/paths/{id}` — shaping rail, ghost rows | `/shaping/…`, `apply-proposal`, `/changes` | Shaping sheet (§5.7) | 4 |
| `/paths/{id}` — *Start a flow* link | none | Path view (§5.3, §5.12) | 7 |
| `/lessons/{id}` — Read, Quick check, complete | `GET /lessons/{id}`, `…/attempt`, `…/complete`, `…/generate` | Lesson view (§5.4) | 1 |
| `components/path-complete.tsx` — doors | — | Lesson view (§5.4) | 1 (Beat door: 6) |
| `components/markdown.tsx`, `mermaid.tsx` | — | Markdown renderer (§5.4.1) | 1 |
| `components/tutor/` — the Rail | `/conversation…`, `tutor-check-answer` | Tutor sheet (§5.6) | 3 |
| `/new` — onboarding | `POST /paths`, `GET /paths/{id}` | New path (§5.5) | 2 |
| `/beats/new` | `POST /beats` | Deploy analyst (§5.10) | 6 |
| `/beats/{id}` — standing orders, research state, rail (`beat-rail.tsx`, `skipped-row.tsx`) | `GET /beats/{id}`, `POST …/retry` (`DELETE /beats/{id}` has no web control yet) | Beat page (§5.10) | 6 |
| `/briefs/{id}` — body, Builds on, Sources (`builds-on-line.tsx`, `brief-sources.tsx`) | `GET /briefs/{id}`, `POST …/read` | Brief reader (§5.11) | 6 |
| `/flow/new`, flow bar, advance card, `/flow/done` | none | Flow (§5.12) | 7 |
| — (no web equivalent) | device registration, notification preferences (new) | Notification settings, push routing (§5.13) | 8 |
