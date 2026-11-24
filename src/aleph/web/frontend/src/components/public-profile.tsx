// Shared pieces of the Public profile pages (Public profiles PRD §3, TDD §6):
// the signed-out header, the one "not here" page, the owner line and the Copy
// card. Learner text (titles, Guidance, Teaching style) is always rendered as
// plain text here, never Markdown (SR 9).

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { type PublicOwner, copySignInHref, isNotFound } from "../lib/api";
import { sessionQueryOptions } from "../lib/auth";
import { AlephGlyph, AlephLogo } from "./aleph-logo";
import { PRIMARY_CTA_BASE } from "./state-card";

/** Whether the visitor reading a public page is signed in (a cached read). */
export function useSignedIn(): boolean {
  return useQuery(sessionQueryOptions).data?.authenticated ?? false;
}

/** The header a signed-out visitor sees: the brand and the one way in. */
export function PublicHeader() {
  return (
    <header
      data-testid="public-header"
      className="sticky top-0 z-10 box-border h-[var(--app-header-h)] border-b border-divider bg-night/90 pt-[env(safe-area-inset-top)] backdrop-blur"
    >
      <div className="mx-auto flex h-full w-full max-w-[560px] items-center justify-between gap-3 px-5 lg:max-w-none lg:px-6">
        <Link to="/" aria-label="Aleph" className="inline-flex shrink-0 rounded-md">
          <AlephLogo />
        </Link>
        <Link
          to="/login"
          className="inline-flex min-h-[44px] items-center rounded-md px-2 text-sm font-semibold text-porcelain transition-colors hover:text-teal-bright"
        >
          Sign in
        </Link>
      </div>
    </header>
  );
}

/** A small initial avatar for the profile's owner (no photos, PRD §6). */
export function OwnerAvatar({ name, size = "md" }: { name: string; size?: "sm" | "md" }) {
  const box = size === "md" ? "h-14 w-14 text-xl" : "h-6 w-6 text-[11px]";
  return (
    <span
      aria-hidden="true"
      className={`grid ${box} shrink-0 place-items-center rounded-full bg-elevated font-serif font-medium text-porcelain ring-1 ring-divider`}
    >
      {name.trim().charAt(0).toUpperCase() || "A"}
    </span>
  );
}

/** "Shared by Matt McNaughton · @matt", linking back to the profile. */
export function SharedBy({ owner }: { owner: PublicOwner }) {
  return (
    <p className="flex items-center gap-2 text-sm text-mist">
      <OwnerAvatar name={owner.display_name} size="sm" />
      <span>
        Shared by{" "}
        <Link
          to="/@{$handle}"
          params={{ handle: owner.handle }}
          className="font-semibold text-porcelain hover:text-teal-bright"
        >
          {owner.display_name}
        </Link>{" "}
        · @{owner.handle}
      </span>
    </p>
  );
}

/**
 * The one card on a shared path or Beat: start your own (PRD §3 frames 4–5).
 * Signed in it opens the prefilled form; signed out it signs in with a typed
 * copy intent and lands on that form (TDD §5). It never creates anything.
 */
export function CopyCard({
  kind,
  owner,
  itemId,
  title,
  body,
  action,
}: {
  kind: "path" | "beat";
  owner: PublicOwner;
  itemId: string;
  title: string;
  body: string;
  action: string;
}) {
  const signedIn = useSignedIn();
  const from = `${owner.handle}/${itemId}`;
  return (
    <section
      data-testid="copy-card"
      className="mt-6 rounded-lg border border-divider bg-surface p-5"
    >
      <p className="font-semibold">{title}</p>
      <p className="mt-1 text-sm leading-6 text-mist">{body}</p>
      {signedIn ? (
        kind === "path" ? (
          <Link to="/new" search={{ from }} className={`mt-4 ${PRIMARY_CTA_BASE}`}>
            {action}
          </Link>
        ) : (
          <Link to="/beats/new" search={{ from }} className={`mt-4 ${PRIMARY_CTA_BASE}`}>
            {action}
          </Link>
        )
      ) : (
        <a href={copySignInHref(kind, owner.handle, itemId)} className={`mt-4 ${PRIMARY_CTA_BASE}`}>
          Sign in to {action.charAt(0).toLowerCase()}
          {action.slice(1)}
        </a>
      )}
    </section>
  );
}

/**
 * Private, unknown, unshared and deleted all render this one page, so a URL
 * never confirms that something exists (PRD §4.9).
 */
export function NotHere() {
  return (
    <main className="page">
      <section
        data-testid="public-not-here"
        className="mt-10 rounded-lg border border-divider bg-surface px-6 py-8 text-center"
      >
        <div className="flex justify-center">
          <AlephGlyph size="md" />
        </div>
        <h1 className="mt-4 font-serif text-xl font-medium">Nothing to see here</h1>
        <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
          This link doesn't go to a public profile or path. It may have been made private.
        </p>
        <Link to="/" className={`mt-5 ${PRIMARY_CTA_BASE}`}>
          Start your own path
        </Link>
      </section>
    </main>
  );
}

/** The shared loading/absent/error frame every public page goes through. */
export function PublicPage<T>({
  query,
  children,
}: {
  query: { data: T | undefined; error: unknown; isPending: boolean };
  children: (data: T) => ReactNode;
}) {
  if (query.data !== undefined) return <>{children(query.data)}</>;
  if (query.isPending) {
    return (
      <main className="page" aria-busy="true">
        <p className="mt-10 text-center text-sm text-mist">Loading…</p>
      </main>
    );
  }
  if (isNotFound(query.error)) return <NotHere />;
  return (
    <main className="page">
      <p data-testid="public-error" role="alert" className="mt-10 text-center text-sm text-mist">
        We couldn't load this page. Try again in a moment.
      </p>
    </main>
  );
}

/** The quiet invitation at the foot of a page a signed-out visitor is reading. */
export function JoinFooter({ owner }: { owner: string }) {
  if (useSignedIn()) return null;
  return (
    <div data-testid="join-footer" className="mt-12 border-t border-divider pt-8 text-center">
      <div className="flex justify-center">
        <AlephGlyph size="sm" />
      </div>
      <p className="mt-3 font-serif text-lg font-medium">Learn anything, lesson by lesson.</p>
      <p className="mx-auto mt-1 max-w-[22rem] text-sm leading-6 text-mist">
        Aleph writes a learning path for any topic you name. Start one of {firstName(owner)}'s, or
        your own.
      </p>
    </div>
  );
}

export function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] || name;
}

/** "Started from Matt McNaughton's path", on the visitor's own copy. */
export function CopyCredit({
  kind,
  source,
}: {
  kind: "path" | "Beat";
  source: { display_name: string; handle: string; id: string };
}) {
  return (
    <p data-testid="copy-credit" className="mt-1 flex items-center gap-2 text-sm text-mist">
      <OwnerAvatar name={source.display_name} size="sm" />
      <span>
        Started from{" "}
        {kind === "path" ? (
          <Link
            to="/@{$handle}/paths/$pathId"
            params={{ handle: source.handle, pathId: source.id }}
            className="text-porcelain hover:text-teal-bright"
          >
            {source.display_name}'s path
          </Link>
        ) : (
          <Link
            to="/@{$handle}/beats/$beatId"
            params={{ handle: source.handle, beatId: source.id }}
            className="text-porcelain hover:text-teal-bright"
          >
            {source.display_name}'s Beat
          </Link>
        )}
      </span>
    </p>
  );
}
