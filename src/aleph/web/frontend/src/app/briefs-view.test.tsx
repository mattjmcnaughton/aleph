import { QueryClient } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { briefReadPingsFor, seedBrief, seedSkippedBriefId } from "../mocks/beats";
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

  it("a deep link to a Brief that doesn't exist shows the unavailable state", async () => {
    useAnalystSession();
    window.history.pushState({}, "", "/briefs/does-not-exist");
    render(<App />);

    await screen.findByTestId("brief-unavailable", {}, { timeout: 3000 });
  });

  // --- Read pings (D11, TDD §6/§9) -------------------------------------------

  it("[D11] the `opened` ping fires on mount and does not re-fire on re-render", async () => {
    seedBrief({ id: "brief-opened", beatId: BEAT_ID });

    await gotoBrief("brief-opened");

    // Wait for the mutation's own fetch to land.
    await act(async () => {
      await Promise.resolve();
    });
    expect(briefReadPingsFor("brief-opened")).toEqual([{ marker: "opened", tzOffsetMinutes: 0 }]);

    // Force a handful of re-renders of the same mounted route (StrictMode's
    // own double-invoke shape, and ordinary React churn) — still exactly one.
    for (let i = 0; i < 3; i++) {
      await act(async () => {});
    }
    expect(briefReadPingsFor("brief-opened")).toHaveLength(1);
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

  it('[TDD §7] the read ping invalidates exactly ["beats", beatId] — the unread count and the rail\'s read state move in the same interaction', async () => {
    const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    seedBrief({ id: "brief-invalidate", beatId: BEAT_ID });

    await gotoBrief("brief-invalidate");
    await act(async () => {
      await Promise.resolve();
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["beats", BEAT_ID] });
  });
});
