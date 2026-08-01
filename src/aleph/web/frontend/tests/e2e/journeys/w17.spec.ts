// W17 — Shape your path: ask, preview, **Apply** (PRD §8, §5.4).
//
// The phase's central journey, end to end on a phone: a learner on the path
// view opens the shaping rail, asks for more practice, sees the edit drawn into
// their own path as **ghost rows**, taps **Apply**, and finds real lessons where
// the ghosts were — generating through Phase 1's untouched pipeline.
//
// Three claims this spec exists to hold:
//
//  1. **Nothing happens until the tap.** A Proposal on screen is an offer: the
//     outline is byte-for-byte unchanged while it stands, and "Not now" leaves
//     it that way without a request (PRD §5.4 — never a silent rewrite).
//  2. **Ghosts become rows in one round trip.** Apply answers with the refreshed
//     path, so the iris preview is replaced by real teal rows immediately — no
//     second fetch, and no window where the learner sees both.
//  3. **Applied is applied when the *structure* lands** (PRD §5.7). The added
//     lessons arrive `ungenerated` and ride Phase 1 from there; the Change does
//     not wait on generation, and neither does this spec.
//
// > **Scope note (AL-331 → AL-360).** The epic gives AL-360 the whole W17–W21
// > set. This file and `w19.spec.ts` are the apply/undo half, landed with the UI
// > they drive so it is not merged unexercised; AL-360 owns extending them
// > (W18's revised content, W20's declined edit, W21's guardrail) rather than
// > writing a second suite beside them.

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { createPath, railLessons, uniqueTopic } from "../fixtures/journey";
import {
  ADDED_LESSON_TITLE_PREFIX,
  ADDITION_LESSON_COUNT,
  applyProposal,
  askForAddition,
  closeShapingRail,
  fetchChanges,
  ghostRows,
  openShapingRail,
  proposalCards,
} from "../fixtures/shaping";

test.use({ storageState: DEV_STORAGE_STATE });

test.describe("W17 shape your path — add lessons", { tag: "@w17" }, () => {
  test("a Proposal previews as ghost rows and Apply turns them into real lessons", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Tidal power"));
    const before = await railLessons(page).count();
    expect(before).toBeGreaterThan(0);

    await openShapingRail(page);
    const card = await askForAddition(page);

    // --- the offer, previewed --------------------------------------------------
    // The card states its scale in the learner's terms before anything is
    // consented to (PRD §5.4's "adds 2 lessons ≈ 10 min").
    await expect(card.getByTestId("shaping-rail-proposal-cost")).toContainText(
      `Adds ${ADDITION_LESSON_COUNT} lessons`,
    );
    await expect(ghostRows(page)).toHaveCount(ADDITION_LESSON_COUNT);
    // A ghost is a drawing of an offer, and the path is still exactly itself:
    // the real rows have not moved and no Change exists.
    await expect(railLessons(page)).toHaveCount(before);
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(0);

    // --- the tap ---------------------------------------------------------------
    await applyProposal(page, card);

    await expect(railLessons(page)).toHaveCount(before + ADDITION_LESSON_COUNT);
    // The added rows are real path structure now — recognizable by the title the
    // stub gives what it proposes.
    const added = page
      .getByTestId("path-rail")
      .locator("button[data-unlock-state]", { hasText: ADDED_LESSON_TITLE_PREFIX });
    await expect(added).toHaveCount(ADDITION_LESSON_COUNT);

    // One Apply is one Change, and it is applied the moment the structure lands.
    const { changes } = await fetchChanges(page, pathId);
    expect(changes).toHaveLength(1);
    expect(changes[0].status).toBe("applied");
    expect(changes[0].kinds).toEqual(["add_lessons"]);

    // The card says so, and offers the way back to the thing it changed.
    await expect(card.getByTestId("shaping-rail-proposal-view")).toBeVisible();
  });

  test("Not now leaves the path exactly as it was, and writes nothing", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Kite aerodynamics"));
    const before = await railLessons(page).count();

    await openShapingRail(page);
    const card = await askForAddition(page);
    await expect(ghostRows(page)).toHaveCount(ADDITION_LESSON_COUNT);

    await card.getByTestId("shaping-rail-proposal-dismiss").click();

    await expect(card).toHaveAttribute("data-state", "dismissed");
    // Declining is never destructive: the preview goes, the path does not move,
    // and no Change was ever created.
    await expect(ghostRows(page)).toHaveCount(0);
    await expect(railLessons(page)).toHaveCount(before);
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(0);
  });

  test("ghosts belong to the open thread — closing the rail takes them with it", async ({
    page,
  }) => {
    await createPath(page, uniqueTopic("Glacier melt"));
    await openShapingRail(page);
    await askForAddition(page);
    await expect(ghostRows(page)).toHaveCount(ADDITION_LESSON_COUNT);

    await closeShapingRail(page);

    await expect(ghostRows(page)).toHaveCount(0);

    // Reopening finds the same pending Proposal — the thread is server state —
    // and the preview with it.
    await openShapingRail(page);
    await expect(proposalCards(page).last()).toHaveAttribute("data-state", "pending");
    await expect(ghostRows(page)).toHaveCount(ADDITION_LESSON_COUNT);
  });
});
