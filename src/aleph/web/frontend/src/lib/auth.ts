// Pure auth-routing decisions plus the one canonical session query identity.
// The root route's `beforeLoad` is the only caller of `authRedirect`; keeping
// the decision pure makes the signed-out -> login -> signed-in state machine
// testable without a router or a network.
import { queryOptions } from "@tanstack/react-query";
import { type AuthSession, getAuthSession } from "./api";

/** The public route unauthenticated learners are allowed to sit on. */
export const LOGIN_PATH = "/login";

/**
 * THE session query — one definition of the key + fetcher, shared by every
 * reader (root `ensureQueryData`, index, login) and the logout mutation's cache
 * write. Spelling `["auth","session"]` + `getAuthSession` by hand in each file
 * is how the key and the cached shape drift apart, so nobody should.
 */
export const sessionQueryOptions = queryOptions({
  queryKey: ["auth", "session"] as const,
  queryFn: getAuthSession,
});

/**
 * Where the router should send the learner given the resolved session and the
 * path they asked for. Returns null to stay put.
 *
 * - Unauthenticated on any protected route -> /login.
 * - Authenticated but sitting on /login    -> app root.
 * - Otherwise                              -> stay.
 */
export function authRedirect(
  session: AuthSession | undefined,
  pathname: string,
): { to: string } | null {
  const authenticated = session?.authenticated ?? false;
  if (!authenticated && pathname !== LOGIN_PATH) {
    return { to: LOGIN_PATH };
  }
  if (authenticated && pathname === LOGIN_PATH) {
    return { to: "/" };
  }
  return null;
}
