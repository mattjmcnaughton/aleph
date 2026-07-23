import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { AlephGlyph, AlephLogo } from "../components/aleph-logo";
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
  const hasError = new URLSearchParams(window.location.search).get("error") === "auth_failed";
  const providerLabel = formatProviderLabel(session.data?.provider ?? "keycloak");

  return (
    <main className="min-h-screen bg-night px-4 py-6 text-porcelain">
      <div className="mx-auto flex min-h-[calc(100vh-3rem)] w-full max-w-[440px] flex-col justify-center">
        <section className="rounded-lg border border-divider bg-surface p-6 text-center shadow-md">
          <div className="flex justify-center">
            <AlephLogo />
          </div>

          <div className="mt-8 flex justify-center">
            <AlephGlyph size="lg" />
          </div>
          <h1 className="mt-7 text-2xl font-semibold leading-tight">Sign in to Aleph</h1>
          <p className="mx-auto mt-3 max-w-[19rem] text-sm leading-6 text-mist">
            Name a topic, get a generated learning path. Your paths and progress sync to your
            account.
          </p>
          {hasError ? (
            <p className="mt-5 rounded-md border border-danger-border/60 bg-danger-bg px-3 py-2 text-sm text-danger">
              Sign-in didn&apos;t complete. Try again.
            </p>
          ) : null}
          <a
            href={AUTH_LOGIN_PATH}
            className="mt-6 inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright"
          >
            Continue with {providerLabel}
          </a>
          <p className="mx-auto mt-4 max-w-[18rem] text-xs leading-5 text-slate">
            We only read your public profile and email.
          </p>
        </section>
      </div>
    </main>
  );
}
