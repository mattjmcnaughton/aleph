// Contract-shaped fakes for `GET`/`POST /api-keys` and `DELETE /api-keys/{id}`.
// `configureApiKeys([...])` seeds the list; `resetApiKeys()` clears it between
// tests and stories.

import { HttpResponse, http } from "msw";
import { type ApiKeyMetadata, API_KEYS_URL } from "../lib/api-keys";

let keys: ApiKeyMetadata[] = [];
let counter = 0;

export function configureApiKeys(seed: ApiKeyMetadata[]): void {
  keys = seed.map((key) => ({ ...key }));
}

export function resetApiKeys(): void {
  keys = [];
  counter = 0;
}

export const apiKeyHandlers = [
  http.get(API_KEYS_URL, () => HttpResponse.json({ keys })),
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
    keys = keys.map((key) =>
      key.id === params.id ? { ...key, revoked_at: key.revoked_at ?? now } : key,
    );
    return new HttpResponse(null, { status: 204 });
  }),
];
