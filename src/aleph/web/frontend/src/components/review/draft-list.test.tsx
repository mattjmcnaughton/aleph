import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FlashcardDraftCard, FlashcardDrafts } from "../../lib/api";
import { DraftList } from "./draft-list";

// The drafts block below a lesson's completion state (PRD §3, Phase 3 TDD
// §5.2/§8). No router dependency — pure state + callbacks — so this is a
// direct unit suite, unlike the Link-bearing review components (covered end
// to end in `src/app/flashcards-drafts.test.tsx`).

const CARDS: FlashcardDraftCard[] = [
  { id: "c1", front: "What does `extends` mean?", back: "It constrains T." },
  { id: "c2", front: "Why constrain a generic?", back: "So the body can rely on it." },
  { id: "c3", front: "What is a generic?", back: "A type parameter." },
];

function drafts(overrides: Partial<FlashcardDrafts> = {}): FlashcardDrafts {
  return { state: "generated", cards: CARDS, ...overrides };
}

function noop() {}

// Every render below carries `triggerRateLimited`/`triggerErrored: false`
// unless the test is specifically about ticket 3's `not_started` line — the
// ordinary case for every other state, where a trigger that never failed has
// nothing to say.
const NO_TRIGGER_ERROR = { triggerRateLimited: false, triggerErrored: false };

describe("DraftList", () => {
  it("renders nothing for undefined (loading, gated off, or a failed poll)", () => {
    render(
      <DraftList
        drafts={undefined}
        onKeep={noop}
        keeping={false}
        keepErrored={false}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    expect(screen.queryByTestId("draft-list")).toBeNull();
  });

  it("renders nothing once every draft is resolved (generated, no cards left)", () => {
    render(
      <DraftList
        drafts={drafts({ cards: [] })}
        onKeep={noop}
        keeping={false}
        keepErrored={false}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    expect(screen.queryByTestId("draft-list")).toBeNull();
  });

  it("shows a generating notice while the run is still in progress", () => {
    render(
      <DraftList
        drafts={drafts({ state: "generating", cards: [] })}
        onKeep={noop}
        keeping={false}
        keepErrored={false}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    screen.getByTestId("flashcard-drafts-generating");
  });

  it("[§5.6] a failed run offers a retry, never a dead spinner", () => {
    const onRetry = vi.fn();
    render(
      <DraftList
        drafts={drafts({ state: "failed", cards: [] })}
        onKeep={noop}
        keeping={false}
        keepErrored={false}
        onRetry={onRetry}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    fireEvent.click(screen.getByTestId("flashcard-drafts-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("[PRD §3] all cards keep by default", () => {
    render(
      <DraftList
        drafts={drafts()}
        onKeep={noop}
        keeping={false}
        keepErrored={false}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    const cards = screen.getAllByTestId("draft-card");
    expect(cards).toHaveLength(3);
    for (const card of cards) {
      expect(card.getAttribute("data-kept")).toBe("true");
    }
    expect(screen.getByTestId("draft-keep-count").textContent).toBe("3 kept");
  });

  it("[PRD §3] the primary action names its own live count as toggles change", () => {
    render(
      <DraftList
        drafts={drafts()}
        onKeep={noop}
        keeping={false}
        keepErrored={false}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    expect(screen.getByTestId("draft-keep-button").textContent).toBe("Keep 3 cards");

    fireEvent.click(screen.getAllByTestId("draft-toggle")[2]);
    expect(screen.getByTestId("draft-keep-button").textContent).toBe("Keep 2 cards");
    expect(screen.getByTestId("draft-keep-count").textContent).toBe("2 kept");
    expect(screen.getAllByTestId("draft-card")[2].getAttribute("data-kept")).toBe("false");

    fireEvent.click(screen.getAllByTestId("draft-toggle")[1]);
    fireEvent.click(screen.getAllByTestId("draft-toggle")[0]);
    expect(screen.getByTestId("draft-keep-button").textContent).toBe("Keep none");
  });

  it("submits exactly the toggled-on ids", () => {
    const onKeep = vi.fn();
    render(
      <DraftList
        drafts={drafts()}
        onKeep={onKeep}
        keeping={false}
        keepErrored={false}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    fireEvent.click(screen.getAllByTestId("draft-toggle")[2]); // discard c3
    fireEvent.click(screen.getByTestId("draft-keep-button"));
    expect(onKeep).toHaveBeenCalledWith(["c1", "c2"]);
  });

  it("[PRD §3] 'Skip — keep none' is equally reachable and ignores the toggle state", () => {
    const onKeep = vi.fn();
    render(
      <DraftList
        drafts={drafts()}
        onKeep={onKeep}
        keeping={false}
        keepErrored={false}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    // All three still toggled on (the default) — Skip must still send `[]`.
    fireEvent.click(screen.getByTestId("draft-skip-button"));
    expect(onKeep).toHaveBeenCalledWith([]);
  });

  it("shows the keep-failure notice when the caller reports one", () => {
    render(
      <DraftList
        drafts={drafts()}
        onKeep={noop}
        keeping={false}
        keepErrored={true}
        onRetry={noop}
        retrying={false}
        {...NO_TRIGGER_ERROR}
      />,
    );
    screen.getByTestId("draft-keep-error");
  });

  describe("[§5.6 / ticket 3] the 429/409 rows beside `not_started`", () => {
    it("renders nothing for an ordinary not_started (no trigger error)", () => {
      render(
        <DraftList
          drafts={drafts({ state: "not_started", cards: [] })}
          onKeep={noop}
          keeping={false}
          keepErrored={false}
          onRetry={noop}
          retrying={false}
          {...NO_TRIGGER_ERROR}
        />,
      );
      expect(screen.queryByTestId("flashcard-drafts-trigger-error")).toBeNull();
    });

    it("a capped trigger (429) says drafting is unavailable today, not silence", () => {
      render(
        <DraftList
          drafts={drafts({ state: "not_started", cards: [] })}
          onKeep={noop}
          keeping={false}
          keepErrored={false}
          onRetry={noop}
          retrying={false}
          triggerRateLimited={true}
          triggerErrored={false}
        />,
      );
      expect(screen.getByTestId("flashcard-drafts-trigger-retry-ratelimit").textContent).toMatch(
        /unavailable today/i,
      );
      // The generic notice must not also show for a rate-limit.
      expect(screen.queryByTestId("flashcard-drafts-trigger-retry-error")).toBeNull();
    });

    it("a non-429 trigger failure (e.g. a 409 lesson_not_generated race) shows the generic notice", () => {
      render(
        <DraftList
          drafts={drafts({ state: "not_started", cards: [] })}
          onKeep={noop}
          keeping={false}
          keepErrored={false}
          onRetry={noop}
          retrying={false}
          triggerRateLimited={false}
          triggerErrored={true}
        />,
      );
      screen.getByTestId("flashcard-drafts-trigger-retry-error");
      expect(screen.queryByTestId("flashcard-drafts-trigger-retry-ratelimit")).toBeNull();
    });
  });
});
