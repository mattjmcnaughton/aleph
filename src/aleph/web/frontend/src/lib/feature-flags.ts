import { useQuery } from "@tanstack/react-query";
import { sessionQueryOptions } from "./auth";

// Feature flags are delivered as part of the auth session (`user.feature_flags`,
// resolved per learner on the backend — AL-203). Reading them is just reading
// that same cached session query, so any component can gate on a flag with no
// extra request and no new plumbing.
//
// This deliberately reuses `sessionQueryOptions` rather than respelling the key
// + fetcher: the house rule in `lib/auth.ts` is one definition of the session
// query, and a hand-spelled duplicate here would read a second, separately
// cached copy.
//
// Unknown or absent flags resolve to **off**, which is also what an
// unsettled/failed session query yields — the gate stays closed rather than
// flashing a dark feature open before the session lands.

export function useFeatureFlag(key: string): boolean {
  const session = useQuery(sessionQueryOptions);
  return session.data?.user?.feature_flags?.[key] ?? false;
}
