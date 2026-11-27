import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Breadcrumbs } from "../components/breadcrumbs";
import { SectionHeader } from "../components/section-header";
import { PRIMARY_CTA, SECONDARY_CTA, StateCard } from "../components/state-card";
import { Workspace } from "../components/workspace";
import {
  API_KEYS_QUERY_KEY,
  type ApiKeyMetadata,
  createApiKey,
  listApiKeys,
  mcpConfigSnippet,
  revokeApiKey,
} from "../lib/api-keys";
import { useFeatureFlag } from "../lib/feature-flags";

// Settings → API keys (docs/mcp.md): mint, list and revoke the keys an MCP
// client uses, and show how to connect one. Modelled on accanto's API keys
// section: a key is shown once, revoked keys stay listed, revoke asks first.

export const Route = createFileRoute("/settings/api-keys")({ component: ApiKeysPage });

const SMALL_SECONDARY = `${SECONDARY_CTA} !min-h-11 !w-auto !py-2 text-sm`;
const PLACEHOLDER = "YOUR_API_KEY";

function ApiKeysPage() {
  const enabled = useFeatureFlag("mcp");
  return (
    <Workspace testid="api-keys-settings" width="lesson">
      <Breadcrumbs current="API keys" />
      <p className="kicker">Settings</p>
      <h1 className="mt-2 page-title">API keys</h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Connect Claude, Cursor or another AI app to Aleph. With a key it can read your paths and
        lessons, start new paths and edit their titles, Teaching style and About you. It can't
        delete anything, and a Quick check's answer stays hidden until you've answered it.
      </p>
      {enabled ? (
        <ApiKeys />
      ) : (
        <StateCard testid="api-keys-unavailable" spacing="mt-7">
          <p className="text-sm text-mist">API keys aren't available yet.</p>
        </StateCard>
      )}
    </Workspace>
  );
}

function ApiKeys() {
  const queryClient = useQueryClient();
  const keys = useQuery({ queryKey: API_KEYS_QUERY_KEY, queryFn: listApiKeys });
  const [name, setName] = useState("");
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  const [message, setMessage] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY });

  const create = useMutation({
    mutationFn: createApiKey,
    onMutate: () => {
      setSecret(null);
      setMessage("");
    },
    onSuccess: (created) => {
      setSecret({ name: created.name, value: created.key });
      setName("");
      void refresh();
    },
    onError: () =>
      setMessage(
        "Creation was not confirmed. A key may have been created. Reload the list and revoke any key you don't want before creating another.",
      ),
  });

  async function copy(text: string, done: string) {
    try {
      await navigator.clipboard.writeText(text);
      setMessage(done);
    } catch {
      setMessage("Couldn't copy. Select the text and copy it yourself.");
    }
  }

  const snippet = mcpConfigSnippet(window.location.origin, secret?.value ?? PLACEHOLDER);
  const list = keys.data ?? [];

  return (
    <>
      <SectionHeader kicker="New key" spacing="mt-9" />
      <form
        className="mt-3"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate(name.trim());
        }}
      >
        <label htmlFor="api-key-name" className="text-sm font-semibold">
          Name
        </label>
        <p className="text-sm text-mist">
          Name it after the app that will use it, so you know which one to revoke later.
        </p>
        <div className="mt-2 flex gap-2">
          <input
            id="api-key-name"
            data-testid="api-key-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            required
            autoComplete="off"
            placeholder="Claude desktop"
            className="min-h-12 min-w-0 flex-1 rounded-md border border-faint bg-surface px-3 text-porcelain placeholder:text-slate focus:border-teal focus:outline-none"
          />
          <button
            type="submit"
            disabled={!name.trim() || create.isPending}
            className={`${PRIMARY_CTA} !w-auto`}
          >
            {create.isPending ? "Creating…" : "Create key"}
          </button>
        </div>
      </form>

      {message ? (
        <output data-testid="api-keys-message" className="mt-3 block text-sm text-mist">
          {message}
        </output>
      ) : null}

      {secret ? (
        <NewKey
          secret={secret}
          onCopy={() =>
            void copy(secret.value, "Key copied. It can't be shown again after you dismiss it.")
          }
          onDismiss={() => {
            setSecret(null);
            setMessage("");
          }}
        />
      ) : null}

      <SectionHeader
        kicker="Your keys"
        spacing="mt-9"
        summary={
          keys.isSuccess ? `${list.filter((key) => !key.revoked_at).length} active` : undefined
        }
      />
      {keys.isError ? (
        <p role="alert" className="mt-3 text-sm text-danger">
          We couldn't load your keys.{" "}
          <button type="button" className="underline" onClick={() => void refresh()}>
            Try again
          </button>
        </p>
      ) : keys.isPending ? (
        <p className="mt-3 text-sm text-mist">Loading…</p>
      ) : list.length === 0 ? (
        <p className="mt-3 text-sm text-mist">No keys yet.</p>
      ) : (
        <ul data-testid="api-key-list">
          {list.map((key) => (
            <KeyRow key={key.id} apiKey={key} onRevoked={() => setMessage("API key revoked.")} />
          ))}
        </ul>
      )}

      <SectionHeader kicker="Connect an app" spacing="mt-9" />
      <p className="mt-3 text-sm leading-6 text-mist">
        Add Aleph as a remote MCP server at{" "}
        <code className="rounded bg-porcelain/[0.09] px-1.5 py-0.5 font-mono text-porcelain">
          {window.location.origin}/mcp
        </code>{" "}
        and send the key as a Bearer token. Most apps accept this config:
      </p>
      <pre
        data-testid="mcp-config"
        className="mt-3 whitespace-pre-wrap break-all rounded-md bg-surface p-4 font-mono text-[13px] leading-6 text-porcelain"
      >
        {snippet}
      </pre>
      <button
        type="button"
        className={`mt-3 ${SMALL_SECONDARY}`}
        onClick={() => void copy(snippet, "Config copied.")}
      >
        Copy config
      </button>
      <p className="mt-3 text-sm leading-6 text-mist">
        {secret
          ? "The config above includes your new key. Copy it before you dismiss the key."
          : `Replace ${PLACEHOLDER} with a key you create above.`}
      </p>
    </>
  );
}

function NewKey({
  secret,
  onCopy,
  onDismiss,
}: {
  secret: { name: string; value: string };
  onCopy: () => void;
  onDismiss: () => void;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div
      data-testid="new-api-key"
      className="mt-5 rounded-lg border border-teal/60 bg-surface px-4 py-4"
    >
      <p className="font-semibold">Copy the key for “{secret.name}” now</p>
      <p className="text-sm text-mist">It won't be shown again.</p>
      <label className="sr-only" htmlFor="new-api-key-value">
        New API key (shown once)
      </label>
      <input
        id="new-api-key-value"
        data-testid="new-api-key-value"
        type={shown ? "text" : "password"}
        readOnly
        value={secret.value}
        autoComplete="off"
        spellCheck={false}
        className="mt-3 min-h-12 w-full rounded-md border border-faint bg-night px-3 font-mono text-sm text-porcelain"
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className={`${PRIMARY_CTA} !min-h-11 !w-auto !py-2 text-sm`}
          onClick={onCopy}
        >
          Copy key
        </button>
        <button
          type="button"
          className={SMALL_SECONDARY}
          aria-pressed={shown}
          onClick={() => setShown((value) => !value)}
        >
          {shown ? "Hide key" : "Show key"}
        </button>
        <button type="button" className={SMALL_SECONDARY} onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

function KeyRow({ apiKey, onRevoked }: { apiKey: ApiKeyMetadata; onRevoked: () => void }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const revoke = useMutation({
    mutationFn: () => revokeApiKey(apiKey.id),
    onSuccess: () => {
      setConfirming(false);
      onRevoked();
      void queryClient.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY });
    },
  });
  return (
    <li
      data-testid="api-key-row"
      className="flex min-h-16 items-center justify-between gap-4 border-b border-divider py-3"
    >
      <div className="min-w-0">
        <p className="truncate font-semibold">{apiKey.name}</p>
        <p className="text-sm text-mist">
          Created {day(apiKey.created_at)}
          {apiKey.revoked_at ? `, revoked ${day(apiKey.revoked_at)}` : null}
        </p>
        {revoke.isError ? (
          <p role="alert" className="text-sm text-danger">
            Revocation was not confirmed. Reload the page before trying again.
          </p>
        ) : null}
      </div>
      {apiKey.revoked_at ? (
        <span className="rounded-full border border-divider px-2.5 py-1 text-xs text-mist">
          Revoked
        </span>
      ) : confirming ? (
        <span className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            disabled={revoke.isPending}
            onClick={() => revoke.mutate()}
            className="min-h-11 rounded-md bg-danger px-3 text-sm font-semibold text-night disabled:opacity-50"
          >
            Revoke key
          </button>
          <button
            type="button"
            // biome-ignore lint/a11y/noAutofocus: Focus moves only after the learner chose Revoke; Cancel is the safe default.
            autoFocus
            disabled={revoke.isPending}
            onClick={() => setConfirming(false)}
            className={SMALL_SECONDARY}
          >
            Cancel
          </button>
        </span>
      ) : (
        <button
          type="button"
          aria-label={`Revoke ${apiKey.name}`}
          onClick={() => setConfirming(true)}
          className="min-h-11 shrink-0 rounded-md px-3 text-sm font-semibold text-danger hover:bg-elevated"
        >
          Revoke
        </button>
      )}
    </li>
  );
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
