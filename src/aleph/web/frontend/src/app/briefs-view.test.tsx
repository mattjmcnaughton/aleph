import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { briefReadPingsFor, seedBeat, seedBrief, seedSkippedBriefId } from "../mocks/beats";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

// The Brief reading surface (PRD §3, TDD §8, AL-531): `routes/briefs.$briefId.tsx`
// — title, date, `Builds on Brief #N`, the Markdown body through `markdown.tsx`
// UNTOUCHED, and the Sources block with its own `IntersectionObserver`-driven
// read ping. Driven end to end through the real router, MSW, and a stubbed
// `IntersectionObserver` (jsdom carries none) — `beats-view.test.tsx`'s own seam.

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

function useAnalystSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
}

async function gotoBrief(briefId: string) {
  useAnalystSession();
  window.history.pushState({}, "", `/briefs/${briefId}`);
  render(<App />);
  return screen.findByTestId("brief-body");
}

// --- A minimal, controllable IntersectionObserver stub ----------------------
//
// jsdom ships no IntersectionObserver at all, so `brief-sources.tsx` degrades
// to "never fires" without one (its own documented fallback). Each test that
// cares about the Sources ping installs this stub and drives the callback by
// hand, which is the only way to simulate "scrolled into view" in jsdom.

class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = [];
  callback: IntersectionObserverCallback;
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn(() => []);
  root = null;
  rootMargin = "";
  thresholds: ReadonlyArray<number> = [];

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    FakeIntersectionObserver.instances.push(this);
  }
}

function intersect(instance: FakeIntersectionObserver, isIntersecting: boolean) {
  act(() => {
    instance.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      instance as unknown as IntersectionObserver,
    );
  });
}

beforeEach(() => {
  FakeIntersectionObserver.instances = [];
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const BEAT_ID = "beat-brief-view";

describe("Brief view — /briefs/$briefId", () => {
  it("[FIX 3, flag off] renders a dead end rather than 'Loading your Brief…' forever", async () => {
    seedBrief({ id: "brief-flag-off", beatId: BEAT_ID, title: "Flag off" });
    window.history.pushState({}, "", "/briefs/brief-flag-off");
    render(<App />);

    await screen.findByTestId("brief-unavailable");
    expect(screen.queryByText(/loading your brief/i)).toBeNull();
    expect(screen.queryByTestId("brief-body")).toBeNull();
  });

  it("[PRD §3] renders title, date, and the Markdown body", async () => {
    seedBrief({
      id: "brief-basic",
      beatId: BEAT_ID,
      number: 5,
      publishedOn: "2026-08-03",
      title: "The ambient-documentation backlash arrived",
      bodyMarkdown: "**Northlake** published a review.",
    });

    const body = await gotoBrief("brief-basic");
    expect(screen.getByText(/brief #5/i).textContent).toContain("Aug 3");
    expect(
      screen.getByRole("heading", { name: "The ambient-documentation backlash arrived" }),
    ).toBeTruthy();
    expect(body.querySelector("strong")?.textContent).toBe("Northlake");
  });

  it("[markdown.tsx, security] the body is rendered through the shared renderer with no second sanitization path", async () => {
    // The identical raw-HTML-escape proof `markdown.test.tsx` runs directly
    // against `<Markdown>` — reproduced here through the full route so a
    // second, route-local sanitizer (which would behave differently) cannot
    // hide behind this surface never being exercised end to end.
    seedBrief({
      id: "brief-untrusted",
      beatId: BEAT_ID,
      bodyMarkdown:
        'Before <img src="x" onerror="alert(1)"> after, and a [link](javascript:alert(1)).',
    });

    const body = await gotoBrief("brief-untrusted");
    expect(body.querySelector("img")).toBeNull();
    expect(body.innerHTML).toContain("&lt;img");
    const link = body.querySelector("a");
    expect(link?.getAttribute("href")).not.toContain("javascript:");
    // Exactly one render container for the body — no parallel node holding a
    // second, differently-sanitized copy of the same source.
    expect(screen.getAllByTestId("brief-body")).toHaveLength(1);
  });

  it("[PRD §3] `Builds on Brief #N` links to the previous published Brief", async () => {
    seedBrief({
      id: "brief-with-parent",
      beatId: BEAT_ID,
      number: 5,
      buildsOn: { id: "brief-4", number: 4, publishedOn: "2026-07-27" },
    });

    await gotoBrief("brief-with-parent");

    const link = screen.getByTestId("builds-on-line");
    expect(link.textContent).toBe("Builds on Brief #4 (Jul 27)");
    expect(link.getAttribute("href")).toContain("/briefs/brief-4");
  });

  it("[D1] `builds_on: null` renders no line at all", async () => {
    seedBrief({ id: "brief-first", beatId: BEAT_ID, number: 1, buildsOn: null });

    await gotoBrief("brief-first");

    expect(screen.queryByTestId("builds-on-line")).toBeNull();
  });

  it("[PRD §3] Sources render with all four fields, and the URL is a real link", async () => {
    seedBrief({
      id: "brief-sources",
      beatId: BEAT_ID,
      sources: [
        {
          position: 1,
          publisher: "Northlake Health System",
          title: "Ambient Documentation: 14-Month Post-Deployment Review",
          published_on: "2026-07-30",
          url: "https://example.com/northlake-review",
        },
        {
          position: 2,
          publisher: "US Food and Drug Administration",
          title: "Digital Health PCCP Amendments: Q2 Processing Times",
          published_on: "2026-08-01",
          url: "https://example.com/fda-pccp-q2",
        },
      ],
    });

    await gotoBrief("brief-sources");

    const block = screen.getByTestId("brief-sources");
    // A first-class region: an elevated surface, not a footnote.
    expect(block.className).toContain("bg-elevated");

    const rows = screen.getAllByTestId("brief-source");
    expect(rows).toHaveLength(2);

    const first = rows[0];
    expect(first.textContent).toContain("Ambient Documentation: 14-Month Post-Deployment Review");
    expect(first.textContent).toContain("Northlake Health System");
    expect(first.textContent).toContain("Jul 30");
    const link = first.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://example.com/northlake-review");
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");

    // Unnumbered on purpose (PRD Appendix A) — no "1." / "2." prefix printed.
    expect(block.textContent).not.toMatch(/^\s*1\./);
  });

  it("[390x844] a long publisher name is truncated rather than overflowing", async () => {
    seedBrief({
      id: "brief-long-publisher",
      beatId: BEAT_ID,
      sources: [
        {
          position: 1,
          publisher:
            "The International Consortium for Extremely Long Institutional Names in Regulatory Publishing",
          title: "A Report",
          published_on: "2026-08-01",
          url: "https://example.com/report",
        },
      ],
    });

    await gotoBrief("brief-long-publisher");

    const meta = screen.getByTestId("brief-source").querySelector("p:last-child");
    expect(meta?.className).toContain("truncate");
  });

  it("[D2, hand-off item 2] a Skipped entry's id resolves without crashing, showing the unavailable state", async () => {
    seedSkippedBriefId({ id: "brief-skipped", beatId: BEAT_ID });

    useAnalystSession();
    window.history.pushState({}, "", "/briefs/brief-skipped");
    render(<App />);

    await screen.findByTestId("brief-unavailable");
    expect(screen.queryByTestId("brief-body")).toBeNull();
  });

  it("[code-review FIX 2] a deep link to a Skipped Brief sends NO `opened` ping", async () => {
    // The reviewer's own probe: the mount effect used to fire before the
    // render body ever inspected `body_markdown` to decide this page is
    // showing `UnavailableState` — so a deep link to a Skipped id rendered
    // "We couldn't load this Brief" AND sent `{"marker":"opened"}` in the
    // same visit. The server now also refuses to stamp a Skipped row
    // (`BriefRepository.mark_read`'s own `kind == PUBLISHED` guard, see the
    // integration test), but the client should not be sending this ping at
    // all for a page it is telling the learner it "couldn't load".
    seedSkippedBriefId({ id: "brief-skipped-no-ping", beatId: BEAT_ID });

    useAnalystSession();
    window.history.pushState({}, "", "/briefs/brief-skipped-no-ping");
    render(<App />);

    await screen.findByTestId("brief-unavailable");
    // Give the effect a chance to have fired if the guard were missing.
    await act(async () => {
      await Promise.resolve();
    });
    expect(briefReadPingsFor("brief-skipped-no-ping")).toEqual([]);
  });

  it("a deep link to a Brief that doesn't exist shows the unavailable state", async () => {
    useAnalystSession();
    window.history.pushState({}, "", "/briefs/does-not-exist");
    render(<App />);

    await screen.findByTestId("brief-unavailable", {}, { timeout: 3000 });
  });

  // --- Read pings (D11, TDD §6/§9) -------------------------------------------

  it("[D11, code-review FIX 4] the `opened` ping fires exactly once even under React StrictMode's double-invoked mount effect", async () => {
    // The original test here (`for (let i = 0; i < 3; i++) await act(async
    // () => {})`) flushed microtasks but triggered no re-render at all: the
    // effect's deps (`detail`, `pingRead`) are both referentially stable
    // across those no-op `act()` calls, so React never re-ran the effect
    // body regardless of whether `openedFiredForRef` existed — mutation
    // testing proved this by deleting the ref guard entirely and watching
    // all 13 tests in this file, including this one, keep passing. This
    // version forces an ACTUAL second invocation of the mount effect with
    // the identical `detail` the guard is supposed to survive: React
    // StrictMode deliberately mounts → runs effects → unmounts → remounts →
    // runs effects again, on a single initial render, specifically to
    // surface effects that are not idempotent — exactly the shape the
    // docstring credits `openedFiredForRef` with covering, and exactly the
    // shape `main.tsx` (`<StrictMode>`) actually renders the whole app
    // through in production dev builds.
    seedBrief({ id: "brief-opened", beatId: BEAT_ID });

    useAnalystSession();
    window.history.pushState({}, "", "/briefs/brief-opened");
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await screen.findByTestId("brief-body");
    await act(async () => {
      await Promise.resolve();
    });

    expect(briefReadPingsFor("brief-opened")).toEqual([{ marker: "opened", tzOffsetMinutes: 0 }]);
  });

  it("[D11] `tz_offset_minutes` rides on the read ping", async () => {
    vi.spyOn(Date.prototype, "getTimezoneOffset").mockReturnValue(-120);
    seedBrief({ id: "brief-tz", beatId: BEAT_ID });

    await gotoBrief("brief-tz");
    await act(async () => {
      await Promise.resolve();
    });

    expect(briefReadPingsFor("brief-tz")).toEqual([{ marker: "opened", tzOffsetMinutes: -120 }]);
  });

  it("[D11] the `sources` ping fires EXACTLY ONCE even if the block scrolls in and out repeatedly", async () => {
    seedBrief({
      id: "brief-sources-ping",
      beatId: BEAT_ID,
      sources: [
        {
          position: 1,
          publisher: "Publisher",
          title: "Title",
          published_on: "2026-08-01",
          url: "https://example.com/a",
        },
      ],
    });

    await gotoBrief("brief-sources-ping");
    await act(async () => {
      await Promise.resolve();
    });

    expect(FakeIntersectionObserver.instances).toHaveLength(1);
    const observer = FakeIntersectionObserver.instances[0];

    // In, out, in, out, in — the block scrolling repeatedly through view.
    intersect(observer, true);
    intersect(observer, false);
    intersect(observer, true);
    intersect(observer, false);
    intersect(observer, true);

    await act(async () => {
      await Promise.resolve();
    });

    expect(briefReadPingsFor("brief-sources-ping")).toEqual([
      { marker: "opened", tzOffsetMinutes: 0 },
      { marker: "sources", tzOffsetMinutes: 0 },
    ]);
    // The observer disconnects itself the instant it fires — the structural
    // half of "exactly once", independent of the ref guard.
    expect(observer.disconnect).toHaveBeenCalledTimes(1);
  });

  it("[code-review FIX 3/5] the `sources` ping still fires — and `opened` fires only once — after a Brief→Brief→cached-Brief navigation", async () => {
    // The reviewer's own probe: open b5 (don't scroll) → follow `Builds on
    // #4` to b4 (fires [opened, sources] once scrolled) → navigate BACK to
    // b5, which TanStack Query still has cached (no `undefined` gap for
    // `detail` to pass through, so this route re-renders in place rather
    // than remounting) → b5's own Sources block must STILL be able to fire
    // its `sources` ping, and `opened` must not fire a second time for b5.
    // Before FIX 3, `<BriefSources>` carried no `key`, so returning to a
    // cached Brief reused the same instance with `firedRef` already `true`
    // and an already-disconnected observer — permanently suppressing
    // `sources` for b5. Before FIX 5, `openedFiredForRef` was a single
    // `string | null` that only remembered the MOST RECENT id (b4's, after
    // this navigation), so the guard's `=== detail.id` check was false for
    // b5 all over again and `opened` fired a second time.
    seedBrief({
      id: "brief-b5",
      beatId: BEAT_ID,
      number: 5,
      buildsOn: { id: "brief-b4", number: 4, publishedOn: "2026-07-27" },
      sources: [
        {
          position: 1,
          publisher: "Publisher Five",
          title: "Source Five",
          published_on: "2026-07-30",
          url: "https://example.com/five",
        },
      ],
    });
    seedBrief({
      id: "brief-b4",
      beatId: BEAT_ID,
      number: 4,
      buildsOn: null,
      sources: [
        {
          position: 1,
          publisher: "Publisher Four",
          title: "Source Four",
          published_on: "2026-07-20",
          url: "https://example.com/four",
        },
      ],
    });

    await gotoBrief("brief-b5");
    await act(async () => {
      await Promise.resolve();
    });
    expect(briefReadPingsFor("brief-b5")).toEqual([{ marker: "opened", tzOffsetMinutes: 0 }]);
    // b5's Sources block never scrolled into view — no `sources` ping yet,
    // and its observer is still live (never fired, never disconnected).

    // Follow `Builds on Brief #4` — an uncached Brief, a real remount.
    fireEvent.click(screen.getByTestId("builds-on-line"));
    await screen.findByRole("heading", { name: "Brief #4" });
    await act(async () => {
      await Promise.resolve();
    });
    expect(briefReadPingsFor("brief-b4")).toEqual([{ marker: "opened", tzOffsetMinutes: 0 }]);

    const b4Observer =
      FakeIntersectionObserver.instances[FakeIntersectionObserver.instances.length - 1];
    if (!b4Observer) throw new Error("expected b4's Sources block to install an observer");
    intersect(b4Observer, true);
    await act(async () => {
      await Promise.resolve();
    });
    expect(briefReadPingsFor("brief-b4")).toEqual([
      { marker: "opened", tzOffsetMinutes: 0 },
      { marker: "sources", tzOffsetMinutes: 0 },
    ]);

    // Back to b5 — already in the TanStack Query cache (fetched once above,
    // well inside the 30s `staleTime`), so this route re-renders in place
    // rather than remounting.
    window.history.back();
    await screen.findByRole("heading", { name: "Brief #5" });
    await act(async () => {
      await Promise.resolve();
    });

    // FIX 5: `opened` did NOT fire a second time for b5.
    expect(briefReadPingsFor("brief-b5")).toEqual([{ marker: "opened", tzOffsetMinutes: 0 }]);

    // FIX 3: b5's Sources block gets a FRESH observer (keyed on `detail.id`)
    // rather than reusing b4's already-fired, already-disconnected one, so
    // it can still fire `sources` for b5 on first visibility.
    const b5Observer =
      FakeIntersectionObserver.instances[FakeIntersectionObserver.instances.length - 1];
    if (!b5Observer || b5Observer === b4Observer) {
      throw new Error("expected a fresh observer instance for b5, keyed by Brief id");
    }
    intersect(b5Observer, true);
    await act(async () => {
      await Promise.resolve();
    });
    expect(briefReadPingsFor("brief-b5")).toEqual([
      { marker: "opened", tzOffsetMinutes: 0 },
      { marker: "sources", tzOffsetMinutes: 0 },
    ]);
  });

  it('[code-review FIX 1] the read ping invalidates the ["beats"] PREFIX — reaching both the list and the detail query, not just the detail key', async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    seedBrief({ id: "brief-invalidate", beatId: BEAT_ID });

    await gotoBrief("brief-invalidate");
    await act(async () => {
      await Promise.resolve();
    });

    // The defect this locked in: invalidating `["beats", BEAT_ID]` alone
    // never prefix-matches the list's own `["beats"]` key (a query key must
    // START WITH the filter key — the reverse is never true), so the list —
    // the only carrier of `unread_count` — was never refreshed. `["beats"]`
    // is a prefix of itself AND of `["beats", BEAT_ID]`, so this one call
    // reaches both.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["beats"] });
  });

  it("[code-review FIX 1, end to end] a read ping actually refreshes the home unread count, not just the rail's read state", async () => {
    // Reproduces the reviewer's own probe: home shows "1 new brief · weekly"
    // → open the Brief → `opened` fires and the rail's `read_at` stamps →
    // navigate home (still inside the 30s `staleTime` every query in this
    // app defaults to, `app.tsx`) → the home card must no longer say "1 new
    // brief". A single continuous `render(<App />)` with real `Link` clicks
    // (never a second `render()` call, which would mint a fresh
    // `QueryClient` per TanStack Router precedent and could never reproduce
    // a same-session cache staleness bug at all) — home -> the Beat's rail
    // -> the Brief -> back home, entirely through the real router + the real
    // cache this bug lives in.
    const brief = { id: "brief-refresh-count", beatId: "beat-refresh-count" };
    seedBeat({
      id: brief.beatId,
      topic: "Ambient documentation",
      level: "some_experience",
      entries: [
        {
          kind: "published",
          id: brief.id,
          number: 1,
          publishedOn: "2026-08-03",
          title: "The first Brief",
          readAt: null,
        },
      ],
    });
    seedBrief({ id: brief.id, beatId: brief.beatId, number: 1 });

    useAnalystSession();
    window.history.pushState({}, "", "/");
    render(<App />);

    const card = await screen.findByTestId("beat-list-item");
    expect(screen.getByTestId("beat-item-status").textContent).toBe("1 new brief · weekly");

    fireEvent.click(card);
    const publishedRow = await screen.findByTestId("beat-rail-published");
    const railLink = publishedRow.querySelector("a");
    if (!railLink) throw new Error("expected the rail row to render a link");
    fireEvent.click(railLink);

    await screen.findByTestId("brief-body");
    // Let the `opened` mutation's own fetch (and its `onSuccess`
    // invalidation) land before navigating away.
    await act(async () => {
      await Promise.resolve();
    });
    expect(briefReadPingsFor(brief.id)).toEqual([{ marker: "opened", tzOffsetMinutes: 0 }]);

    fireEvent.click(screen.getByRole("link", { name: /your beats/i }));

    await waitFor(() => {
      expect(screen.getByTestId("beat-item-status").textContent).toBe("Up to date · weekly");
    });
  });
});
