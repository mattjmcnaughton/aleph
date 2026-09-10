import {
  Link,
  Outlet,
  createRootRouteWithContext,
  redirect,
  useRouterState,
} from "@tanstack/react-router";
import { useEffect } from "react";
import type { RouterContext } from "../app/app";
import { AppHeader } from "../components/app-header";
import { LOGIN_PATH, authRedirect, sessionQueryOptions } from "../lib/auth";
import { clearFlow, readFlow } from "../lib/flow";

export const Route = createRootRouteWithContext<RouterContext>()({
  // The auth gate: resolve the session once (cached), then let the pure
  // `authRedirect` state machine decide where the learner belongs.
  beforeLoad: async ({ context, location }) => {
    const session = await context.queryClient.ensureQueryData(sessionQueryOptions);
    const decision = authRedirect(session, location.pathname);
    if (decision) {
      throw redirect({ to: decision.to });
    }
  },
  component: RootLayout,
  errorComponent: RootError,
});

/** A flow's own two surfaces (flow TDD §5.4) — everywhere else, arriving is leaving. */
function isFlowRoute(pathname: string): boolean {
  return pathname.startsWith("/lessons/") || pathname.startsWith("/flow/");
}

function RootLayout() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  // The login screen is its own full-bleed surface — no app chrome.
  const showChrome = pathname !== LOGIN_PATH;

  // Leaving is ending (flow TDD D5): a flow that silently survived a trip to
  // home would resume itself hours later. This is what makes *Home* in the
  // header, sign-out, or a typed URL end one — `/flow/new`'s own start
  // overwrites the record on its own terms, and `/lessons/*`'s open effect
  // (`routes/lessons.$lessonId.tsx`) is the finer-grained twin of this one,
  // catching a navigation *within* the lesson surface that the flow itself
  // did not send the learner to. `readFlow()` (not the reactive `useFlow()`)
  // because this only needs a snapshot at the moment the route changes, not a
  // subscription that re-renders the whole shell on every write mid-flow.
  useEffect(() => {
    if (!isFlowRoute(pathname) && readFlow() !== null) {
      clearFlow();
    }
  }, [pathname]);

  return (
    <div className="min-h-screen bg-night text-porcelain">
      {showChrome ? <AppHeader /> : null}
      <Outlet />
    </div>
  );
}

function RootError() {
  return (
    <main className="min-h-screen bg-night px-4 py-6 text-porcelain">
      <div className="mx-auto flex min-h-[calc(100vh-3rem)] w-full max-w-[480px] flex-col justify-center">
        <section className="rounded-lg border border-danger-border/60 bg-surface p-6 text-center shadow-md">
          <h1 className="text-xl font-semibold text-danger">Something went wrong.</h1>
          <p className="mt-2 text-sm text-mist">We couldn't load Aleph. Try again.</p>
          <Link
            to="/"
            className="mt-6 inline-flex w-full justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright"
          >
            Back to your paths
          </Link>
        </section>
      </div>
    </main>
  );
}
