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

`/mcp` accepts only Aleph's own tokens. Both kinds below are rows in `api_keys`
and are looked up by SHA-256 digest; nothing else is accepted, so an identity
provider's token is never passed through to `/api/v1` (MCP authorization spec,
"Token Passthrough").

- **OAuth connections** (the usual way). An MCP client such as Claude or Amp is
  given only the URL. Its first request gets `401` with
  `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource", scope="mcp"`
  and it runs the flow below. The result is a **connection**: an `api_keys` row
  with the client's `client_id`, listed under **Connected apps** in Settings →
  API keys and disconnected there.
- **API keys** for clients that can't sign in. A learner creates one under
  **Settings → API keys** (`/settings/api-keys`) and the client sends it as
  `Authorization: Bearer <key>`. This follows accanto's API keys: the key is
  `secrets.token_urlsafe(32)` (43 characters, no prefix), only its SHA-256
  digest is stored, the secret is shown once at creation, there is no expiry,
  and revoking stamps `revoked_at` and keeps the row, so revoked keys stay
  listed. A revoked key is `401` on its next request.
- **A token only works through `/mcp`.** Each tool forwards to an `/api/v1`
  route in-process, and the API accepts a key only on those forwarded calls.
  Sent straight to `/api/v1`, a key is `401`. A leaked token can therefore do
  what the tools do and nothing else: it can't delete a path, use admin routes
  or manage keys.
- **Key management is browser-session only.** `/api/v1/api-keys` and
  `/api/v1/oauth/consent` refuse any `Authorization` header with `403`
  (`details.reason: browser_session_required`), so no key, script or agent can
  list, create, revoke or approve.
- An unknown, revoked or expired token is `401` with `error="invalid_token"` and
  the same `resource_metadata`, so the client refreshes or signs in again. The
  session cookie is never read, so a browser can't drive these writes with
  ambient credentials.
- **The `mcp` feature flag** gates the server, the key routes, the consent page
  and the settings page per account. It's off by default and on for admins, and
  returns `404` when off. A token whose owner loses the flag gets `404` at
  `/mcp`; `/oauth/authorize` sends such a learner back to the client with
  `error=access_denied`.

### OAuth flow (Aleph as the authorization server)

Code: `routers/oauth.py`, `services/oauth.py`. It follows the MCP authorization
spec (2025-11-25) and OAuth 2.1 for public clients.

1. **Discovery.** `GET /.well-known/oauth-protected-resource` (also served at
   `/.well-known/oauth-protected-resource/mcp`) names `resource: <base>/mcp` and
   this server as `authorization_servers`.
   `GET /.well-known/oauth-authorization-server` advertises `S256` PKCE only,
   `token_endpoint_auth_methods_supported: ["none"]`,
   `client_id_metadata_document_supported: true`, the `authorization_code` and
   `refresh_token` grants and the single scope `mcp`. There is no
   `registration_endpoint`: dynamic client registration is optional in the spec
   and deprecated in its next revision, and Claude and Amp both use CIMD.
2. **Client identity: Client ID Metadata Documents.** The `client_id` is an
   HTTPS URL with a path; Aleph fetches it (public addresses only, no redirects,
   5 KB cap, 5 s timeout). The host is resolved once and the request connects
   to that checked address, with TLS verifying the real hostname, so DNS
   rebinding can't reach an internal address, requires its `client_id` to equal the URL, a
   `redirect_uris` list and a public client, and stores nothing per client. Any
   HTTPS client may ask; the learner decides on the consent page. A redirect URI
   must match exactly, except a loopback `http` URI may use any port (RFC 8252).
3. **`GET /oauth/authorize`** needs `response_type=code`,
   `code_challenge_method=S256` and a 43–128 character `code_challenge`; an
   optional `resource` must be `<base>/mcp` and `scope` only `mcp`. A signed-out
   learner signs in through the normal cookie flow first (`/auth/login?oauth=1`
   returns here). Until the client and redirect URI check out, errors go to
   `/connect?error=…` and never to the client; after that they go to the
   redirect URI with `state` and `iss`.
4. **Consent at `/connect`.** The validated request waits in the signed session.
   The page shows the client's name, the host publishing its metadata and the
   host it returns to, and warns when every redirect URI is loopback (any local
   program could claim the name). The page is sent with
   `Content-Security-Policy: frame-ancestors 'none'` and `X-Frame-Options: DENY`,
   so no site can frame it to trick a click on Connect. `POST /api/v1/oauth/consent {id, approve}`
   (same origin) answers it once; a stale `id` is `409`. Approving stores a
   one-time code (`oauth_codes`, SHA-256 only, 60 s) and returns
   `{redirect_to}`; at most 10 approvals per learner per hour (`429`).
5. **`POST /oauth/token`** (form-encoded). `authorization_code` redeems the code
   once (atomically, across machines), checks `client_id`, `redirect_uri`, the
   PKCE verifier and `resource`, and creates the connection. A code presented
   again is refused and revokes the connection it issued. The response is
   `{access_token, token_type: "Bearer", expires_in: 3600, refresh_token, scope}`
   with `Cache-Control: no-store`.
6. **Refresh.** `refresh_token` rotates both tokens on the same row (Settings
   keeps one entry per connection). The access token lasts an hour; the refresh
   token lasts 30 days from its last use. Presenting a replaced refresh token (a
   sign it was stolen) revokes the connection. Disconnecting in Settings ends
   access and refresh at once.

### Key routes

| Method | Path | Body | Success |
| ------ | ---- | ---- | ------- |
| `GET` | `/api/v1/api-keys` | — | `200 {keys: [{id, name, created_at, revoked_at}], connections: [{id, name, client_host, created_at}]}`, oldest first; keys include revoked ones, connections only live ones; never a secret or digest |
| `POST` | `/api/v1/api-keys` | `{name}` (stripped, 1–120 chars) | `201 {id, name, key}`; `key` is the secret, in this response only |
| `DELETE` | `/api/v1/api-keys/{id}` | — | `204`; revokes a key or disconnects a connection; a repeat keeps the first `revoked_at`; another learner's is `404` |

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

Give the client the URL `https://<host>/mcp`. Claude (custom connector, "Use
Claude's published identity"), Claude Code and Amp then open the browser to sign
in and approve. For a client that can't sign in, add a key as a header (the
settings page fills this in with a new key):

```json
{ "mcpServers": { "aleph": { "type": "http", "url": "https://<host>/mcp",
  "headers": { "Authorization": "Bearer <api key>" } } } }
```

## Not yet

There is no import of fully written paths (passages and Quick checks supplied
by the caller), no Attempt or completion tools, and no tutor or shaping
tools.
