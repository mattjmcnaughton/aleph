# PRD — The iOS app (native SwiftUI client)

**Status:** Draft · **Owner:** solo builder · **Roadmap item:** none yet — this PRD proposes one (§2.3)
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [`api.md`](../api.md) · [`architecture.md`](../architecture.md) · [`ci.md`](../ci.md) · [`deploy.md`](../deploy.md) · [`metrics.md`](../metrics.md) · [Phase 1 PRD](phase-1-path-generation.md) · [Phase 2 PRD](phase-2-tutor.md) · [Phase 2B PRD](phase-2b-shape-your-path.md) · [Phase 5 streaks PRD](phase-5-streaks.md) · [Phase 6 PRD](phase-6-analyst.md) · [Flow TDD](../tdds/flow.md) · mocks: [MVP mobile](../mocks/aleph-mvp-mobile.html), [phase-2 tutor](../mocks/aleph-phase-2-tutor.html), [path complete](../mocks/aleph-path-complete.html)

> **This document owns the product boundary, plus the client decisions already settled with the
> owner.** Those decisions (§6) are recorded here because they shape scope and phasing; their
> mechanics — module layout, the generated client's configuration, the SSE parser, the polling
> scheduler, the Keychain schema, the fastlane lanes — belong to a companion **iOS TDD**
> (`docs/tdds/ios-app.md`, not yet written). Where this PRD names a mechanism it is because the
> product rule is unintelligible without it.
>
> **Two dependencies are being built on other branches** and are referenced, not specified:
> **bearer auth** on the backend (§5.1 states only the requirements it must meet) and the
> **home-screen PoC** that proves the CI build-and-screenshot loop (Slice 0, §12).
>
> Anything below that the author believes may be wrong in the settled decisions is raised in §13
> as an open question rather than silently changed.

## 1. Summary

Aleph is mobile-first by design, but today it is a web app in a phone browser. This PRD specifies
a **native iPhone app**, written in SwiftUI, that is a second client of the **same `/api/v1`** —
same Account, same paths, same progress, same tutor. A learner can start a path on the web, read
the next lesson on the train in the app, and come back to the web with nothing out of step,
because there is only ever one server and one source of truth.

The app ships in slices. The first slice is the core loop and nothing else: **sign in → Your
paths → open a lesson → read the Read passage → answer the Quick check → Mark complete.** Path
creation, the in-lesson tutor, shaping, and streaks follow, each a self-contained slice. Push
notifications, Beats/Briefs (the analyst) and Flow are out of v1.

**The core design claim: the API does not change.** The app reuses every `/api/v1` route as
documented. The single backend change is authentication — accepting
`Authorization: Bearer <OIDC access token>` beside the session cookie — because a native app
cannot sensibly ride a browser cookie. Everything else the app needs, the web app already needed:
trigger + poll for generation, an SSE stream for the tutor and shaping, answer-hiding on the Quick
check, untrusted Markdown at the edge.

The app lives in **`ios/`** in this repository, so an API change and its client change land in
the same PR, and it is distributed through **TestFlight** — internally to the owner, then by
public link to friends — before any App Store submission.

## 2. Context & goals

### 2.1 Why now

Every Phase 1 metric is a *return* metric: Activation is four lessons on one path inside seven
days, Return is a second distinct Day. A phone browser tab is the weakest possible place to
return to — no home-screen icon by default, sign-in that expires with the cookie, no native
navigation, and nothing to tap at 7am except a bookmark. A native app is a stronger door into the
same loop, and the loop itself is already built and launched: Phases 1, 2, 2B and the streaks
slice are all shipped, so the client has a complete, stable API to target.

It is also cheap in the way that matters. The backend has done the hard work: generation is
trigger + poll (so the client never holds a long request open), the tutor's stream is documented
event-by-event, answer-hiding is a property of the payload rather than the UI, and every route
already returns `404` rather than `403` for another learner's data. The app is a renderer and a
state machine over that contract.

### 2.2 Goals

- The owner can do a day's learning entirely in the app — read, check, complete, ask the tutor —
  with no reason to open the web app.
- **One Account, two clients, zero drift.** Anything done in one client is visible in the other on
  its next read. The app keeps no second copy of anything the server owns.
- The app is **at least as safe** as the web app with model output: generated Markdown is
  untrusted, the Quick check answer is never available before an Attempt, and a failed or refused
  generation has a defined state.
- The app is buildable, testable and screenshot-reviewable **from CI alone**, so an agent working
  in a Linux container (with no Xcode) can change Swift code, push, and see the result.
- Friends can install it from a TestFlight public link without the owner touching their devices.

**Non-goals (v1)**

- **No API redesign.** No mobile-specific endpoints, no response reshaping, no GraphQL, no BFF.
  If a screen seems to need a new route, that is a question for §13 first.
- **No offline mode.** The app reads and writes through the API; a dropped connection is an error
  state with a retry, not a sync queue.
- **No parity for parity's sake.** Beats/Briefs, Flow, the admin model picker and admin flag
  routes stay web-only in v1 (§4).
- **No iPad- or Mac-specific layout.** iPhone only; the app may run on iPad in compatibility mode,
  but no layout work targets it.
- **No third-party analytics, crash or state-management SDKs.**

### 2.3 What accepting this changes about the plan

The vocabulary and docs are authoritative, so the app lands with them, not after them. Accepting
this PRD includes:

- **[`roadmap.md`](../roadmap.md)** — a new section, *iOS app*, beside *Flow*, with this PRD as its
  spec and the §12 slices as its status line.
- **[`CONTEXT.md`](../CONTEXT.md)** — **no new domain terms.** The app introduces a client, not a
  concept: every screen here is named with an existing term. One clarifying line under *Design*:
  **Nocturne** also governs the iOS app, and the **Rail** on a phone is a SwiftUI sheet (one
  surface, now three presentations). If the TDD finds it needs a term, it adds it there first.
- **[`api.md`](../api.md)** — the bearer-auth paragraph (owned by the bearer branch). No route
  table changes.
- **[`ci.md`](../ci.md)** — the `ios` workflow (§9.4).
- **[`deploy.md`](../deploy.md)** — the TestFlight runbook and every new secret (§10).
- **[`development.md`](../development.md)** — Xcode setup, pointing the simulator at a local
  backend and Keycloak.

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
9. *As a friend of the owner,* I install from a TestFlight link, sign in, and make my first path
   without anyone walking me through it.

## 4. Scope

**In scope (v1 = Slices 1–5, §12)**

- Sign-in and sign-out via OIDC (PKCE, public client) — §5.1.
- **Your paths** — the Switcher as the home screen — §5.2.
- The **path view** with its path rail, read-only in Slice 1; rename, Teaching style and delete in
  Slice 2 — §5.3.
- The **lesson view**: Read passage, Quick check, Attempt and Outcome, Mark complete, every
  Generation state, path completion — §5.4.
- **New path** (onboarding) — §5.5.
- The in-lesson **tutor** (Rail as a sheet, Suggestions, Tutor check, streamed replies) — §5.6.
- **Shape your path** (shaping rail, Proposals, ghost rows, Apply, Undo, Change history) — §5.7.
- **Streaks** — Daily streak, Best streak, the activity strip, Path streak chips — §5.8.
- Honouring the learner's **feature flags** from the session probe on every gated surface — §5.9.
- Unit, snapshot and UI tests; a macOS CI workflow; TestFlight distribution — §9, §10.

**Out of scope for v1 — and where it goes**

| Deferred | What it is in the web app today | Goes to |
| --- | --- | --- |
| **Push notifications** | Nothing — the web app sends none. | **Slice 6** (§12). Needs new device-registration endpoints and an APNs sender — the first API addition this PRD would make — and a use case that does not contradict the streaks PRD's "never a push" (§13 Q6). |
| **Beats & Briefs** (the analyst) | A *Deploy analyst* entry in the home *New* menu (`/beats/new`: Topic, Level, the Anchor day it *Reports on*, optional Guidance); a Beats section on home beside Your paths with unread counts; a Beat's page (`/beats/{id}`) showing its standing orders, research state and a rail of dated entries — published Briefs and one-line **Skipped** periods; and the Brief reader (`/briefs/{id}`) — a Markdown body on the lesson reading surface, a *Builds on Brief #N* line, and its cited **Sources** listed at the foot. | A later iOS slice, sequenced against usage. Reuses §5.4's reading surface and Markdown renderer; the API (with its `tz_offset_minutes` on every call) is already sufficient. |
| **Flow** | Built but **dark** behind the `flow` flag: a *Flow* door on home opening `/flow/new`, where the learner picks a **Flow length** (3, 5, 8 or *until I stop*) and a **Flow scope** (which paths; Interleave, Random or one path at a time); then each Mark complete starts a 5-second advance into the next lesson under a flow bar, and the run ends on a receipt at `/flow/done`. 100% client-side — no route, nothing stored on the server. | After it launches on the web. Because Flow is client-only, an iOS Flow is a re-implementation of the web's state machine, not an API integration — it should copy a launched design, not a dark one. |
| **Admin surfaces** | The per-path model picker on New path (`model_outline`/`model_lesson`), the tutor's per-message `model` override, the feature-flag admin routes. | Web only, indefinitely. The app never sends a model override. |
| **Quote / selection to quote, path scope, Shaky badge** | Not built on the web either (CONTEXT.md phase boundaries). | With the web's later Q&A slice. |
| **Offline reading, widgets, Live Activities, Siri/App Intents, Universal Links** | — | Unscoped. Universal Links to `/lessons/{id}` are the most likely next ask. |
| **App Store release** | — | After TestFlight external has run long enough to answer §13's App Review questions (§10.3). |

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
  → a plain "daily limit reached, try tomorrow" with nothing to retry; anything else → a message and
  a retry that preserves what the learner typed or selected.
- **`tz_offset_minutes`** wherever a route takes it, as the web sends it: JavaScript's
  `getTimezoneOffset()` convention (minutes to *subtract* from UTC, so a zone ahead of UTC is
  negative). This is the **opposite sign** of Foundation's `secondsFromGMT()`; the conversion is
  one function, unit-tested at both signs and at a DST boundary.
- **Accessibility is a requirement, not a polish item.** Dynamic Type through the accessibility
  sizes without truncating a Read passage, a stem or an option; VoiceOver labels and traits on
  every control (a Quick check option announces *selected*, *correct*, *incorrect*); no meaning
  carried by colour alone (unlock and generation states have text or a symbol as well as a tint).
- **Nocturne.** The app extends the visual system — night background, porcelain text, teal for the
  path, iris for the tutor — using native controls rather than imitating web ones. No mock exists
  for iOS; the snapshot suite (§9.2) is the visual record, on the Phase 5 precedent of specifying
  against tokens directly.

### 5.1 Sign-in, the session, and the bearer requirement

**What the learner sees.** A launch screen with the Aleph mark and one *Sign in* button. Tapping
it opens the identity provider's page in an `ASWebAuthenticationSession` sheet; on success the app
lands on Your paths. On cancel the learner is back on the launch screen with no error; on failure
they see a short message and the button again. Relaunching the app never asks again while the
refresh token is valid. *Sign out* lives in a small account menu on Your paths (display name,
email, *Sign out*).

**Client requirements**

- The app is a **public OIDC client** — no client secret ships in the binary — using the
  authorization-code flow with **PKCE (S256)** and a `state` check, through
  `ASWebAuthenticationSession`. **Auth0** in production, **Keycloak** in development and CI, chosen
  by build configuration. The app discovers endpoints from the issuer's
  `.well-known/openid-configuration`, the same way the backend does.
- It requests `openid profile email offline_access` (plus whatever audience parameter the provider
  needs to mint an API access token — §13 Q2) and stores the access and refresh tokens in the
  **Keychain**, device-only (`…ThisDeviceOnly`), never in `UserDefaults` or on disk.
- Every API request carries `Authorization: Bearer <access token>`. A `401` triggers **one**
  refresh-and-retry; concurrent `401`s share a single refresh. A failed refresh clears the tokens
  and returns to the launch screen — never a loop, never a crash, and never a request sent without
  the header.
- **Sign out** deletes the Keychain items and in-memory state. It does not call `/auth/logout`
  (that route clears a browser cookie the app does not have). Whether it also ends the provider's
  browser session is §13 Q3.
- After sign-in the app calls **`GET /api/v1/auth/session`** and treats its `user` (display name,
  `is_admin`, `feature_flags`) as the signed-in state, exactly as the web's root `beforeLoad` does.

**Backend requirements (dependency — bearer auth, built on another branch).** The app needs
exactly these properties; the design is that branch's.

1. **Every `/api/v1` route that accepts the session cookie also accepts
   `Authorization: Bearer <access token>`**, including both SSE send routes and
   `GET /api/v1/auth/session`. Cookie behaviour for the web app is unchanged.
2. **Same human, same Account.** A bearer token resolves to the Account keyed by the same
   `(issuer, subject)` the cookie login provisions, so paths made on the web appear in the app and
   vice versa.
3. **First sign-in can happen in the app.** A valid bearer token for an identity with no Account
   provisions one exactly as a first web login would — including display name and a **verified**
   email, because derived-admin classification keys off the email domain and must not trust an
   unverified claim (`auth.py`). If the access token does not carry those claims, the server
   obtains them (e.g. from the provider's userinfo endpoint); the app must not be asked to send
   identity claims it could forge.
4. **Validation is strict.** Signature against the issuer's JWKS, issuer equal to the configured
   `OIDC_ISSUER`, audience equal to the API's configured audience, and expiry are all checked. A
   missing, malformed, expired or wrong-audience token is a **`401` in the shared envelope** — it
   never silently degrades to an anonymous request (an anonymous `GET /auth/session` is `200
   {authenticated: false}`, so a silent degrade there would read to the app as "signed out" rather
   than "refresh needed"; the bearer branch must make that case unambiguous).
5. **No cookie side effects.** A bearer-authenticated request neither reads nor sets the session
   cookie.
6. **Provider configuration exists for a public native client** in both providers: an Auth0
   *Native* application (separate from the existing *Regular Web Application*) and a Keycloak
   public client in the dev realm, each with the app's redirect URI registered.

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
- **Slice 2:** a *New path* button, and **swipe-to-delete** with a confirmation dialog (Delete is
  destructive and not undoable, Phase 1 §5.5). The confirm says nothing about streaks (Phase 5
  §4.6).
- **Slice 5:** the streak line and activity strip above the list, and Path streak chips on rows
  (§5.8).
- If the learner has Beats, the app does **not** show them in v1. (A one-line hint that Beats live
  on the web is §13 Q9.)

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
  with the Day arithmetic done on device in the learner's time zone, as the web does.
- **Slice 3:** the tutor's floating mark (§5.6). **Slice 5:** the *Day N 🔥* acknowledgement on
  the day's first completion (§5.8).

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
  app schemes and anything else render as plain text.
- **No remote images** are fetched (the prompt asks for none; the renderer enforces it rather than
  trusting the prompt).
- **Mermaid:** a ` ```mermaid ` fence renders as **its source in a code block**, labelled as a
  diagram — the API's documented fallback, and lossless because the prompt requires the surrounding
  prose to explain the diagram. A sandboxed `WKWebView` renderer (no network, no navigation,
  mermaid at `securityLevel: "strict"`, falling back to source on a parse error) is a **later
  enhancement**, not v1.
- The same renderer is reused for the tutor's replies, the Outcome explanation (inline subset) and,
  later, Briefs.

### 5.5 New path (Slice 2)

The onboarding capture, as the web's `/new`.

- **Topic** (required, 1–500 chars after trimming), **Level** (*new to it · some experience · I
  work in it*), optional **Guidance** (≤ 4000) and optional **Teaching style** (≤ 2000), each with
  a one-line hint that matches the web's copy. Client-side limits mirror the server's; the server's
  `422` is still handled.
- Submit → `POST /api/v1/paths` → `202 {id}` → poll `GET /paths/{id}`: **ready** navigates to the
  path view; **refused** shows the refusal message gracefully with the topic preserved for editing;
  **failed** keeps the learner on the form, inputs intact, with one-tap retry
  (`POST /paths/{id}/retry`); `429` shows the daily limit.
- Leaving the screen mid-generation cancels the poll, not the generation: the path appears on Your
  paths in its current state.
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
- The stream must survive the sheet being dismissed and re-opened within the lesson; leaving the
  lesson cancels it (§13 Q8 asks whether backgrounding should too).

### 5.7 Shape your path (Slice 4)

The Phase 2B shaping rail, unchanged in behaviour; its PRD governs. Gated by the `shaping` flag,
and offered only on a **ready** path.

- A sheet from the path view with its own thread (`/paths/{id}/shaping/conversation`), the same
  streaming as §5.6 plus the `proposal` frame.
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
- **Never** a streak warning, a nudge, a freeze or a leaderboard — and in this slice, no
  notification of any kind (Phase 5 §3).
- A failed summary read renders nothing, not an error: the streak is decoration, never a blocker.

### 5.9 Feature flags

The app reads `user.feature_flags` from `GET /api/v1/auth/session` and treats an unknown or
not-yet-loaded key as **off**, as the web's `useFeatureFlag` does — a gate never flashes open. A
flagged surface whose flag is off renders **no entry point at all** (not a disabled one), and a
`404` from a flagged route mid-use (a kill switch flipped) collapses that surface quietly rather
than erroring the screen. The flags are re-read on foreground, so a kill switch reaches the app
without a reinstall.

## 6. System design (product view)

These are **settled** with the owner; the TDD owns their mechanics.

- **Native SwiftUI, iOS 17+.** Observation (`@Observable`) for screen state and `NavigationStack`
  for navigation; **no third-party state-management library**. iOS 17 is the floor because
  Observation needs it.
- **Monorepo.** The Xcode project lives in **`ios/`** in this repository, so an API change and its
  client change are one PR, reviewed together, with both CIs green.
- **A generated API client.** `swift-openapi-generator` generates the Swift client and types from
  FastAPI's OpenAPI document. The document is exported to a committed file, and a check on the
  Linux `gate` job fails when the committed copy drifts from the app factory's — so a backend PR
  that changes a DTO shows its client-visible diff in review and cannot merge with a stale copy.
  The generated code sits behind small **protocols** the screens depend on (`PathsAPI`,
  `LessonsAPI`, `TutorStream`, …), which is what makes the in-memory fakes of §9.1 cheap.
- **The SSE stream is hand-parsed.** FastAPI's OpenAPI describes a `text/event-stream` response
  only loosely, so the two send routes are called through `URLSession.bytes(for:)` and a small,
  fully unit-tested frame parser over the four documented events plus the `: ping` comment.
- **Polling is structured concurrency.** Each poll loop is a `.task` on its screen: cancellation on
  disappear is the language's, not a timer the app must remember to invalidate.
- **An `Authenticator` protocol** owns tokens: the real one wraps `ASWebAuthenticationSession`,
  the Keychain and refresh; the fake hands out a fixed token. The API layer asks it for a token per
  request and reports `401`s back to it.
- **Configuration** — API base URL, OIDC issuer, client id, redirect URI — comes from build
  configurations (*Debug → local backend + Keycloak*, *Release → production + Auth0*). Any App
  Transport Security exception for plain-HTTP localhost is **Debug-only**.
- **Data model:** none of its own. The app persists only tokens (Keychain) and, at most, small UI
  preferences. There is no on-device database in v1.

## 7. Success metrics

The app does not get its own north star. **Activation rate** stays the north star; the app's job
is to make returning easier, so its primary metric is about return, stated so it can fail.

**Primary — Return through the app.**
> Among accounts that use both clients, the share of **Active days** on which the app was used,
> and the **Return** rate (a second distinct Day) for learners whose first path was started in the
> app versus on the web.

If the app does not raise Return for the learners who have it, it is a second rendering of the
same loop and the later slices should be re-argued rather than built.

**This needs one thing the API does not record today: which client made a request.** Product
events are emitted server-side and carry no client dimension, and there is no client event ingest
(metrics.md). The smallest honest answer is a server-side `client` attribute on product events
derived from how the request authenticated (bearer vs cookie) or from a `User-Agent` the app sets.
That is a backend change beyond "bearer only", so it is raised as **§13 Q5** rather than assumed.

**Supporting metrics**

- **App adoption:** % of active accounts with at least one bearer-authenticated request in a week.
- **First-lesson time** on a cold launch with a valid token: launch → Read passage visible, p50/p95
  (measured with `XCTMetric`/os_signpost in the UI suite, not with an SDK).
- **Slice uptake:** tutor messages, shaping Applies and streak-line views from the app, once each
  slice ships (the same events the web already emits).

**Guardrails**

- **≥ 99.5% of app launches crash-free** on TestFlight builds, from App Store Connect / Xcode
  Organizer crash reports — no third-party crash SDK. (Apple calls this "crash-free sessions";
  that is not a **Session** in the CONTEXT.md sense.)
- **No metric drift between clients.** `lesson_viewed` volume per completed lesson from the app
  stays within the web's band; a large excess means a poll loop is not cancelling.
- **Quick-check correctness** for app users stays in its Phase 1 band — a sharp rise suggests an
  answer leak and is investigated, not celebrated.
- **Sign-in failure rate** (provider errors and failed refreshes reaching the launch screen) —
  watched from structlog on the bearer path.

## 8. Core workflows (UI tests)

The XCUITest suite **mirrors the Playwright journeys**, tagged with the **same W-numbers**: a
workflow is one product behaviour, proven on each client that offers it, not a new workflow per
client. Like Playwright, it runs on a phone (the CI simulator). Which slice brings each:

| Slice | Mirrored workflows |
| --- | --- |
| 1 | **W2** progress persists across sign-out/relaunch · **W3** reach four completions on one path · **W4** multiple paths keep independent progress (paths seeded by the fixture) · **W6** Quick-check Outcome, both branches, and no answer before an Attempt · **W8** lesson generation failure is recoverable (the lesson half) |
| 2 | **W1** first path, end to end · **W5** delete a path · **W7** an unsafe topic is refused gracefully · **W8** (the outline half) |
| 3 | **W9**, **W11**–**W16** — the tutor |
| 4 | **W17**–**W21** — shaping |
| 5 | **W22**, **W23** — streaks |

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

## 9. Testing & CI

### 9.1 Unit tests — fakes over mocks

The repository's rule applies unchanged: **protocol-backed in-memory fakes, not mocks.** Every
screen model depends on the §6 protocols, so a test builds an `InMemoryPathsAPI` with the state it
needs (a path mid-generation, a failed lesson head, a `409 not_latest`) and asserts what the screen
model exposes. A test that needs a mocking framework is a sign the protocol is wrong. Pure logic —
the SSE frame parser, the poll back-off and stall rule, the `tz_offset_minutes` conversion, the
Markdown link-scheme filter, the resume-target choice — is tested directly with no fakes at all.

### 9.2 Snapshot tests

**swift-snapshot-testing** captures **every screen in every state** §5 defines (loading, empty,
generating, failed, refused, locked, the pre- and post-Attempt Quick check, path complete, and so
on), each in **dark, light and an accessibility text size**, on one pinned simulator device and iOS
version. Screens are rendered from fakes, so snapshots are deterministic and need no network.

**The reference PNGs are committed** under `ios/` and are the visual record of the app. This is
deliberate: it is how an agent with no Mac sees what it built. The loop is *push Swift → CI renders
→ PNGs land on the branch → read them*, and a reviewer sees UI changes as image diffs in the PR. CI
fails on any unrecorded difference and uploads the failing/expected/diff images as an artifact; how
new references are recorded on CI and returned to the branch is the TDD's (§13 Q11).

### 9.3 UI tests

**XCUITest** runs the §8 workflows. Two layers, because GitHub's macOS runners cannot run the
Docker services (Postgres, Keycloak) the Playwright job uses:

- **Hermetic (every PR):** the app launched with a launch argument that swaps in the in-memory
  fakes and the fake `Authenticator`. Fast, deterministic, and proves navigation, state and
  accessibility identifiers. It does **not** prove the contract with the real server.
- **Full-stack (the target):** the app against the real stub-model backend
  (`scripts/e2e_backend.py`) and a real Keycloak, with fixtures that create paths through the API
  using a token from the dev realm. How that stack runs on a macOS runner — native installs, a
  Linux job the simulator can reach, or a scheduled rather than per-PR run — is **§13 Q10**. Until
  it is answered, the contract is protected by the OpenAPI drift check (§6) and the generated
  client compiling against it.

W34's interactive sign-in drives the system `ASWebAuthenticationSession` sheet, which is the
flakiest thing to automate; if it cannot be made reliable it becomes a scripted manual check in
the TestFlight checklist (§10.2) and the token-handling half is covered by unit tests.

### 9.4 CI

- A separate **`ios` workflow** on a GitHub **macOS** runner: build, unit tests, snapshot tests,
  hermetic UI tests. Triggered by changes under `ios/`, the committed OpenAPI document, or the
  workflow itself — **not** on every backend PR, because macOS minutes cost several times Linux
  minutes (10× on private repositories at current GitHub pricing) and the drift check already runs
  on Linux. Runs on the same ref cancel their predecessors, as `ci.yml` does.
- Xcode and simulator versions are pinned in the workflow, not inherited from the runner image, so
  an image update does not silently change every snapshot.
- The four existing jobs (gate / integration / e2e / compose-smoke) are unchanged except for the
  drift check added to `gate`.

## 10. Distribution

### 10.1 Accounts and secrets

- An **Apple Developer Program** membership (annual fee), an App ID and bundle identifier, and an
  App Store Connect app record.
- **fastlane** drives build and upload, authenticating with an **App Store Connect API key**
  (issuer id, key id, `.p8`) stored in GitHub secrets, plus whatever signing material the TDD's
  signing choice needs (§13 Q12). Every new secret is documented in `deploy.md` beside the existing
  production secrets, with no value in the repository.

### 10.2 TestFlight

- **Internal** testing for the owner first: no Beta App Review, available minutes after upload.
- **External** testing by **public link** for friends once Slice 2 ships (§12) — before that,
  a friend could sign in but not make a path. External builds go through **Beta App Review**
  (typically the first build of each version), which needs a beta description, a feedback contact,
  sign-in instructions or a demo account for the reviewer, and the privacy information below.
- **Builds expire after 90 days.** A dormant app stops launching for testers, so the upload lane
  runs on a schedule (or on every merge that touches `ios/`) often enough that a current build
  always exists. A release checklist covers what automation cannot: a real-device sign-in against
  Auth0 and a pass through the Slice's workflows.
- **Versioning.** The build number comes from CI; the marketing version and what triggers an
  upload are §13 Q7 — the repository's semantic-release is wired to the backend, and an iOS
  `feat:` commit must not be what decides a Fly deploy.

### 10.3 App Store (later)

Known requirements and review risks, recorded now so the earlier slices do not paint the app into
a corner:

- **Sign in with Apple / an equivalent privacy-focused login** (Guideline 4.8) is likely required
  **if** the Auth0 tenant offers third-party social logins (e.g. Google) to create the primary
  account. An email/password-only Auth0 database connection does not trigger it. §13 Q1.
- **In-app account deletion** (Guideline 5.1.1(v)) is required of apps that let users create an
  account — and signing in for the first time provisions one. The API has **no account-deletion
  route** today, so this would be the PRD's second API addition. §13 Q4.
- **AI-generated content.** Reviewers may probe the refusal boundary through topic entry and the
  tutor. The existing boundary (Phase 1 §10) and its graceful refusals are the answer; the review
  notes should say plainly that content is AI-generated, and the age-rating questionnaire must be
  answered honestly for an open-ended generator and chat. A *report this lesson* affordance may be
  asked for; there is none today (Phase 2 §5.7b deliberately has no flag UI).
- A **privacy policy URL** and the App Privacy "nutrition label" (identifiers and account data
  collected; no tracking), plus export-compliance (HTTPS only — standard exemption).

## 11. Guardrails & safety

- **Generated content is untrusted** (§5.4.1): no raw HTML, filtered link schemes, no remote images,
  Mermaid as source. The rule applies to every surface showing model output — Read passages,
  explanations, tutor and shaping replies.
- **Answer-hiding is the server's, and the app must not undo it.** The app never caches a
  `correct_index` it obtained from anywhere but an Attempt response (or a later payload carrying
  the Attempt), never logs payloads, and never prefetches in a way that grades.
- **Tokens:** Keychain, device-only; never logged, never in a URL, never in a crash report; the
  binary contains no client secret.
- **Kill switches reach the app.** Flags are re-read on foreground (§5.9); a flagged route's `404`
  degrades the surface quietly.
- **No new data leaves the device.** No analytics SDK, no crash SDK beyond Apple's own.
- **The refusal boundary is unchanged.** The app adds no new generation input; it sends exactly the
  fields the web sends.

## 12. Phasing

Slices are numbered on their own to avoid colliding with the roadmap's phases. Each ends with its
§8 workflows green on CI and a TestFlight build.

| Slice | Contents | Exit |
| --- | --- | --- |
| **0 — PoC** *(another branch)* | A trivial home screen in `ios/`, built on a macOS runner, with one committed snapshot PNG an agent can read. | The CI build + screenshot loop works end to end. Nothing user-facing. |
| **1 — Read and check (MVP)** | Bearer auth merged and deployed (dependency); the generated client and drift check; §5.1 sign-in/out; §5.2 Your paths (read-only, Continue); §5.3 path view (read-only); §5.4 lesson view with every state; the snapshot and hermetic UI suites; TestFlight **internal**. | The owner does a day's lessons in the app; W2, W3, W4, W6, W8 (lesson), W34, W35, W36 pass. |
| **2 — Make paths** | §5.5 New path; delete, rename, Teaching style. TestFlight **external** public link opens. | W1, W5, W7, W8 (outline) pass; a friend makes and starts a path unaided. |
| **3 — The tutor** | §5.6. | W9, W11–W16 pass. |
| **4 — Shape your path** | §5.7. | W17–W21 pass. |
| **5 — Streaks** | §5.8. Small — one read and three views — and could move earlier without disturbing anything. | W22, W23 pass. |
| **6 — Push notifications** *(later)* | New device-registration endpoints (`POST`/`DELETE` a device token), an APNs sender, the permission prompt, and a use case that survives §13 Q6. | Its own PRD amendment, written when the use case is chosen. |
| **Later** | Beats & Briefs; Flow once launched on the web; sandboxed Mermaid; App Store submission (§10.3). | Sequenced against usage. |

## 13. Risks & open questions

**Risks**

- **The bearer branch is the critical path.** Nothing past Slice 0 can be tested end to end until
  it is merged and deployed with both providers configured (§5.1 requirement 6).
- **Full-stack UI tests on macOS** may not be practical (no Docker on GitHub's macOS runners), in
  which case the app's contract with the real server is proven only by compilation against the
  OpenAPI document and by manual TestFlight passes. Mitigation: the drift check, the hermetic
  suite, and a short manual checklist per release.
- **OpenAPI fidelity.** Pydantic v2's `anyOf`-with-`null` and discriminated unions (e.g. a Beat's
  two entry shapes, Proposal operations) can generate awkward Swift. Mitigation: wrap generated
  types behind the §6 protocols and app-owned models, so an awkward shape is fixed in one adapter.
- **Snapshot churn.** Three appearance variants × every state × every screen is a large and growing
  set of PNGs in git. Mitigation: pinned device and toolchain, and a periodic look at repository
  size (Git LFS if it bites).
- **macOS CI cost** (§9.4). Mitigation: path filters, cancellation, and no full-stack run per PR.
- **Two clients drift in behaviour, not data.** A rule implemented client-side on the web (the
  resume target, the stall timeouts, the streak line's copy, path-complete Day arithmetic) can
  diverge in Swift. Mitigation: each is named in §5 with its web source, unit-tested on both sides,
  and changed in the same PR.
- **TestFlight expiry** (§10.2) silently strands friends if nobody uploads for 90 days.

**Open questions** (the author's recommendation in brackets; nothing here is decided)

1. **Which Auth0 connections are enabled in production?** If any social login can create the
   account, Guideline 4.8 likely requires Sign in with Apple (or an equivalent) before the App
   Store — and adding an Apple connection in Auth0 gives a learner who signs in with Apple a
   *different* `(issuer, subject)` from their web login, i.e. a second, empty Account. [Decide
   before external TestFlight; consider email/password only, or account linking in Auth0.]
2. **How does each provider mint an API access token the server can validate?** Auth0 issues a
   JWT access token only when an `audience` (an Auth0 API) is requested; Keycloak needs an
   audience mapper on the public client. The bearer branch's choice decides what the app sends.
3. **Ephemeral or shared browser session for sign-in?** `prefersEphemeralWebBrowserSession` avoids
   the system "wants to use … to sign in" prompt and leaves no Safari cookie, but forgoes SSO with
   Safari; and sign-out then needs no provider logout. [Ephemeral.]
4. **Account deletion.** Needed for the App Store (§10.3) but not TestFlight, and it is an API
   addition the "API unchanged" decision did not anticipate. [Defer to the App Store slice; specify
   it for both clients at once.]
5. **A `client` dimension on product events** (§7). Without it the primary metric cannot be
   computed. [A server-derived attribute from the auth method — a small backend change worth
   making alongside bearer auth.]
6. **What is push *for*?** The streaks PRD says *never* a push about streaks, and the web sends
   nothing. Plausible uses that do not contradict it: "your path is ready" after a long outline
   generation, or a published Brief once Beats reach the app. [Pick the use case before building
   Slice 6's endpoints.]
7. **How is the iOS app versioned and released?** semantic-release versions and deploys the
   backend from commit types, so an iOS-only `feat:` would cut a backend release and a Fly deploy.
   Options: an `ios` commit scope the analyzer ignores, a separate iOS tag namespace, or a manual
   `workflow_dispatch` upload lane. [Manual dispatch plus a scheduled upload to beat the 90-day
   expiry.]
8. **Should a streaming tutor reply survive backgrounding?** iOS suspends the app shortly after it
   leaves the foreground, which ends the stream and so persists nothing. [Accept it in v1: the
   question stays in the composer for one-tap resend.]
9. **Should Your paths acknowledge Beats** for a learner who has them ("Your analysts live on the
   web for now")? Knowing whether they have any means calling `GET /beats`, which runs the arrival
   drain and can claim (and bill) research — so even a count is not free. [No call; at most a
   static line in the account menu.]
10. **Where does the full-stack XCUITest stack run?** Native Postgres + Keycloak on the macOS
    runner, a self-hosted Mac, or a nightly rather than per-PR job. [Nightly, native installs,
    once Slice 1 is stable.]
11. **How do recorded snapshots get back to the branch?** A CI job that commits new references to
    the PR branch (bot commit) versus an artifact a human or agent downloads and commits. The
    owner's intent is that PNGs are committed so an agent can view them; a bot commit is the
    lowest-friction way for an agent with no Mac. [Bot commit on an explicit `record` dispatch or
    label, never automatically on a failing run.]
12. **Code signing:** fastlane `match` (a private certificates repo and a passphrase secret) versus
    Xcode cloud-managed signing via the API key. [Cloud-managed signing — one fewer repository and
    secret.]
13. **Is light mode real?** The web app is dark-only (`color-scheme: dark`) — Nocturne is a dark
    system. The settled testing plan snapshots light and dark. Either the iOS app gets a genuine
    light Nocturne (design work this PRD does not otherwise budget for), or it pins dark and the
    light snapshots are dropped. [Pin dark for v1; keep the large-text variant.]
14. **Redirect URI.** The HTTPS-callback form of `ASWebAuthenticationSession` arrives in iOS 17.4;
    on an iOS 17.0 floor a custom URL scheme is the portable choice. [Custom scheme, or raise the
    floor to 17.4 — either is cheap now and expensive later.]
15. **Auth library or hand-rolled?** The no-third-party rule was stated for state management.
    AppAuth-iOS is the standard OIDC client; a hand-rolled flow over `AuthenticationServices` +
    `CryptoKit` is small but is security code. [TDD's call; state the trade-off there.]

## 14. Release criteria (v1 = Slices 1–5)

The app's v1 is shipped when:

- [ ] Every §8 workflow for Slices 1–5 passes on CI (hermetic), and W34/W35 pass against a real
      provider at least in the release checklist.
- [ ] A learner can sign in with their web account and see exactly the paths and progress the web
      shows — and a completion in either client shows in the other on its next read.
- [ ] The Quick check answer is never present in the app before an Attempt (W6), and no screen
      grades on the client.
- [ ] A hostile Read passage renders inert (W36); no surface renders model output through anything
      but the one Markdown renderer.
- [ ] Every screen state in §5 has a snapshot in dark and at an accessibility text size (and light,
      if §13 Q13 keeps it), committed and green.
- [ ] Leaving any generating screen stops its polling, verified by a unit test per poll loop and by
      the `lesson_viewed` guardrail on TestFlight usage.
- [ ] A flag turned off on the server removes its surface from the app on next foreground.
- [ ] A TestFlight build is available to internal and external testers, uploaded by CI from secrets,
      with the runbook in `deploy.md`.
- [ ] The docs in §2.3 are updated in the same PRs as the code they describe.

---

### Appendix — traceability to the web app

| Web route / component | API | iOS screen | Slice |
| --- | --- | --- | --- |
| `/login`, `lib/auth.ts` | OIDC + `GET /auth/session` | Launch / sign-in (§5.1) | 1 |
| `/` — Your paths, Continue card | `GET /paths` | Your paths (§5.2) | 1 |
| `/` — New menu, delete confirm | `POST /paths`, `DELETE /paths/{id}` | Your paths (§5.2) | 2 |
| `/` — streak line, activity strip, chips | `GET /progress/summary` | Your paths (§5.8) | 5 |
| `/` — Beats section, Flow door | `GET /beats`; none (Flow is client-only) | — | Later |
| `/paths/{id}` — path rail, states | `GET /paths/{id}`, `POST …/retry` | Path view (§5.3) | 1 |
| `/paths/{id}` — title, Teaching style | `PATCH /paths/{id}`, `PUT …/teaching-style` | Path view (§5.3) | 2 |
| `/paths/{id}` — shaping rail, ghost rows | `/shaping/…`, `apply-proposal`, `/changes` | Shaping sheet (§5.7) | 4 |
| `/lessons/{id}` — Read, Quick check, complete | `GET /lessons/{id}`, `…/attempt`, `…/complete`, `…/generate` | Lesson view (§5.4) | 1 |
| `components/markdown.tsx`, `mermaid.tsx` | — | Markdown renderer (§5.4.1) | 1 |
| `components/tutor/` — the Rail | `/conversation…`, `tutor-check-answer` | Tutor sheet (§5.6) | 3 |
| `/new` — onboarding | `POST /paths`, `GET /paths/{id}` | New path (§5.5) | 2 |
| `/beats/new`, `/beats/{id}`, `/briefs/{id}` | `/beats…`, `/briefs…` | — | Later |
| `/flow/new`, `/flow/done`, flow bar | none | — | Later |
