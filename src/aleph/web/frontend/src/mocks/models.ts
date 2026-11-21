// The fake server's `MODEL_ALLOWLIST` (TDD §5.3/D14) — one list, read by both
// endpoints that touch it in the real backend: `GET /auth/session` exposes it to
// admins (`user.model_allowlist`) and `POST /paths` validates the picker's
// overrides against it (`422 validation_error` off-allowlist, docs/api.md).
//
// Kept in its own module so the session fake (`./handlers`) and the paths fake
// (`./paths`) can share it without importing each other.

/** Bare OpenRouter ids, in the order the picker must render them (TDD §5.3). */
export const ADMIN_MODEL_ALLOWLIST: readonly string[] = [
  "openai/gpt-6-luna",
  "openai/gpt-6.2-sol",
  "anthropic/claude-haiku-5.5",
  "anthropic/claude-sonnet-5.5",
  "anthropic/claude-opus-5.5",
] as const;
