import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useRouterState } from "@tanstack/react-router";
import { AlephGlyph } from "../components/aleph-logo";
import { PRIMARY_CTA, SECONDARY_CTA, StateCard } from "../components/state-card";
import { Workspace } from "../components/workspace";
import { CONSENT_QUERY_KEY, decideConsent, getConsent } from "../lib/oauth-consent";

// The consent step of Aleph's OAuth flow for MCP clients (docs/mcp.md).
// `/oauth/authorize` signs the learner in, checks the app's request and sends
// the browser here; approving or cancelling returns it to the app. The page
// names the app and the host it returns to, since any app can ask (MCP
// authorization spec, "Trust Policies").

export const Route = createFileRoute("/connect")({ component: ConnectPage });

const ERRORS: Record<string, string> = {
  invalid_client:
    "The app didn't identify itself in a way Aleph can check. Try connecting from the app again, or use an API key instead.",
  invalid_redirect:
    "The app asked to return somewhere it hasn't listed, so Aleph stopped. Try connecting from the app again.",
};

/** Leave the SPA for the app's redirect URI; a seam tests replace. */
export const navigation = { leave: (url: string) => window.location.assign(url) };

function ConnectPage() {
  const search = useRouterState({ select: (state) => state.location.searchStr });
  const error = new URLSearchParams(search).get("error");
  return (
    <Workspace testid="connect" width="lesson">
      <AlephGlyph size="lg" />
      {error ? (
        <Problem title="Can't connect this app">{ERRORS[error] ?? ERRORS.invalid_client}</Problem>
      ) : (
        <Consent />
      )}
    </Workspace>
  );
}

function Problem({ title, children }: { title: string; children: string }) {
  return (
    <>
      <h1 className="mt-8 page-title">{title}</h1>
      <StateCard testid="connect-error" spacing="mt-5">
        <p className="text-sm text-mist">{children}</p>
      </StateCard>
      <Link to="/settings/api-keys" className={`mt-6 ${SECONDARY_CTA}`}>
        Go to API keys
      </Link>
    </>
  );
}

function Consent() {
  const request = useQuery({ queryKey: CONSENT_QUERY_KEY, queryFn: getConsent, retry: false });
  const decide = useMutation({
    mutationFn: (approve: boolean) => decideConsent(request.data?.id ?? "", approve),
    onSuccess: ({ redirect_to }) => navigation.leave(redirect_to),
  });

  if (request.isPending) return <p className="mt-8 text-sm text-mist">Loading…</p>;
  if (request.isError) {
    return (
      <Problem title="No app is waiting">
        This connection request has expired or was already answered. Start connecting from the app
        again.
      </Problem>
    );
  }
  const app = request.data;
  const busy = decide.isPending || decide.isSuccess;
  return (
    <>
      <h1 className="mt-8 page-title">Connect {app.client_name}?</h1>
      <p className="mt-3 text-base leading-7 text-mist">
        <span className="font-semibold text-porcelain">{app.client_name}</span> wants to use Aleph
        as you. It can read your paths and lessons, start new paths, and edit their titles, Teaching
        style and About you. It can't delete anything, and a Quick check's answer stays hidden until
        you've answered it.
      </p>
      <dl className="mt-6 divide-y divide-divider border-y border-divider text-sm">
        <div className="flex justify-between gap-4 py-3">
          <dt className="text-mist">Published by</dt>
          <dd
            data-testid="connect-client-host"
            className="min-w-0 font-mono [overflow-wrap:anywhere]"
          >
            {app.client_host}
          </dd>
        </div>
        <div className="flex justify-between gap-4 py-3">
          <dt className="text-mist">Returns to</dt>
          <dd
            data-testid="connect-redirect-host"
            className="min-w-0 font-mono [overflow-wrap:anywhere]"
          >
            {app.redirect_host}
          </dd>
        </div>
      </dl>
      {app.loopback_only ? (
        <p data-testid="connect-loopback-note" className="mt-4 text-sm leading-6 text-mist">
          This app finishes signing in on your own device. Only connect if you just started
          connecting {app.client_name} yourself.
        </p>
      ) : null}
      <p className="mt-4 text-sm leading-6 text-mist">
        You can disconnect it any time in Settings → API keys.
      </p>
      {decide.isError ? (
        <p role="alert" className="mt-4 text-sm text-danger">
          That didn't go through. Start connecting from the app again.
        </p>
      ) : null}
      <div className="mt-7 flex gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => decide.mutate(false)}
          className={SECONDARY_CTA}
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => decide.mutate(true)}
          className={PRIMARY_CTA}
        >
          {busy && decide.variables ? "Connecting…" : "Connect"}
        </button>
      </div>
    </>
  );
}
