# TDD — Public profiles

**Status:** Accepted 2026-11-24. Built 2026-11-24 and shipped on (`public_profiles` defaults on; nothing is public until a learner opts in). · **Owner:** solo builder
**Companion to:** [Public profiles PRD](../prds/public-profiles.md)
**Design:** Storybook `Screens/Public profiles`
([`public-profiles.stories.tsx`](../../src/aleph/web/frontend/src/stories/public-profiles.stories.tsx)),
the built screens. They replaced the concept storyboard the design was decided on.
**Security review:** [design review thread](https://ampcode.com/threads/T-01a20da5-5f85-70ed-a3df-6d63b7f21f8b)
of `main` at `ea0cd2f`. It found no vulnerability in current code. Its findings are
folded in below and marked **(SR n)**.
**Vocabulary:** *Public profile*, *Handle*, *Shared*, *Copy* (defined in [`CONTEXT.md`](../CONTEXT.md)).

**As built, where it differs from the text below:**
- The public Brief page is `/@$handle/beats/$beatId/briefs/$briefId`, not
  `/@$handle/briefs/$briefId`: the public API checks the chain through the Beat,
  so the client needs the Beat id.
- `PUT /account/public-profile` adds `422 handle_required` for turning the
  profile on without a Handle. `handle_rejected` carries `details.reason`.
- `require_same_origin` also rejects a cookie write that sends neither `Origin`
  nor `Referer`. Besides the request's own origin it accepts `PUBLIC_BASE_URL`
  and the new `CSRF_TRUSTED_ORIGINS` setting; outside production it also trusts
  the Vite dev server's origin, which its proxy does not rewrite.
- `public_profiles` defaults on (D9, owner change 2026-11-24): there is no admin dogfood step.

## 0. Owner decisions (agreed 2026-11-24)

1. **Constitution §1 (SR 1, SR 4).** Amended on 2026-11-24. Anonymous requests may
   read an explicitly published projection; every mutation and unpublished
   resource stays owner-scoped. No shared cache may store a response carrying
   account or session data, or keep a projection after it is withdrawn.
2. **Sharing is live (SR 2).** The settings page says so before A shares
   anything:
   - A shared path shows its current Teaching style, and a shared Beat publishes
     future Briefs as they arrive. A copy prefills from the current values.
   - The activity strip is account-wide, so it reflects lessons on private paths
     too. It has its own checkbox ("across all your paths").
   - Lesson titles are generated and can echo About you or Guidance. "View as a
     visitor" is the preview, and the settings note says to check it.
3. **Handles are permanent** in the first slice (D8).
4. **No per-IP rate limit** on public reads (§8).

## 1. Decision record

| # | Decision | Why |
| - | -------- | --- |
| D1 | A copy carries **creation inputs only**. Nothing on the server copies a row. | B's submit is the only write (Constitution §2). There is no copied structure to keep consistent. |
| D2 | "Start this path" links to `/new?from=<handle>/<pathId>`. The form fetches the inputs from the public API. | Guidance stays out of URLs and history. The form gets a source ID for the credit. |
| D3 | Public reads use a **separate router, repository queries and DTOs**. No public code calls `get_owned_path`, `_owned_beat` or the signed-in DTOs. | A field added to a private DTO later cannot leak (SR 3). |
| D4 | Every public read checks the **whole chain** in one query: handle → profile enabled → item owned by that user and shared → child (Brief, Sources). | Checking only the parent and then fetching a child by ID would make the check meaningless (SR 3). |
| D5 | Uniform `404 not_found` for every absent case, **at the API**. The SPA document is still `200` (`web/serve.py`). | Making the document 404 needs a server-side existence check; not worth it in the first slice (SR 10). |
| D6 | Sign-in returns to the copy through a **typed copy intent**, not a raw `next`. | Removes the open-redirect class instead of validating URLs (SR 6). |
| D7 | Credit (`source_path_id`, `source_beat_id`) is **non-authoritative**. It is silently dropped when unavailable and shown only while the source is public. | Anyone can send a public ID with unrelated inputs, so the credit proves nothing (SR 7). |
| D8 | **Handles cannot be changed or released** in the first slice. | A freed handle would pass the old owner's links to whoever claims it (SR 11). Turning the profile off hides it; the handle stays reserved. |
| D9 | A new `public_profiles` flag, resolved for the **profile owner**, **on by default** (like `flow`). | Shipping on exposes nothing: every profile and every item's `shared` start false, so nothing is public until a learner opts in. The flag stays as the kill switch: `FEATURE_FLAG_DEFAULTS=public_profiles:off` makes every profile 404 and hides the owner routes. |

## 2. Data model (migration `0025_public_profiles`)

- `users.handle`: `text`, nullable, **unique**, `CHECK` against
  `^[a-z0-9-]{3,30}$`. Written once (D8). A reserved list (`admin`, `aleph`,
  `support`, `staff`, `api`, `auth`, `login`, `new`, `settings`, …) is rejected
  in the domain rule.
- `users.profile_public`: `bool`, default `false`.
- `users.profile_show_activity`: `bool`, default `true`.
- `paths.shared` and `beats.shared`: `bool`, default `false`.
- `paths.source_path_id` → `paths.id`, `beats.source_beat_id` → `beats.id`:
  nullable, `ON DELETE SET NULL`.
- Indexes: the unique index on `handle`, plus partial indexes
  `(user_id) WHERE shared` on `paths` and `beats`.

Turning the profile off keeps each item's `shared` value for next time
(PRD §4.1).

## 3. Domain (`domains/public_profiles.py`, pure)

- `validate_handle(raw) -> Handle | HandleRejected` (`too_short`, `too_long`,
  `bad_characters`, `reserved`).
- `credit_for(source) -> Credit | None`: the source still exists, is shared, and
  its owner's profile is on; otherwise `None`.

## 4. API

### Public (no auth dependency)

All are `GET`, under `routers/v1/public.py`, mounted at `/api/v1/public`.

| Route | Returns |
| ----- | ------- |
| `/profiles/{handle}` | Display name, handle, member since, optional activity (streak, best, 49-day strip), shared paths (title, Level, `{done,total}`, path streak), shared Beats (topic, cadence, latest Brief title, Brief count). |
| `/profiles/{handle}/paths/{path_id}` | Title, Topic, Level, Guidance, Teaching style, units with lesson titles and complete/not-complete, A's position. |
| `/profiles/{handle}/beats/{beat_id}?cursor=` | Topic, Level, Guidance, cadence, Briefs (number, date, title, Source count), paginated 20 at a time. |
| `/profiles/{handle}/beats/{beat_id}/briefs/{brief_id}` | Brief Markdown body and Sources. Published Briefs only, never Skipped rows. |

These endpoints must not do any of the following:
- **Read the session or bearer token.** No `get_optional_current_user`, so they
  never renew or set a cookie. A signed-in visitor gets the same response as an
  anonymous one (SR 4).
- **Return private IDs.** Responses contain no user IDs, emails or IDs of
  unshared items. Progress is nested under the shared path it belongs to, unlike
  the signed-in summary, which lists `path_id`s (SR 3).
- **Write anything.** No read pings, no events tied to an account, no generation
  (SR 9, 11).

**Caching (Constitution §1).** One middleware sets `Cache-Control: no-store` on
every `/api/*` and `/auth/*` response that has not set its own `Cache-Control`.
That covers the session probe (which carries email and flags) and every existing
signed-in response, not only the new ones; it closes a gap the 2026-11-24
amendment makes explicit. A test asserts it on the session probe, a signed-in
read and a public read.

Headers on every public response, including 404:
`Cache-Control: no-store`, `X-Robots-Tag: noindex`. `GET /api/v1/auth/session`
also gets `Cache-Control: private, no-store` (SR 4). `noindex` is advisory; the
access control is D4.

Every public response DTO lives in `dtos/public.py`. A test pins the exact key set
of each DTO, so adding a field is a deliberate change to that test.

### Owner (authenticated, owner-scoped)

| Route | Does |
| ----- | ---- |
| `GET /api/v1/account/public-profile` | Handle, `profile_public`, `profile_show_activity`. |
| `PUT /api/v1/account/public-profile` | Sets the three fields. The handle can be set only while it is `NULL` (`409 handle_taken`, `409 handle_locked`, `422 handle_rejected`). |
| `PUT /api/v1/paths/{id}/shared`, `PUT /api/v1/beats/{id}/shared` | `{shared: bool}` through the existing `get_owned_path` / `_owned_beat` (404 for another learner's item). |

**CSRF (SR 5).** No route checks `Origin` today; the cookie's `SameSite=Lax` is
the only defense. These writes publish data, so they get one: a dependency
`require_same_origin` that rejects a cookie-authenticated non-GET request whose
`Origin` (or `Referer`, if `Origin` is absent) is not the app's own origin.
Bearer requests skip it. Applying it to every existing mutation is a sensible
follow-up but outside this slice.

### Create requests gain a credit field

`CreatePathRequest.source_path_id` and the Beat create request's
`source_beat_id` are optional UUIDs. Inside the create transaction the service
looks the source up with the public chain from D4. If it isn't public, the field
is dropped and the create succeeds exactly as without it (D7). Nothing else comes
from the source: not the inputs, not the models, not web search settings, not the
Anchor day. B's quotas, flags and the analyst kill switch apply as usual.

`PathDetailResponse` and the Beat detail gain
`source: {display_name, handle, path_id} | null`. It is resolved on every read
with `credit_for`, so it disappears when the source is unshared.

## 5. Sign-in return (SR 6)

1. The copy card links to `GET /auth/login?copy=path:<handle>:<uuid>`
   (or `beat:`).
2. `login` parses the value against that grammar and stores
   `{kind, handle, id}` in the session next to Authlib's OIDC state. Anything
   that doesn't parse is ignored.
3. `auth_callback` pops the intent only after a successful token exchange and
   builds the destination itself: `/new?from=<handle>/<id>` or
   `/beats/new?from=…`. With no intent, or a failed sign-in, it behaves as today
   (`/`, or `/login?error=auth_failed`).

There is no free-form URL anywhere in this flow. The intent is single-use.

## 6. Frontend

- **Routes:** `/@$handle`, `/@$handle/paths/$pathId`, `/@$handle/beats/$beatId`,
  `/@$handle/briefs/$briefId`. `authRedirect` lets exactly these shapes through
  unauthenticated (SR 10). The root layout renders a public header on them
  (brand, and Sign in or the account initial) and skips `PushLifecycle`.
- **Query keys** start with `["public", …]`, separate from the private cache.
- **Rendering (SR 9):**
  - Learner text (Guidance, Teaching style, titles) renders as plain text, never
    Markdown.
  - Brief bodies use `Markdown` with Mermaid and Visual fences rendered as plain
    code blocks.
  - Source links render only when the URL parses as absolute `http:` or `https:`
    with a host; otherwise the URL is shown as text. This fix belongs in
    `brief-sources.tsx` for signed-in Briefs too, since `href={source.url}` is
    raw there today.
  - The public Brief view fires neither the `opened` nor the `sources` read
    ping, even when A views their own Brief.
- **New path `from` (D2):** `validateSearch` accepts `from` as
  `<handle>/<uuid>`. The form fetches the public path once, fills only empty
  fields, labels them "From Matt McNaughton's path", sends `source_path_id`, and
  never submits on its own (SR 8). If the fetch 404s, the form opens empty with a
  one-line note. New beat does the same and gains `guidance` prefill.
- **Settings → Public profile:** frames 1–2 of the storyboard, plus "View as a
  visitor", which opens the public route.

## 7. Prompt injection (SR 8)

A's Guidance and Teaching style become B's generation inputs. This is the same
trust level as anything B types: B sees every field, can edit it, and must
submit. The existing tag-forgery guard and output validation apply. Nothing B
generates goes back to A. The remaining risk is that hostile text steers B's own
outline. That is a content quality issue, not an access issue, so it is accepted.

## 8. Load and cost

There is no per-IP limit, by the owner's choice. Bounds instead:
- every public query is indexed, and the Brief list is paginated;
- the profile shows at most 50 shared items;
- public GETs never start generation or research.

Latency and volume of `/api/v1/public/*` go on the existing request dashboards.
Scraping public content stays possible.

## 9. Testing

Backend tests run against real Postgres (integration), with two owners (A, C)
and an anonymous visitor:

- Shared items are readable; unshared items, items under another handle
  (`/profiles/a/paths/<C's shared path>`), sibling Briefs of an unshared Beat,
  and Skipped Briefs all return the same 404 body.
- Unsharing, turning the profile off, deleting, and the flag kill switch each
  close direct and nested reads on the very next request.
- Public responses carry `no-store` and `noindex` and no `Set-Cookie`, with and
  without a session cookie.
- DTO key sets are pinned.
- Copy: an unshared, deleted or foreign `source_path_id` creates the path
  without credit. A valid one credits until the source is unshared.
- Sign-in intent: malformed values are ignored; the intent is consumed once.
  `next=https://evil.example` does nothing.
- `require_same_origin` rejects a cross-origin cookie `PUT` and allows the same
  request with a bearer token.
- Handle rules, as pure domain tests.

Frontend: `authRedirect` lets only the public shapes through; the Source URL
filter; `from` prefill does not overwrite typed values. A Playwright test covers
PP3: anonymous visit → sign in → prefilled form → submit → drafting.

## 10. Not in this slice

Handle changes, a server-side 404 for public documents, CSRF on existing
mutations, per-IP limits, search indexing, and everything in PRD §6.

## 11. Tickets

0. Done: §1 amended, decisions in §0, CONTEXT.md terms added.
1. `no-store` middleware. Migration, domain rules, owner routes with
   `require_same_origin`.
2. Public router, queries and DTOs, with the chain and header tests.
3. Credit fields on create and detail.
4. Typed sign-in intent.
5. Frontend public routes, settings page, `from` prefill, Source URL filter.
6. Ship with `public_profiles` on by default. Release gate (security review) before merge: two owners and an anonymous visitor; unsharing closes direct, nested and cached reads; `no-store` and no `Set-Cookie` on public responses.
