# Content MCP server

Aleph serves a [Model Context Protocol](https://modelcontextprotocol.io) server
at `POST /mcp`, so an AI client (Claude, Amp, Cursor and others) can read a
learner's paths and lessons and create or edit paths for them. The code is in
`src/aleph/routers/mcp.py`, and the tests are in `tests/integration/test_mcp_api.py`.

## Transport

Streamable HTTP, **stateless and JSON-only**. Each JSON-RPC request gets one
`application/json` reply, and a notification gets `202`. There is no
`Mcp-Session-Id` and no server-sent stream (`GET /mcp` is `405`), and batches
are not accepted. It supports protocol versions `2025-11-25`, `2025-06-18` and
`2025-03-26`. `initialize` echoes a supported requested version and otherwise
offers the newest one. A `MCP-Protocol-Version` header naming an unsupported
version is `400`. Methods: `initialize`, `ping`, `tools/list`, `tools/call`.

## Authentication and gating

- **API keys** (the usual way). A learner creates one under **Settings → API
  keys** (`/settings/api-keys`) and the MCP client sends it as
  `Authorization: Bearer <key>`. This follows accanto's API keys: the key is
  `secrets.token_urlsafe(32)` (43 characters, no prefix), only its SHA-256
  digest is stored, the secret is shown once at creation, there is no expiry,
  and revoking stamps `revoked_at` and keeps the row, so revoked keys stay
  listed. A revoked key is `401` on its next request.
- **A key only works through `/mcp`.** Each tool forwards to an `/api/v1` route
  in-process, and the API accepts a key only on those forwarded calls. Sent
  straight to `/api/v1`, a key is `401`. A leaked key can therefore do what the
  tools do and nothing else: it can't delete a path, use admin routes or manage
  keys.
- **Key management is browser-session only.** `/api/v1/api-keys` refuses any
  `Authorization` header with `403` (`details.reason: browser_session_required`),
  so no key, script or agent can list, create or revoke keys.
- An **OIDC access token** for `OIDC_API_AUDIENCE` also works. That stays off in
  production while the audience is unset. With no `Authorization` header the
  reply is `401` with
  `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource"`,
  and the session cookie is never read, so a browser can't drive these writes
  with ambient credentials. The metadata route is `404` while the audience is
  unset.
- **The `mcp` feature flag** gates the server, the key routes and the settings
  page per account. It's off by default and on for admins, and returns `404` when
  off. A key whose owner loses the flag gets `404` at `/mcp`.

### Key routes

| Method | Path | Body | Success |
| ------ | ---- | ---- | ------- |
| `GET` | `/api/v1/api-keys` | — | `200 {keys: [{id, name, created_at, revoked_at}]}`, oldest first, revoked included; never a secret or digest |
| `POST` | `/api/v1/api-keys` | `{name}` (stripped, 1–120 chars) | `201 {id, name, key}`; `key` is the secret, in this response only |
| `DELETE` | `/api/v1/api-keys/{id}` | — | `204`; a repeat keeps the first `revoked_at`; another learner's key is `404` |

Writes check `Origin` like other cookie writes that hand out access. Don't
retry an uncertain create automatically: the secret can't be recovered, so
reload the list and revoke any key you don't want.

## Tools

Every tool forwards in-process to an `/api/v1` route with the caller's own
token. Ownership (`404` for another learner's path), validation, rate limits,
the poll-as-trigger resume and the answer-hiding rule are therefore exactly the
web client's. An `/api/v1` error comes back as a tool result with
`isError: true` and text like `422 validation_error: …`. A successful JSON tool
returns the route's body as `structuredContent` and as JSON text.

| Tool | Route | Writes |
| ---- | ----- | ------ |
| `list_paths` | `GET /api/v1/paths` | no |
| `get_path` | `GET /api/v1/paths/{path_id}` | no (may trigger generation, as the web poll does) |
| `get_lesson` | `GET /api/v1/lessons/{lesson_id}` | no (same) |
| `export_path` | `GET` of the path and each generated lesson | no |
| `create_path` | `POST /api/v1/paths` | yes; counts against the daily path limit |
| `rename_path` | `PATCH /api/v1/paths/{path_id}` | yes; display title only |
| `set_teaching_style` | `PUT /api/v1/paths/{path_id}/teaching-style` | yes; forward-only |
| `get_about_you` | `GET /api/v1/about-you` | no |
| `set_about_you` | `PUT /api/v1/about-you` | yes; forward-only |

The admin model picker overrides are not exposed. `create_path` returns
`{id}` immediately, and the client polls `get_path` until `status` is
`ready`, `failed` or `refused`.

**`export_path`** returns one Markdown document. It contains the title, Topic,
Level and Teaching style, then each unit and lesson in order. A generated
lesson has its Read passage (with Visual fences removed and each Visual's alt
text quoted) and its Quick check. An ungenerated lesson says *Not generated
yet*, because lessons are generated a few ahead of the learner. **A Quick
check's correct option and explanation appear only after the learner's
Attempt** (W6), exactly as `GET /lessons/{id}` reveals them. Only fields the
exporter names are written, so a field the lesson API adds later is not
exported by accident.

## Connecting a client

Add the key as a header (the settings page fills this in with a new key):

```json
{ "mcpServers": { "aleph": { "type": "http", "url": "https://<host>/mcp",
  "headers": { "Authorization": "Bearer <api key>" } } } }
```

## Not yet

There is no import of fully written paths (passages and Quick checks supplied
by the caller), no Attempt or completion tools, and no tutor or shaping
tools.
