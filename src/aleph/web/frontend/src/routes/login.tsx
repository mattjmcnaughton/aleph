import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useRouterState } from "@tanstack/react-router";
import { AlephGlyph } from "../components/aleph-logo";
import { PRIMARY_CTA_BASE } from "../components/state-card";
import { AUTH_LOGIN_PATH } from "../lib/api";
import { sessionQueryOptions } from "../lib/auth";

export const Route = createFileRoute("/login")({
  component: Login,
});

function formatProviderLabel(provider: string): string {
  const labels: Record<string, string> = {
    auth0: "Auth0",
    keycloak: "Keycloak (dev)",
  };
  return labels[provider.toLowerCase()] ?? provider;
}

// The public sign-in surface. An already-authenticated learner never lands
// here: the root `beforeLoad` gate (authRedirect) bounces them to the app
// before this renders, so there is no signed-in branch to reach.
function Login() {
  const session = useQuery(sessionQueryOptions);
  const search = useRouterState({ select: (state) => state.location.searchStr });
  const hasError = new URLSearchParams(search).get("error") === "auth_failed";
  const providerLabel = formatProviderLabel(session.data?.provider ?? "keycloak");

  return (
    <main className="min-h-screen bg-night px-6 pb-6 pt-[calc(1.5rem+env(safe-area-inset-top))] text-porcelain">
      <div className="mx-auto flex min-h-[calc(100vh-3rem)] w-full max-w-[400px] flex-col justify-center">
        {/* No card and one mark: the sign-in screen is the first thing a new
            learner sees, so it is a title, a sentence and one action. */}
        <section>
          <AlephGlyph size="lg" />
          <h1 className="mt-8 page-title">Sign in to Aleph</h1>
          <p className="mt-3 max-w-[22rem] text-base leading-7 text-mist">
            Name a topic, get a generated learning path. Your paths and progress sync to your
            account.
          </p>
          {hasError ? (
            <div className="mt-5 rounded-md border border-danger-border/60 bg-danger-bg px-3 py-2 text-sm text-danger">
              <p>Sign-in didn&apos;t complete. Try again.</p>
              <Link
                to="/login"
                className="mt-2 inline-flex min-h-11 items-center rounded-md px-3 text-porcelain underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
              >
                Back to sign in
              </Link>
            </div>
          ) : null}
          <a href={AUTH_LOGIN_PATH} className={`mt-8 ${PRIMARY_CTA_BASE}`}>
            Continue with {providerLabel}
          </a>
          <p className="mt-4 text-sm leading-6 text-slate">
            We only read your public profile and email.
          </p>
        </section>
      </div>
    </main>
  );
}
