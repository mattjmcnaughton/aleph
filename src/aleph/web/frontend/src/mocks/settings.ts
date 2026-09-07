// Contract-shaped fakes for the Settings API (CONTEXT.md: Settings;
// docs/api.md ## Settings). A one-row store, because `PATCH` merges into what
// `GET` then returns — the same "the result of a mutation comes back out the
// next read" reason `mocks/flashcards.ts` keeps a store for cards.
//
// The session fake (`mocks/handlers.ts`) carries its *own* `user.settings`;
// the SPA reads settings from there, and `useUpdateSettings` folds a PATCH
// response into that cached session. A test dialling in a non-default setting
// therefore overrides the session (`server.use(...)`), exactly as it does for
// a flag — this store only answers the two settings routes.

import { HttpResponse, http } from "msw";
import { API_V1_BASE, type UserSettings } from "../lib/api";

const DEFAULT: UserSettings = { auto_draft_flashcards: true };

let settings: UserSettings = { ...DEFAULT };
let patchFails = false;
/** Every `PATCH /settings` body the fake received, in order. */
let patchRequests: Partial<UserSettings>[] = [];

/** Reset store + config between tests (wired into tests/setup.ts). */
export function resetSettings(): void {
  settings = { ...DEFAULT };
  patchFails = false;
  patchRequests = [];
}

export function configureSettings(overrides: {
  settings?: Partial<UserSettings>;
  /** When true, `PATCH /settings` raises a generic `500`. */
  patchFails?: boolean;
}): void {
  if (overrides.settings) settings = { ...settings, ...overrides.settings };
  if (overrides.patchFails !== undefined) patchFails = overrides.patchFails;
}

export function settingsPatchRequests(): Partial<UserSettings>[] {
  return patchRequests;
}

export const settingsHandlers = [
  http.get(`${API_V1_BASE}/settings`, () => HttpResponse.json(settings)),

  http.patch(`${API_V1_BASE}/settings`, async ({ request }) => {
    const body = (await request.json()) as Partial<UserSettings>;
    patchRequests.push(body);
    if (patchFails) {
      return HttpResponse.json(
        {
          error: {
            code: "internal_error",
            message: "Something went wrong.",
            request_id: "test-request-id",
          },
        },
        { status: 500 },
      );
    }
    settings = { ...settings, ...body };
    return HttpResponse.json(settings);
  }),
];
