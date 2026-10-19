import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { type AuthSession, logout } from "../lib/api";
import { sessionQueryOptions } from "../lib/auth";

/**
 * Sign the learner out: end the server session, drop the cached session query,
 * and land them on /login. Exposes `pending` so the trigger can disable itself.
 */
export function useLogout(): { signOut: () => void; pending: boolean } {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: logout,
    // `onSettled` runs on both success and failure: even if the server call
    // fails we still tear down the local session and bounce to /login.
    onSettled: () => {
      // Write the ended session into cache without inventing a provider — carry
      // over whatever the previous session reported (keycloak in dev, auth0 in
      // prod) so the login screen still names the right IdP.
      queryClient.setQueryData<AuthSession>(sessionQueryOptions.queryKey, (prev) => ({
        authenticated: false,
        provider: prev?.provider ?? "keycloak",
        user: null,
      }));
      void navigate({ to: "/login" });
    },
  });

  return { signOut: () => mutation.mutate(), pending: mutation.isPending };
}
