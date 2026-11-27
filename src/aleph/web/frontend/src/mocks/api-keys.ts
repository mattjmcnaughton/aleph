// Contract-shaped fakes for `GET`/`POST /api-keys` and `DELETE /api-keys/{id}`.
// `configureApiKeys([...])` seeds the list; `resetApiKeys()` clears it between
// tests and stories.

import { HttpResponse, http } from "msw";
import { type ApiKeyMetadata, API_KEYS_URL, type Connection } from "../lib/api-keys";
import { CONSENT_URL, type ConsentRequest } from "../lib/oauth-consent";

let keys: ApiKeyMetadata[] = [];
let connections: Connection[] = [];
let consent: ConsentRequest | null = null;
let counter = 0;

export function configureApiKeys(seed: ApiKeyMetadata[], seedConnections: Connection[] = []): void {
  keys = seed.map((key) => ({ ...key }));
  connections = seedConnections.map((connection) => ({ ...connection }));
}

/** Park a connection request, as `/oauth/authorize` does before `/connect`. */
export function configureConsent(request: ConsentRequest | null): void {
  consent = request ? { ...request } : null;
}

export const EXAMPLE_CONSENT: ConsentRequest = {
  id: "req-1",
  client_name: "Claude Code",
  client_id: "https://claude.ai/oauth/claude-code-client-metadata",
  client_host: "claude.ai",
  redirect_host: "localhost:53682",
  loopback_only: true,
};

export function resetApiKeys(): void {
  keys = [];
  connections = [];
  consent = null;
  counter = 0;
}

export const apiKeyHandlers = [
  http.get(API_KEYS_URL, () => HttpResponse.json({ keys, connections })),
  http.post(API_KEYS_URL, async ({ request }) => {
    const { name } = (await request.json()) as { name: string };
    counter += 1;
    const id = `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`;
    keys = [
      ...keys,
      { id, name: name.trim(), created_at: new Date().toISOString(), revoked_at: null },
    ];
    return HttpResponse.json(
      { id, name: name.trim(), key: `k3yDemoSecret${counter}_9f2QxLw7aVb0cTn4pRs8uEyZhJm5dGo` },
      { status: 201 },
    );
  }),
  http.delete(`${API_KEYS_URL}/:id`, ({ params }) => {
    const now = new Date().toISOString();
    connections = connections.filter((connection) => connection.id !== params.id);
    keys = keys.map((key) =>
      key.id === params.id ? { ...key, revoked_at: key.revoked_at ?? now } : key,
    );
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(CONSENT_URL, () =>
    consent
      ? HttpResponse.json(consent)
      : HttpResponse.json(
          { error: { code: "not_found", message: "no connection request" } },
          { status: 404 },
        ),
  ),
  http.post(CONSENT_URL, async ({ request }) => {
    const { id, approve } = (await request.json()) as { id: string; approve: boolean };
    if (!consent || consent.id !== id) {
      return HttpResponse.json(
        { error: { code: "conflict", message: "a newer request replaced this one" } },
        { status: 409 },
      );
    }
    const params = approve ? "code=demo-code&state=xyz" : "error=access_denied&state=xyz";
    consent = null;
    return HttpResponse.json({ redirect_to: `http://127.0.0.1:53682/callback?${params}` });
  }),
];
