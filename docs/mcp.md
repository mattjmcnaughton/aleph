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

- **Bearer only**, through the same `aleph/bearer.py` validation as `/api/v1`
  (see [api.md](api.md#authentication)). With no `Authorization` header the
  reply is `401` with
  `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource"`,
  and the session cookie is never read. This means a browser cannot drive these
  writes with ambient credentials.
- `GET /.well-known/oauth-protected-resource` (RFC 9728) names `OIDC_ISSUER` as
  the authorization server. It is `404` while bearer auth is off.
- **Bearer auth is off until `OIDC_API_AUDIENCE` is set** (production doesn't set
  it today), so every `/mcp` request fails `401` until it is configured.
  `docs/deploy.md` covers the Auth0 API setup.
- **The `mcp` feature flag** gates the whole server per account. It is off by
  default and on for admins, and returns `404` when off.

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

```json
{ "mcpServers": { "aleph": { "url": "https://<host>/mcp" } } }
```

A client that implements MCP authorization finds the provider from the `401`
and the protected-resource metadata. Otherwise, pass
`Authorization: Bearer <access token>` for the API audience.

## Not yet

There is no import of fully written paths (passages and Quick checks supplied
by the caller), no Attempt or completion tools, and no tutor or shaping
tools.
