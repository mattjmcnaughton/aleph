import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { briefReadPingsFor, seedBeat, seedBrief } from "../mocks/beats";
import { learnerUser } from "../mocks/handlers";
import { server } from "../mocks/server";
import { App } from "./app";

// The home "Brief ready" indicator (mock E): when any Beat has unread Briefs,
// the Beats section moves up above the day's state, its header carries an
// "N new" chip, and each Beat with news is highlighted with an unread dot.
// With nothing unread, home keeps its usual order.

const analystSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { analyst: true } },
};

function useAnalystSession(): void {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(analystSession)));
}

async function gotoHome(): Promise<void> {
  window.history.pushState({}, "", "/");
  render(<App />);
  await screen.findByTestId("paths-switcher");
}

function published(id: string, number: number, readAt: string | null = null) {
  return {
    kind: "published" as const,
    id,
    number,
    publishedOn: "2026-08-03",
    title: `Brief ${number}`,
    readAt,
  };
}

function seedUnread(id: string, topic: string, unread = 1): void {
  seedBeat({
    id,
    topic,
    level: "some_experience",
    entries: Array.from({ length: unread }, (_, i) => published(`${id}-brief-${i + 1}`, i + 1)),
  });
}

function seedRead(id: string, topic: string): void {
  seedBeat({
    id,
    topic,
    level: "some_experience",
    entries: [published(`${id}-brief-1`, 1, "2026-08-04T09:00:00Z")],
  });
}

/** True when `a` comes before `b` in document order. */
function precedes(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("Home — the Brief-ready indicator", () => {
  it("moves the Beats section above the day's state when a Brief is unread", async () => {
    useAnalystSession();
    seedUnread("beat-news", "eClinical industry watch");

    await gotoHome();
    await screen.findByTestId("beat-list-item");

    const beats = screen.getByTestId("beats-section");
    expect(beats.getAttribute("data-promoted")).toBe("true");
    expect(precedes(beats, screen.getByTestId("today-rail"))).toBe(true);
  });

  it("keeps the Beats section below the paths when nothing is unread", async () => {
    useAnalystSession();
    seedRead("beat-quiet", "eClinical industry watch");

    await gotoHome();
    await screen.findByTestId("beat-list-item");

    const beats = screen.getByTestId("beats-section");
    expect(beats.getAttribute("data-promoted")).toBe("false");
    expect(precedes(screen.getByTestId("today-rail"), beats)).toBe(true);
    expect(precedes(screen.getByTestId("paths-section-toggle"), beats)).toBe(true);
    expect(screen.queryByTestId("beats-unread-chip")).toBeNull();
  });

  it("shows an 'N new' chip counting unread Briefs across Beats, even when collapsed", async () => {
    useAnalystSession();
    seedUnread("beat-a", "EU AI regulation", 2);
    seedUnread("beat-b", "eClinical industry watch", 1);

    await gotoHome();
    const chip = await screen.findByTestId("beats-unread-chip");
    expect(chip.textContent).toBe("3 new");

    fireEvent.click(screen.getByTestId("beats-section-toggle"));
    expect(screen.queryByTestId("beat-list-item")).toBeNull();
    expect(screen.getByTestId("beats-unread-chip").textContent).toBe("3 new");
  });

  it("highlights a Beat with unread Briefs and leaves a read one alone", async () => {
    useAnalystSession();
    seedUnread("beat-news", "eClinical industry watch");
    seedRead("beat-quiet", "EU AI regulation");

    await gotoHome();
    await screen.findAllByTestId("beat-list-item");

    const unread = document.querySelector('[data-beat-id="beat-news"]') as HTMLElement;
    const read = document.querySelector('[data-beat-id="beat-quiet"]') as HTMLElement;
    expect(unread.getAttribute("data-unread")).toBe("true");
    expect(unread.getAttribute("data-variant")).toBe("unread");
    expect(within(unread).queryByTestId("beat-unread-dot")).not.toBeNull();
    expect(read.getAttribute("data-unread")).toBe("false");
    expect(within(read).queryByTestId("beat-unread-dot")).toBeNull();
  });

  it("a refused Beat keeps its refusal treatment", async () => {
    useAnalystSession();
    seedBeat({
      id: "beat-refused",
      topic: "Something out of scope",
      level: "some_experience",
      resolution: "refused",
    });

    await gotoHome();
    await screen.findByTestId("beat-list-item");
    const row = document.querySelector('[data-beat-id="beat-refused"]') as HTMLElement;
    await waitFor(() => expect(row.getAttribute("data-research-state")).toBe("refused"));
    expect(row.getAttribute("data-variant")).toBe("refusal");
    expect(within(row).queryByTestId("beat-unread-dot")).toBeNull();
  });

  it("sorts Beats with unread Briefs first, keeping the rest in order", async () => {
    useAnalystSession();
    // The list API is newest first, so these come back as: read-2, read-1, news.
    seedUnread("beat-news", "eClinical industry watch");
    seedRead("beat-read-1", "EU AI regulation");
    seedRead("beat-read-2", "Postgres releases");

    await gotoHome();
    await screen.findAllByTestId("beat-list-item");

    const order = screen
      .getAllByTestId("beat-list-item")
      .map((row) => row.getAttribute("data-beat-id"));
    expect(order).toEqual(["beat-news", "beat-read-2", "beat-read-1"]);
  });

  it("returns to the usual layout once the Brief has been read", async () => {
    useAnalystSession();
    seedUnread("beat-news", "eClinical industry watch");
    seedBrief({ id: "beat-news-brief-1", beatId: "beat-news", number: 1 });

    await gotoHome();
    await screen.findByTestId("beats-unread-chip");

    // Open the Brief the way a learner does: row, then rail, then Brief.
    fireEvent.click(screen.getByTestId("beat-item-open"));
    const publishedRow = await screen.findByTestId("beat-rail-published");
    const railLink = publishedRow.querySelector("a");
    if (!railLink) throw new Error("expected the rail row to render a link");
    fireEvent.click(railLink);
    await screen.findByTestId("brief-body");
    // Let the `opened` read ping and its list invalidation land.
    await waitFor(() => expect(briefReadPingsFor("beat-news-brief-1")).toHaveLength(1));

    fireEvent.click(screen.getByRole("link", { name: /aleph home/i }));
    await screen.findByTestId("paths-switcher");
    await waitFor(() =>
      expect(screen.getByTestId("beats-section").getAttribute("data-promoted")).toBe("false"),
    );
    expect(screen.queryByTestId("beats-unread-chip")).toBeNull();
  });
});
