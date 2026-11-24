// Pure Public profile helpers (Public profiles TDD §6): which URLs are public,
// how a `?from=` reference parses, and how a Copy prefills a form.

const HANDLE = "[a-z0-9-]{3,30}";
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

// Exactly the four public shapes, and nothing nested under them (SR 10).
const PUBLIC_PATHNAME = new RegExp(
  `^/@(${HANDLE})(?:/paths/${UUID}|/beats/${UUID}(?:/briefs/${UUID})?)?$`,
);
const FROM = new RegExp(`^(${HANDLE})/(${UUID})$`);

/** Whether a signed-out visitor may open `pathname` without signing in. */
export function isPublicPathname(pathname: string): boolean {
  return PUBLIC_PATHNAME.test(pathname);
}

/** The Handle a public page belongs to, or null for any other page. */
export function publicHandleOf(pathname: string): string | null {
  return PUBLIC_PATHNAME.exec(pathname)?.[1] ?? null;
}

/** `?from=<handle>/<uuid>`: the Shared item a Copy starts from. */
export interface CopyFrom {
  handle: string;
  id: string;
}

export function parseFrom(value: unknown): CopyFrom | null {
  if (typeof value !== "string") return null;
  const match = FROM.exec(value);
  return match ? { handle: match[1], id: match[2] } : null;
}

/**
 * The source values for fields the learner has left empty, and nothing else:
 * a Copy never overwrites what was typed or already prefilled (TDD §6).
 */
export function fillEmptyFields<K extends string>(
  current: Partial<Record<K, string>>,
  source: Partial<Record<K, string | null>>,
): Partial<Record<K, string>> {
  const filled: Partial<Record<K, string>> = {};
  for (const key of Object.keys(source) as K[]) {
    const value = source[key];
    if (value && !(current[key] ?? "").trim()) filled[key] = value;
  }
  return filled;
}
