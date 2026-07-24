// The fake server's `MODEL_ALLOWLIST` (TDD §5.3/D14) — one list, read by both
// endpoints that touch it in the real backend: `GET /auth/session` exposes it to
// admins (`user.model_allowlist`) and `POST /paths` validates the picker's
// overrides against it (`422 validation_error` off-allowlist, docs/api.md).
//
// Kept in its own module so the session fake (`./handlers`) and the paths fake
// (`./paths`) can share it without importing each other.

/** Bare OpenRouter ids, in the order the picker must render them (TDD §5.3). */
export const ADMIN_MODEL_ALLOWLIST: readonly string[] = [
  "anthropic/claude-sonnet-5",
  "anthropic/claude-haiku-4-5",
  "anthropic/claude-opus-4-8",
  "openai/gpt-5.6-terra",
  "minimax/minimax-m3",
] as const;
