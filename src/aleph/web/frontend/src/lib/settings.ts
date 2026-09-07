// Learner Settings (CONTEXT.md: Settings / Auto-draft) — reading them, and the
// one mutation that changes them.
//
// Settings ride the auth session (`user.settings`, resolved per learner on the
// backend), exactly as feature flags do (`lib/feature-flags.ts`): reading them
// is reading that same cached session query, so a component can honour a
// setting with no extra request. Same house rule, too — reuse
// `sessionQueryOptions` rather than respell the key, so there is one cached
// session and the settings page and the lesson view can never disagree.
//
// `DEFAULT_SETTINGS` restates the backend's code defaults
// (`services/user_settings.py`), for the window before the session lands and
// for a payload from an older backend that has no `settings` yet. Unlike a
// flag, whose unknown/absent value is *off* (a gate stays closed), a setting's
// absent value is its default — Auto-draft on is the launched Phase 3
// behaviour, not a dark surface.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type AuthSession, type UserSettings, updateSettings } from "./api";
import { sessionQueryOptions } from "./auth";

export const DEFAULT_SETTINGS: UserSettings = {
  auto_draft_flashcards: true,
};

/** The learner's effective settings, off the cached session. */
export function useSettings(): UserSettings {
  const session = useQuery(sessionQueryOptions);
  return session.data?.user?.settings ?? DEFAULT_SETTINGS;
}

/**
 * `PATCH /settings`, folding the response into the cached session so every
 * reader of `useSettings` sees the change in the same render — no refetch of
 * the session probe, which would also re-resolve flags nobody changed.
 */
export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData<AuthSession>(sessionQueryOptions.queryKey, (prev) =>
        prev?.authenticated ? { ...prev, user: { ...prev.user, settings } } : prev,
      );
    },
  });
}
