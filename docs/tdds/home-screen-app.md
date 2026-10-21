# TDD — The Home Screen app (installable web app and Web Push)

**Status:** Accepted for provisional feature-branch implementation; T0 and release acceptance outstanding · **Owner:** solo builder · **Companion to:** [Home Screen app PRD](../prds/home-screen-app.md)
**References:** [`CONSTITUTION.md`](../../CONSTITUTION.md) (§1A) · [`CLAUDE.md`](../../CLAUDE.md) · [`CONTEXT.md`](../CONTEXT.md) · [`api.md`](../api.md) · [`architecture.md`](../architecture.md) · [`ci.md`](../ci.md) · [`deploy.md`](../deploy.md) · [ADR 0001 — store the last-seen time zone](../adrs/0001-store-last-seen-time-zone.md) · [iOS app TDD](ios-app.md) (parked if the PRD is accepted; §9 and §10 here restate what this plan keeps from it)

## Execution amendment (2026-10-20)

**This amendment overrides the historical draft below**, especially proposed owner
decisions, spike-first dependencies, production delivery and optional T7 splitting.
The [PRD execution amendment](../prds/home-screen-app.md#execution-amendment-2026-10-20)
records approved P1–P6 and constitution §8; D1, D7, D11 and D14 are no longer
awaiting those approvals. Native parking is conditional. Signed-cookie sessions
are approved for 90 days since last visit without server-side revocation.
The [owner authorization](https://github.com/mattjmcnaughton/aleph/issues/257#issuecomment-6280580393)
defers T0 to the **end**, accepting provisional/rework risk, not declaring feasibility.
Do not record spike findings until observed; §13 remains empty. No offline cache,
native implementation or native backlog changes are in scope.

**Delivery:** ticket PRs target `feature/home-screen-app`, not `main`. Feature-only
integration is not release acceptance. No deployment, production secrets/data/
infrastructure or Auth0 changes, paid evals or remote branch deletion are authorized.
The throwaway T0 branch is not merged. Human and operational gates are deferred,
not waived or passed: real-iPhone sign-in/force-quit/reopen, declarative payload with
and without the push handler, tap-through of each kind, week-long use, long-session
return, and job deployment/image/env/secrets/schedule checks need recorded evidence
and separate authorization for any production work. CI fakes cannot prove these.

### Ticket-to-issue mapping and provisional dependencies

All issue numbers below are in [mattjmcnaughton/aleph](https://github.com/mattjmcnaughton/aleph/issues/257).
Dependencies require behavior on the exact base, not merely a closed issue.

| Unit | Issue | Provisional prerequisite / scope |
| --- | --- | --- |
| Bootstrap | [#258](https://github.com/mattjmcnaughton/aleph/issues/258) | Feature-branch CI/integration groundwork |
| T0 | [#259](https://github.com/mattjmcnaughton/aleph/issues/259) | Deferred to end; real-iPhone feasibility, never assumed passed |
| T1 | [#260](https://github.com/mattjmcnaughton/aleph/issues/260) | Bootstrap; documentation adoption, T0 no longer blocks provisional work |
| T2 | [#261](https://github.com/mattjmcnaughton/aleph/issues/261) | T1; installability |
| T3 | [#262](https://github.com/mattjmcnaughton/aleph/issues/262) | T1; Flow default and W34 port |
| T4 | [#263](https://github.com/mattjmcnaughton/aleph/issues/263) | T1; Delete Beat web port |
| T5 | [#264](https://github.com/mattjmcnaughton/aleph/issues/264) | T1; approved session lifetime |
| T6 | [#265](https://github.com/mattjmcnaughton/aleph/issues/265) | T1; time zone and client dimension |
| T7a | [#266](https://github.com/mattjmcnaughton/aleph/issues/266) | T2, T6; backend push, preferences, sender, Path ready |
| T7b | [#268](https://github.com/mattjmcnaughton/aleph/issues/268) | T7a; service worker, permission/subscription, Path ready UI, W38 |
| T8 | [#267](https://github.com/mattjmcnaughton/aleph/issues/267) | T6; job/Beats, no push prerequisite |
| T9 | [#269](https://github.com/mattjmcnaughton/aleph/issues/269) | T7a, T7b, T8; Reminders/Brief published, W39/W40 |
| Verification | [#270](https://github.com/mattjmcnaughton/aleph/issues/270) | Implemented units plus deferred T0 and recorded device/operational acceptance |

T7 **is split**, backend first, not optionally split on review size. The Reminder
toggle, chosen hour and Reminder pre-prompt belong to **T9**; references in §8.7,
§8.8 and ticket 7 do not pull them forward into T7b. Backend preference schema/defaults
may still be established in T7a. Review exact-base readiness: native-branch ports
#248/#250/#253 are relevant prior work but are **not on the T1 baseline**; inspect
and port deliberately rather than treating native branch behavior as already present.

**Owner-approved UI decision (2026-10-21, option 2):** `needs_home_screen` shows
install instructions alongside editable account-wide preferences, with explicit copy
that this browser cannot receive Notifications yet. No Device enablement action is
offered in this state. Reminder controls and pre-prompt remain T9-owned.

> **This document is the implementer's brief.** Where it says **settled**, the owner decided.
> Where it says **proposed**, the PRD asks the owner to confirm (PRD §11.1). Where it says
> **decided here**, the author chose and the owner can overturn it in review. Codebase facts are
> cited against `main` at `6381c4e`.
>
> Decision numbers restart at D1 and are scoped to this document. PRD decisions are written
> "PRD P1"; parked iOS documents are written "iOS TDD §14".

## 0. Brief for the implementer

**What you are building.** Three things on top of the existing React app and FastAPI backend:

1. **Installability** — a manifest, icons, Home Screen metadata, an install card for iPhone
   Safari, and a longer session.
2. **Notifications over Web Push** — Devices (push subscriptions), preferences, a VAPID-signed
   sender with an allowlist of push services, a notifications-only service worker, a
   Notifications settings screen, and the three kinds (Path ready, Reminder, Brief published).
3. **The backend groundwork the iOS plan already designed** — the stored Learner time zone, the
   `client` event dimension, and the hourly scheduled job that sends Reminders and publishes
   Briefs.

**What you are not building.** Any Swift; any offline caching; anything for App Store or
TestFlight; any change to an existing `/api/v1` payload.

**Read first, in this order.** `CLAUDE.md`; `docs/CONTEXT.md` (say **Notification**,
**Reminder**, **Device**, **Learner time zone**, **Home Screen app**); the PRD §5 for the part you
are building; the ticket in §12 here.

**How to work.** Red-green TDD, fakes over mocks. One PR per ticket, Conventional Commits. Any
commit a learner should see in production is `fix` or `feat` (CLAUDE.md, *Commit
Conventions*). Ticket 0 is a throwaway spike and is never merged.

## 1. Decision record

| # | Decision | Choice | Status · why |
| --- | --- | --- | --- |
| D1 | Phone client | The existing web app as a Home Screen app; native app parked. | **Proposed** (PRD P1, P2), gated on ticket 0. |
| D2 | Offline | **None.** The service worker has no `fetch` handler and caches nothing. | *Decided here.* No stale-bundle class of bugs; matches the PRD's online-only non-goal. |
| D3 | Service worker | One hand-written file, `public/sw.js`, scope `/`, registered on app start. No Workbox, no Vite PWA plugin. | *Decided here.* It is ~60 lines with two event handlers; a plugin would add a build step and caching defaults we would have to turn off. |
| D4 | Push payload | The **declarative Web Push** JSON shape (`web_push: 8030`, `notification: {title, body, navigate, tag, lang}`) for every browser. The service worker's `push` handler shows the same JSON in browsers that do not handle it declaratively. | *Decided here.* One payload; Safari 18.4+ can show it even if the service worker fails. Verified in ticket 0. |
| D5 | Subscription | Created through the **service worker registration's** `pushManager` (works everywhere), not `window.pushManager` (Safari-only declarative subscription). | *Decided here.* One code path across browsers. |
| D6 | Sender | Hand-written `WebPushSender` over `httpx`: VAPID JWT (ES256) with **joserfc**; payload encryption (RFC 8291 `aes128gcm`) with **cryptography** primitives, tested against RFC 8291's Appendix A vector. Both packages are already in `uv.lock`. | *Decided here*, mirroring iOS TDD D24 (hand-written APNs over httpx). The owner may prefer `pywebpush`; it is synchronous (`requests`) and would need a thread, which is why it is not the default. |
| D7 | Endpoint allowlist | A Device's `endpoint` must be `https`, on port 443, with a host ending in one of: `push.apple.com`, `fcm.googleapis.com`, `push.services.mozilla.com`, `notify.windows.com`. Otherwise `422`. | **Proposed** (PRD P5). Without it, Device registration is a server-side request forgery primitive. |
| D8 | Device identity | One Device per subscription `endpoint` (unique). Registering an existing endpoint moves it to the registering Account and refreshes its keys. | *Decided here*, the same rule as iOS TDD §13.2 for tokens. |
| D9 | Re-registration | On every app start and on every return to the foreground, when permission is `granted`: `getSubscription()`; if none, `subscribe()` again; then `POST /devices` (upsert). | *Decided here.* iOS can drop a subscription silently and fires no `pushsubscriptionchange`. |
| D10 | Push key delivery | `GET /api/v1/push-config` returns the VAPID public key, or `404` when push is not configured, which hides every Notifications control. | *Decided here.* Rotating the key needs no web rebuild, and "no VAPID secrets" is the kill switch. |
| D11 | Session lifetime | `SessionMiddleware(max_age=SESSION_MAX_AGE_SECONDS)`, default **90 days**. | **Proposed** (PRD P3). Starlette already re-issues the cookie on every response while the session holds data, so this is 90 days since the last visit. |
| D12 | Install card trigger | Shown when the browser is iPhone Safari, not standalone, and not dismissed in the last 30 days. | *Decided here.* Detection is a pure function (§4). |
| D13 | Time zone transport | `X-Aleph-Time-Zone: <IANA name>` on every API request, from `apiFetch` and the stream `fetch` calls. | **Settled** in iOS TDD D11; web half only. |
| D14 | `client` dimension | Client-reported header `X-Aleph-Display-Mode: standalone \| browser` on cookie requests → `home_screen` / `web`; bearer requests keep the iOS TDD's mapping (`ios` / `unknown`); job events carry none. | **Proposed** (PRD P6). |
| D15 | Notification links and origin | Absolute URLs built from a new `PUBLIC_BASE_URL` setting, required in production and set to **`https://aleph.mattjmcnaughton.com`**. The custom domain is the only origin learners install from: a Home Screen app, its cookies and its push subscription all belong to the origin it was added from, so one added from `aleph-prod-mattjmcnaughton.fly.dev` would be a separate install. | Origin **settled** (owner). The setting is *decided here*: the job has no request to take an origin from. |
| D16 | Scheduled job | Exactly iOS TDD D14–D17 and §14: hourly Fly scheduled Machine, `deploy/schedules.toml`, recreate on deploy, per-Beat per-local-day guard, idle-Beat rule, UTC for no stored zone. | **Settled** there; restated in §10. |
| D17 | Workflows | W38, W39, W40 keep the iOS PRD's numbers (same behaviours). W34 is Flow (renumbered from W32). W35–W37 stay reserved. | *Decided here.* |

## 1A. Constitution check

How this design meets each principle in [`CONSTITUTION.md`](../../CONSTITUTION.md), how it is
enforced, and the evidence that will show it. Gaps are listed, not papered over.

| § | Applies because | Enforcement | Evidence |
| --- | --- | --- | --- |
| 1 Server owns authorization | Devices and preferences are learner-owned; a Notification names an id. | Device and preference routes act only on the authenticated Account; `DELETE /devices/{id}` for another Account is `404`. A Notification's link opens through the normal authorized reads, so a stray id is a `404`. The endpoint allowlist is checked on the server (D7), not trusted from the client. | Integration tests for cross-Account `404`s and `422` on a non-allowlisted endpoint; unit tests of `is_allowed_endpoint` with lookalike hosts. |
| 3 Truthful history; no duplicates | Reminders, Briefs and Device registration are retried and raced. | `reminder_sends (user_id, local_date)` primary key written before delivery; Beat claims stay the atomic `UPDATE`; Brief published hooks the existing persist step; Devices upsert on a unique `endpoint`. All are database guarantees, not in-process locks. | Job integration tests: killed run never double-sends, 24 ticks run a Beat once, concurrent Device registrations leave one row. |
| 4 Failure, absence, success are distinct | A send can be delivered, refused, gone, or not attempted. | `notification_sent.outcome` distinguishes `delivered`, `gone`, `error` and `disabled` (no VAPID keys); a disabled sender is never reported as delivered. The job exits non-zero when a step fails. The settings screen distinguishes *off*, *blocked*, *needs Home Screen* and *unsupported*. | Sender response-mapping unit tests; `pushState` table; tick exit-code test. |
| 5 Domain language | New concepts: Home Screen app, Device as a subscription. | CONTEXT.md updated before code (ticket 1). Pure rules (endpoint allowlist, Notification copy, whether a Reminder is due, resume target) live in `domains/`. | Review against CONTEXT.md; domain unit tests with examples and counterexamples (Active day suppresses a Reminder; Skipped sends no Brief published). |
| 6 Dependency boundaries | New sender, job and routes. | Routers parse and translate only; `services/notifications.py` orchestrates; `services/push.py` sender behind a `Protocol`; domain functions take explicit inputs. The existing layering test covers the new modules. | Layering test passes with the new modules included. |
| 7 Earned complexity | New service worker, sender and scheduled machine. | No offline caching, no service-worker framework (D2, D3); one new operational piece (the scheduled machine), justified by Reminders and away-time Briefs. The sender adds no new package (D6). | Ticket review. |
| 8 One API, equivalent experiences | This plan removes the iOS client and moves push to the web. | §8 is amended in the same PR as this TDD (amendment record 2026-10-19) so it no longer names push as iOS-only. With one learner client, the parity rule has nothing to check until a second client returns. | The amendment record; CLAUDE.md reconciled. |
| 9 Untrusted generated content | Notification copy can include a path title or Beat topic, which are model-derived or learner-entered. | Plain-text body, no Markdown; `navigate` must be same-origin (checked in `sw-logic`); no Read passage, Brief body or tutor content in a payload. | `sw-logic` unit tests; copy unit tests with hostile titles (HTML, newlines, very long). |
| 10 Verification at the claim's level | Install, sign-in and real delivery happen in iOS Safari. | Backend guarantees in integration tests; client behaviour in Vitest and Playwright. | **Gap:** CI cannot drive iOS Safari as a Home Screen app, so sign-in inside it, real delivery and tap-through are proven only by the manual release checklist (§11). Recorded, not claimed as automated. |

## 2. Architecture overview

```
iPhone Home Screen app / browser tab                      Fly: aleph web machine
┌───────────────────────────────┐   cookie + headers      ┌──────────────────────────────┐
│ React app (unchanged screens) │ ──────────────────────▶ │ /api/v1 (+ devices, prefs,   │
│ lib/push.ts  lib/install.ts   │  X-Aleph-Time-Zone       │   push-config)               │
│ routes/settings.notifications │  X-Aleph-Display-Mode    │ services/notifications.py    │
└──────────────┬────────────────┘                          │ services/push.py (sender)    │
               │ registers                                  └──────────────┬───────────────┘
┌──────────────▼────────────────┐   encrypted push         ┌──────────────▼───────────────┐
│ public/sw.js                  │ ◀── Apple / Google / ◀── │ Fly scheduled machine:       │
│  push → showNotification      │     Mozilla push svc     │ python -m aleph.jobs.tick    │
│  notificationclick → open URL │                          │ (Reminders, Beats → Briefs)  │
└───────────────────────────────┘                          └──────────────────────────────┘
```

Layering is unchanged: `routers → services → (repositories, agents)`. The sender is a service
behind a `Protocol`; pure decisions (whom to remind, what wording, whether an endpoint is
allowed) live in `domains/` and are unit-tested without I/O.

## 3. Installability

**Files (web).**

- `public/manifest.webmanifest`:
  ```json
  {
    "id": "/",
    "name": "aleph",
    "short_name": "aleph",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "<night>",
    "theme_color": "<night>",
    "icons": [
      { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
      { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" },
      { "src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
    ]
  }
  ```
  `<night>` is the hex of the `night` token in `tailwind.config.ts`; the ticket copies the value,
  it does not invent one.
- `public/icons/`: 192, 512, maskable 512, and `apple-touch-icon.png` (180×180, no
  transparency), rendered from `components/aleph-logo.tsx`'s mark on night. Committed PNGs plus
  the one-off script that renders them (`scripts/render_icons.mjs`), so they can be regenerated.
- `index.html` gains: `<link rel="manifest">`, `<link rel="apple-touch-icon">`,
  `<meta name="theme-color">`, `<meta name="apple-mobile-web-app-title" content="aleph">`, and
  `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">` (content
  already pads by the safe-area insets because of `viewport-fit=cover`). The `vite.svg` favicon is
  replaced with the aleph mark.

**Serving (backend).** `web/serve.py` serves `dist/` through `SPAStaticFiles`. Two changes:

- Register the manifest's media type at import:
  `mimetypes.add_type("application/manifest+json", ".webmanifest")`. Python's `mimetypes` does
  not know the extension on every platform, and Starlette guesses from it.
- `sw.js` must be served with `Cache-Control: no-cache` so a deploy's new service worker is
  picked up on the next visit. Add the header for that one path in `SPAStaticFiles.get_response`.

Neither file can fall through to the SPA shell, because `_is_spa_navigation` already refuses
paths with a `.` in their last segment. Add unit tests for both responses.

**Navigation without a back button.** Audit every route in `src/routes/` in standalone mode
(Playwright can emulate it with `matchMedia` only, so this is a manual pass recorded in the
ticket): `login.tsx` error state, `flow.new.tsx`, `flow.done.tsx`, `new.tsx`, `beats.new.tsx`.
Each must offer a visible way to an ancestor (breadcrumbs or a Cancel that routes). Fix what is
missing in the same ticket.

## 4. The install card

`lib/install.ts` exports a pure function:

```ts
type InstallContext = {
  userAgent: string;
  standalone: boolean;          // matchMedia('(display-mode: standalone)').matches || navigator.standalone === true
  dismissedAt: number | null;   // epoch ms from localStorage, null when absent or unreadable
  now: number;
};
export function shouldShowInstallCard(ctx: InstallContext): boolean;
export function installState(ctx: InstallContext & { pushSupported: boolean }):
  "home_screen" | "needs_home_screen" | "unsupported" | "browser_ok";
```

- iPhone Safari is `/iPhone/` in the user agent and **not** `CriOS|FxiOS|EdgiOS` (other iOS
  browsers cannot add a Home Screen app the same way, and the card's instruction would be
  wrong). iPad is excluded (PRD: iPhone only).
- `shouldShowInstallCard` is true for iPhone Safari, not standalone, and `dismissedAt` older than
  30 days or null.
- `installState` drives Notifications settings: `home_screen` (standalone, push supported),
  `needs_home_screen` (iPhone Safari tab), `browser_ok` (any other browser with `PushManager`),
  `unsupported`.

`components/install-card.tsx` renders on Your paths (`routes/index.tsx`) above the Continue card.
**Not now** writes `dismissedAt` (wrapped in try/catch; storage failure means the card simply
shows again next visit). Unit tests: a table over user agents, standalone and dismissal ages.

## 5. Session lifetime

`config.py`: `session_max_age_seconds: int = 90 * 24 * 3600`. `app.py` passes it as `max_age` to
`SessionMiddleware`, and the comment above it is rewritten: the trade-off (signed cookie, no
server-side revocation, logout clears client-side) is unchanged in kind and now spans 90 days.
Integration test: a response to an authenticated request carries `Set-Cookie` with
`Max-Age=7776000`.

## 6. Learner time zone

As iOS TDD §11 specified, web half only:

- **Server (migration 0017).** `users.time_zone text NULL`, `users.time_zone_seen_at timestamptz
  NULL`. `services/time_zone.py::observe(user, header)` validates (≤ 64 chars, in
  `zoneinfo.available_timezones()`; anything else ignored, never a `4xx`) and writes only when
  changed, in its own short transaction:
  `UPDATE users SET time_zone = :tz, time_zone_seen_at = now() WHERE id = :id AND time_zone IS DISTINCT FROM :tz`.
  Called after `get_optional_current_user` resolves a user (`dependencies.py`). Add `tzdata` as a
  runtime dependency so `zoneinfo` works on `python:3.12-slim`.
- **Web.** `apiFetch` (`lib/api.ts`) and the stream `fetch` in `lib/tutor-stream.ts` (and the
  shaping stream's) set `X-Aleph-Time-Zone` from
  `Intl.DateTimeFormat().resolvedOptions().timeZone`. One helper, `lib/client-headers.ts`, builds
  both headers in §7 so the three call sites cannot drift.
- Not declared in OpenAPI; `api.md` documents it under conventions.
- Requests carrying `tz_offset_minutes` keep using it for themselves.

## 7. The `client` event dimension

- `lib/client-headers.ts` also sets `X-Aleph-Display-Mode: standalone | browser`.
- `dependencies._bind_user_to_request` (`dependencies.py:164`) sets a `ContextVar`
  `request_client`: for `auth_method == "session"`, `home_screen` when the header is
  `standalone`, else `web`; for `bearer`, the iOS TDD §12 mapping (`azp`/`client_id` through
  `OIDC_CLIENT_MAP`, else `unknown`). Any other header value reads as `web`.
- `events._emit` (`events.py:295`) adds `client=` when the var is set; `EVENT_FIELDS`
  (`events.py:120`) gains `client` as a common field so `tests/unit/test_events` and
  `test_metrics_queries` anchor it. Background tasks spawned from a request inherit the var; the
  job never sets it.
- `metrics.md` documents the field and the PRD §7 queries, and states that `home_screen` vs `web`
  is client-reported.

## 8. Push

### 8.1 Vocabulary (CONTEXT.md, in ticket 7)

- **Home Screen app** (new): aleph opened from an icon on the phone's Home Screen, running
  full-screen without browser controls. The same web app, not a separate client.
- **Notification** (reworded): a push message aleph sends to a learner's phone or browser.
  Three kinds, as today. Drop "The web has no Notifications."
- **Device** (reworded): one browser or Home Screen app where a learner allowed Notifications
  (one Web Push subscription). An Account can have several. A Device belongs to whichever Account
  registered it last; signing out on it removes it; one the push service reports gone is
  removed.

Code names: `Device`, `NotificationKind.PATH_READY | REMINDER | BRIEF_PUBLISHED`.

### 8.2 Data model (migration 0018)

```
devices                    id uuid PK · user_id FK→users ON DELETE CASCADE ·
                           endpoint text UNIQUE · p256dh text · auth text ·
                           user_agent text NULL · created_at · last_registered_at timestamptz
notification_preferences   user_id PK FK→users ON DELETE CASCADE ·
                           reminder_enabled bool default false ·
                           reminder_hour smallint default 20 CHECK (0..23) ·
                           path_ready_enabled bool default true ·
                           brief_published_enabled bool default true
reminder_sends             user_id FK→users ON DELETE CASCADE · local_date date ·
                           sent_at timestamptz · outcome text
                           PRIMARY KEY (user_id, local_date)
```

`notification_preferences` and `reminder_sends` are exactly iOS TDD §13.2. `devices` replaces the
APNs token columns with the subscription's `endpoint` and keys. A missing preferences row reads
as the defaults. `user_agent` is stored only to label Devices if a list is ever shown; nothing
reads it in v1.

### 8.3 Routes (all `/api/v1`, cookie or bearer, documented in api.md)

| Method | Path | Body | Response | Notes |
| --- | --- | --- | --- | --- |
| `GET` | `/push-config` | — | `200 {vapid_public_key}` or `404` | `404` when VAPID is not configured (D10). |
| `POST` | `/devices` | `{endpoint, keys: {p256dh, auth}}` (the browser's `PushSubscription.toJSON()`) | `200 {id}` | Upsert on `endpoint` (D8). Endpoint outside the allowlist → `422` with code `push_endpoint_not_allowed` (D7). Keys must be base64url, `p256dh` 65 bytes decoded, `auth` 16 bytes. |
| `DELETE` | `/devices/{id}` | — | `204` | Idempotent; another Account's id is `404`. |
| `GET` | `/notification-preferences` | — | `200 {reminder_enabled, reminder_hour, path_ready_enabled, brief_published_enabled}` | |
| `PUT` | `/notification-preferences` | same shape | `200` same shape | `reminder_hour` outside 0–23 is `422`. |

The allowlist check is a pure function in `domains/push_endpoint.py`
(`is_allowed_endpoint(url) -> bool`), unit-tested with lookalike hosts
(`fcm.googleapis.com.evil.example`, `evilpush.apple.com`, `http://`, a port, userinfo, an IP).

### 8.4 Sender (D6)

`services/push.py`:

```python
class PushSender(Protocol):
    async def send(self, device: Device, message: PushMessage) -> PushResult: ...
```

- `WebPushSender`:
  - builds a VAPID JWT: `aud` = the endpoint's origin, `sub` = `PUBLIC_BASE_URL`, `exp` = now +
    12 h, ES256 with `VAPID_PRIVATE_KEY`. Cache one JWT per audience until 1 h before expiry.
  - encrypts the JSON payload per RFC 8291 (`aes128gcm`, one record, record size 4096) with an
    ephemeral P-256 key, the subscription's `p256dh` and `auth`.
  - posts with `httpx.AsyncClient`: headers `Authorization: vapid t=<jwt>, k=<public key>`,
    `Content-Encoding: aes128gcm`, `TTL` (Path ready 3600, Reminder 21600, Brief published
    86400), `Urgency: normal`, and `Topic` (the collapse key: `path-<id>`,
    `reminder-<local date>`, `brief-<id>`; base64url, ≤ 32 chars).
  - maps responses: `201` → delivered; `404`/`410` → **Device gone** (row deleted); `413` →
    payload too large (logged; cannot happen with our sizes); `429`/`5xx` → error, logged, no
    retry in v1.
- `NullPushSender` when `VAPID_PRIVATE_KEY` is unset: logs, sends nothing, records `outcome =
  "disabled"`. This is the kill switch.
- `InMemoryPushSender` for tests. In e2e mode (`scripts/e2e_backend.py`) it records every send at
  `GET /__e2e__/pushes`, as iOS TDD §13.4 planned.

`services/notifications.py` composes preferences, Devices and the sender. Every send is guarded:
a failure is logged and never fails the request or job that caused it. Each send emits a
`notification_sent` product event (`kind`, `outcome`), and a Device deleted as gone emits
`device_removed` (`reason = "gone"`).

**Settings (`config.py`, `deploy.md`).** `VAPID_PRIVATE_KEY` (Fly secret, PEM or raw base64url),
`VAPID_PUBLIC_KEY` (env; derived from the private key if absent), `PUBLIC_BASE_URL` (env in
`fly.toml` as `https://aleph.mattjmcnaughton.com`; required when `ENV=production`, enforced by a model validator alongside the existing
production guards). A `just vapid-keys` recipe prints a fresh pair.

### 8.5 Payload (D4)

```json
{
  "web_push": 8030,
  "notification": {
    "title": "aleph",
    "body": "Your path on Linear algebra is ready.",
    "navigate": "https://aleph.mattjmcnaughton.com/paths/<id>",
    "tag": "path-<id>",
    "lang": "en"
  }
}
```

Copy and targets per kind are iOS TDD §13.5's (Path ready → path view; Reminder → resume
target's lesson, else `/`; Brief published → `/briefs/<id>`), with "Tap to retry" kept for a
failed path. `domains/notification_copy.py` holds the copy as a pure function of kind and inputs,
unit-tested. The Reminder's resume target is computed server-side from the same rule the web's
`components/continue-card.tsx` uses; the ticket moves that rule to the server if it is still only
on the client (CLAUDE.md prefers rules that exist once).

### 8.6 Service worker (`public/sw.js`, D2, D3)

```js
self.addEventListener("push", (event) => {
  const msg = parse(event.data);           // declarative JSON, or null
  if (!msg) return;
  const { title, body, navigate, tag, lang } = msg.notification;
  event.waitUntil(self.registration.showNotification(title, { body, tag, lang, data: { navigate } }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = safeSameOrigin(event.notification.data?.navigate) ?? "/";
  event.waitUntil(openOrFocus(url));       // focus an existing client and navigate it, else openWindow
});
```

- `parse`, `safeSameOrigin` (rejects any URL whose origin is not `self.location.origin`) and the
  client-selection part of `openOrFocus` live in `src/lib/sw-logic.ts` and are unit-tested in
  Vitest; a tiny build step copies them into `sw.js`, or `sw.js` is itself built by Vite as a
  second entry with a fixed output name. The ticket picks whichever keeps `sw.js` at the site
  root with a stable name.
- No `fetch`, `install` caching or `activate` cleanup beyond `self.skipWaiting()` and
  `clients.claim()`.
- Registered from `main.tsx` when `"serviceWorker" in navigator`, after first render, never
  blocking it.

### 8.7 Client: permission and subscription

`lib/push.ts` owns everything browser-side behind a small interface so it can be faked:

```ts
interface PushPlatform {
  permission(): NotificationPermission;                 // "default" | "granted" | "denied"
  requestPermission(): Promise<NotificationPermission>; // must be called from a tap
  getSubscription(): Promise<PushSubscriptionJSON | null>;
  subscribe(vapidPublicKey: string): Promise<PushSubscriptionJSON>;
  unsubscribe(): Promise<void>;
}
```

A pure state machine, `pushState({installState, configured, permission, hasDevice})`, yields
the settings screen's state: `hidden` (no push-config), `needs_home_screen`, `unsupported`,
`off` (permission default), `on`, `blocked` (denied). Unit-tested as a table.

- **Turning on** (from the pre-prompt or the settings button): `requestPermission()` inside the
  click handler → if granted, `subscribe()` → `POST /devices` → store the returned id in
  `localStorage` (convenience only; the server is the truth) → `on`.
- **Pre-prompt moments** (PRD §5.4): when the learner turns on the Reminder toggle while `off`,
  and once after their first path is created (`routes/new.tsx` success). Shown only in
  `home_screen` or `browser_ok` states with permission `default`. "Not now" is remembered per
  browser and the first-path pre-prompt is never shown twice.
- **Re-registration (D9):** on app start and on `visibilitychange` to visible, if permission is
  `granted`: get or re-create the subscription and `POST /devices`. Debounced to once per 10
  minutes.
- **Sign-out:** `useLogout` (`components/use-logout.ts`) first `DELETE`s this browser's Device
  (if an id is stored) and `unsubscribe()`s, each best-effort, then calls `/auth/logout`.
- **Blocked:** the settings screen shows that Notifications are off for aleph and, on iPhone,
  "Settings → Notifications → aleph". Never prompts again.

### 8.8 Notifications settings screen

`routes/settings.notifications.tsx`, linked from the account menu in `components/app-header.tsx`.
Sections: *This device* (the state from §8.7 with its one action) and *What to send* (Reminder
toggle and hour, Path ready, Brief published), read and written through the preference routes
with TanStack Query. The preference toggles are visible and editable even when this device is
`off`, `blocked`, `unsupported` or `needs_home_screen`, since they apply to all the
learner's Devices. `needs_home_screen` also shows install instructions and explains
that this browser cannot receive Notifications yet (owner-approved option 2).
T7b renders Path ready and Brief published; T9 owns Reminder controls and pre-prompt.
Breadcrumbs back to Your paths.

### 8.9 Path ready (ticket 7)

Hook the three fenced outline settlements in `services/generation.py` that emit
`outline_generated` (ready, refused, failed; around `generation.py:517`). After the settlement
commits, call `notifications.path_ready(path)` as a guarded fire-and-forget. Lesson generation
never notifies.

## 9. Reminders and Brief published (ticket 9)

- **Reminders:** iOS TDD §14.2 exactly, with one change: "at least one Device" now counts Web
  Push Devices. Row in `reminder_sends` written before delivery; `>=` the chosen hour; no send on
  an Active day; streak wording when a Daily streak is at stake.
- **Brief published:** after `BriefingService._persist_published` succeeds
  (`services/briefing.py`), for job runs, `POST /beats` and retry alike. Never for Skipped.

## 10. The scheduled job (ticket 8)

Copied from iOS TDD §14 in full, so this plan does not depend on a parked document. Nothing
below is new except that Notifications go through the Web Push sender instead of APNs.

- **Entry point:** `python -m aleph.jobs.tick` (`src/aleph/jobs/tick.py`) builds settings, engine,
  push sender and the briefing runtime as `services/lifecycle.py` does, then runs **Reminders**
  then **Beats**, each in its own `try` and under `asyncio.timeout(JOB_STEP_TIMEOUT_SECONDS)`
  (default 1500). Exit code is non-zero if either step failed. Logs `job_tick_started` /
  `job_tick_finished` with counts.
- **Beats:** `GET /beats` and `GET /beats/{id}` stop calling `_drain`
  (`routers/v1/beats.py:267`, `:360`) and become plain reads. `POST /beats` claims only the Beat
  it created. Each tick lists claim-eligible Beats across accounts (with a `LIMIT`), and for each:
  local today from the stored zone (UTC if none); skip unless `is_claimable` (`domains/cadence.py`);
  skip if idle (latest published Brief unread); check the daily research cap; claim with the
  existing atomic `claim_research`; `run_research` under a semaphore of
  `MAX_CONCURRENT_GENERATIONS`.
- **No overlap:** a scheduled Machine is one machine, so ticks never overlap each other. A web
  request racing the job is handled by the same atomic claims and unique constraints as two
  racing requests.
- **"Already ran today" is durable, per Beat per learner-local day** (iOS TDD D15): a run that
  published or skipped wrote an entry dated local today, which closes the cadence floor until the
  next Anchor day; a failed or refused run leaves a state only `POST /beats/{id}/retry` re-claims;
  a killed run is stale-recovered by the existing timeout and re-claimed by a later tick. No
  job-level run table.
- **Schedules file.** `fly.toml` cannot declare schedules (verified in the flyctl source, iOS PRD
  §5.14), so `deploy/schedules.toml` does:

  ```toml
  # Each entry becomes one Fly scheduled Machine, recreated on every deploy.
  [[schedule]]
  name = "tick"
  schedule = "hourly"                    # fly machine run --schedule: hourly | daily | weekly | monthly
  command = ["python", "-m", "aleph.jobs.tick"]
  memory = "512mb"
  ```

- **Recreated on every deploy.** After `flyctl deploy` succeeds, a new step in
  `.github/workflows/release.yml` runs `scripts/recreate_schedules.py`, which:
  1. resolves the image the deploy just released (the exact flyctl call is fixed in the ticket
     and documented);
  2. lists machines (`flyctl machine list --json`) and destroys every one whose metadata has
     `aleph_schedule` set, and nothing else;
  3. for each entry runs
     `flyctl machine run <image> <command…> --schedule <schedule> --restart no --metadata aleph_schedule=<name> --vm-memory <memory> --region <primary_region>`,
     passing `fly.toml`'s non-secret `[env]` block as `--env` flags. Verify in the ticket that
     `machine run` does not read `[env]` itself: without `ENV=production` the production guards
     in `config.py` would be off. App secrets reach every machine of the app.

  It is idempotent (a rerun destroys and recreates the same set) and runs inside the existing
  `deploy-aleph-prod-mattjmcnaughton` concurrency group. The `release_command` has already
  migrated the database by then. A failed recreate fails the deploy job visibly, so a schedule
  is never left silently running an old image.
- **Running a tick by hand:** `flyctl machine run <image> python -m aleph.jobs.tick --rm`
  (documented in `deploy.md`). Locally: `uv run python -m aleph.jobs.tick` against the dev
  database, with `NullPushSender` unless VAPID keys are set.
- **Cost:** Fly bills the job machine per second of run time only. **Neon wakes hourly** (a few
  minutes of compute per wake before it suspends), the main standing cost, accepted. Model spend
  is bounded by the cadence floor, the idle-Beat rule and the existing daily research cap.
- **Tests (fake clock, `InMemoryPushSender`):** Beats run once per local Anchor day across 24
  ticks; a westward learner's Monday Beat runs on their Monday; idle Beats are skipped; a killed
  run is stale-recovered and produces one Brief; a published Brief sends one Notification and a
  Skipped period none; `GET /beats` claims nothing. `tests/integration/test_beats_api.py` and
  `test_briefing.py` are rewritten to drive the job step instead of the GET drain.
- **Docs:** `deploy.md` gains *Scheduled machines*; `api.md`'s analyst section ("The arrival
  drain") is amended; CONTEXT.md's **Anchor day** and **Brief prefetch** entries already describe
  the job.

## 11. Testing plan

**Backend unit.** Endpoint allowlist; key validation; notification copy; VAPID JWT claims;
RFC 8291 Appendix A encryption vector; response-code mapping; time zone validation; `client`
mapping; session `max_age`; `serve.py` media type and `sw.js` cache header.

**Backend integration** (real Postgres). Device upsert moves between Accounts; `DELETE` of another
Account's Device is `404`; a `410` from the fake sender deletes the Device; preferences default
and round-trip; Path ready sends once per settlement and respects its toggle; Reminder and job
tests (§9, §10); time zone written only on change.

**Web unit (Vitest).** `shouldShowInstallCard` and `installState` tables; `pushState` table; the
turn-on flow against a fake `PushPlatform` (granted, denied, subscribe throws); re-registration
debounce; sign-out order; `sw-logic` parse and same-origin rules; the settings screen with MSW.

**Playwright (phone viewport, stub backend).** A fake `PushPlatform` is installed through
`page.addInitScript` setting `window.__alephPushPlatform`, which `lib/push.ts` uses in place of
the browser's APIs when present. This is a fake of the platform boundary, not a mock of app
internals. Real browser push is not exercised.
- **W38:** turn on Notifications → `POST /devices` recorded → create a path → stub settles the
  outline → `GET /__e2e__/pushes` has one Path ready with the path's URL → `page.goto` that URL
  shows the path.
- **W39:** turn on the Reminder at an hour → preference stored (the scheduling half is the
  backend integration test).
- **W40:** trigger the job's Beats step through an e2e-only route → one Brief published recorded
  → its URL opens the Brief reader.
- **W34:** the Flow journey renamed from W32 (ticket 3).

**Release checklist (manual, owner's iPhone).** Add to Home Screen; icon and splash correct;
sign in inside the Home Screen app; leave and reopen after force-quit (still signed in); turn on
Notifications; receive and tap one Notification of each kind shipped so far; turn off in system
Settings and confirm the settings screen shows *blocked*. Recorded in `docs/deploy.md`.

## 12. Tickets (build order)

Each ticket is one PR unless it says otherwise. **Commit** gives the main commit's type; a
learner-visible change is `fix` or `feat` so it deploys.

**Ticket 0 — Spike: sign-in and push inside the Home Screen app (go / no-go).** *Type:* throwaway
branch, never merged. *Scope:* a minimal manifest and icon; a 20-line `sw.js`; a hard-coded admin
route that sends one Web Push to the caller's subscription with a declarative payload (any
library is fine here); deploy to production behind an env flag or to a scratch Fly app pointed at
the production Auth0 tenant's callback. On the owner's iPhone (iOS 26), from `https://aleph.mattjmcnaughton.com`: add to Home Screen, sign
in, force-quit and reopen, subscribe, receive, tap. *Acceptance:* findings written into a new
§13 of this document: did the OIDC redirect stay inside the Home Screen app; which Auth0
connections work; did the declarative payload display with and without the service worker's
`push` handler; did `navigate` open the app at the URL. *Gate:* if sign-in cannot be made to
work, stop and propose un-parking the native app.

**Ticket 1 — Park the iOS plan and adopt this one (docs).** *Type:* docs. *Commit:* `docs:`.
*Scope:* a **Parked** banner atop `docs/prds/ios-app.md` and `docs/tdds/ios-app.md` pointing here;
rewrite CLAUDE.md's *Two Clients* section to a short note (one client; native parked; its rules
return with it) and drop the `(ios)` scope guidance (the push exception and CONSTITUTION.md §8
were already amended alongside this TDD); roadmap entry; CONTEXT.md **Home Screen app**,
**Notification**, **Device**, **Learner time zone** wording (§8.1); mark this PRD and TDD
accepted. *Acceptance:* no live doc tells a contributor to touch `clients/ios/`. *Depends on:* 0.

**Ticket 2 — Installable.** *Type:* web + backend serving. *Commit:* `feat(web):`. *Scope:* §3,
§4. *Acceptance:* the production image serves `/manifest.webmanifest` as
`application/manifest+json` and `/sw.js` with `no-cache` (extend `just compose-smoke`); the
install card shows only for iPhone Safari outside the Home Screen app; every route has a visible
way back; release checklist items 1–2 pass on the iPhone. *Tests:* §11's install and serving
tests. *Note:* registers an empty `sw.js` (push handlers arrive in ticket 7) so the install
behaves like the final one.

**Ticket 3 — Flow on by default; Flow journey W32 → W34.** *Type:* backend + e2e. *Commits:*
`fix(flags):`, `test(e2e):`. *Scope:* carried verbatim from iOS TDD ticket 3's last two bullets:
flip `FLAG_DEFAULTS[FeatureFlag.FLOW]` to `True` in `services/feature_flags.py` with its unit pin;
rename `w32.spec.ts` → `w34.spec.ts` and its tag; update the Flow TDD, `docs/ci.md`, and
CONTEXT.md's Flow phase note; remove the production override once deployed. *Independent.*

**Ticket 4 — Delete Beat on the web.** *Type:* web. *Commit:* `feat(beats):`. *Scope:* the web
half of iOS TDD ticket 11: a not-undoable confirm on `routes/beats.$beatId.tsx` calling the
existing `deleteBeat` helper; MSW test; an e2e step in W29's journey; update the comment in
`scripts/e2e_backend.py` that says no deletion control exists. *Independent.*

**Ticket 5 — Session lifetime.** *Type:* backend. *Commit:* `fix(auth):`. *Scope:* §5.
*Acceptance:* `Set-Cookie` carries `Max-Age=7776000`; the comment in `app.py` states the
trade-off. *Depends on:* owner confirming PRD P3.

**Ticket 6 — Learner time zone and the `client` dimension.** *Type:* backend + web. *Commits:*
`feat(api):` (migration 0017, `services/time_zone.py`, `tzdata`, `client` ContextVar and
`EVENT_FIELDS`), `feat(web):` (`lib/client-headers.ts` on all three call sites). *Scope:* §6, §7;
`api.md` conventions; `metrics.md`. *Acceptance:* a valid zone is stored once and not rewritten
when unchanged; an invalid one changes nothing and the request succeeds; events from a
standalone-header request carry `home_screen`, a plain cookie request `web`, a job none.
*Tests:* as listed in §11.

**Ticket 7 — Push infrastructure, Notifications settings and Path ready.** *Type:* backend + web.
*Commits:* `feat(push):` (migration 0018, routes, allowlist, `WebPushSender`, `NullPushSender`,
`InMemoryPushSender`, `notifications.py`, Path ready hook, settings and `just vapid-keys`),
`feat(web):` (`sw.js` handlers, `lib/push.ts`, `lib/sw-logic.ts`, pre-prompts, settings screen,
sign-out order, re-registration). *Scope:* §8. *Acceptance:* W38 passes; a non-allowlisted
endpoint is `422`; a `410` deletes the Device; without VAPID secrets nothing is sent, nothing
fails, and the web hides every Notifications control; release checklist: a real Path ready
Notification arrives on the iPhone and opens the path. *Docs:* `api.md` (four routes),
`deploy.md` (secrets, key generation, `PUBLIC_BASE_URL`), CONTEXT.md already updated in ticket 1.
*Depends on:* 2, 6. *Size:* the largest ticket; split backend and web into two PRs if review gets
heavy (backend first; it is inert until the web half lands).

**Ticket 8 — The scheduled job and Beats moved into it.** *Type:* backend + deploy. *Commits:*
`feat(jobs):` (tick, schedules file, `recreate_schedules.py`, release workflow step,
`deploy.md` *Scheduled machines*), `feat(beats):` (GET drains removed, `POST /beats` claims only
its own Beat, the Beats step). *Scope:* §10. *Acceptance:* every §10 test passes; a deploy leaves
exactly the scheduled machines `deploy/schedules.toml` names, on the new image; `api.md`'s
analyst section amended. *Depends on:* 6 (stored zone). Does not need push.

**Ticket 9 — Reminders and Brief published.** *Type:* backend + web. *Commits:* `feat(push):`
(Reminder step in the tick, Brief published hook, copy), `feat(web):` (Reminder toggle and hour on
the settings screen, the Reminder pre-prompt). *Scope:* §9. *Acceptance:* W39, W40 pass; the
§14.2-derived Reminder tests pass (one per local day, late tick catches up, never yesterday's, DST
skip still fires, killed run never double-sends); release checklist: one real Reminder and one real
Brief published on the iPhone. *Depends on:* 7, 8.

**Dependencies:** 0 → 1 → (2, 3, 4, 5 in any order) ; 6 after 1 ; 7 after 2 and 6 ; 8 after 6 ;
9 after 7 and 8. Tickets 3 and 4 do not depend on ticket 0 and can start now.

## 13. Spike findings

*Empty until ticket 0 runs.*

## 14. Risks

- **Sign-in inside the Home Screen app** (PRD R1) — ticket 0.
- **Silent subscription loss on iOS** (PRD R2) — D9 re-registration; `device_removed` and
  re-registration counts in `metrics.md`.
- **Hand-written encryption** (D6) — bounded by the RFC 8291 test vector and a live send in
  ticket 0 and the release checklist; `pywebpush` is the fallback.
- **The job** — inherited from iOS TDD §19: a scheduled machine on a stale image, `[env]` not
  reaching `machine run`, Brief spend without arrival as a bound, Neon waking hourly. Same
  mitigations.
- **Path ready on a stopped machine** — outline generation runs in the web process; if Fly
  stops the machine mid-generation, stale recovery finishes it on the next visit and the
  Notification is late. Accepted, as in the iOS TDD.

## 15. Open items

1. **Owner decisions** PRD P1–P6.
2. ~~**`PUBLIC_BASE_URL`**~~ *Resolved:* `https://aleph.mattjmcnaughton.com` (D15).
3. **Auth0 connections** in production (PRD §11.3), answered by ticket 0.
4. **Later, not v1:** an app icon badge through the Badging API; a Devices list in settings;
   retrying `429`/`5xx` sends; Brief prefetch on the job.
