// W20 — Out-of-vocabulary edits are **declined**, not improvised
// (PRD §8, §5.7; TDD §5.5/D1).
//
// The shaping vocabulary is closed: **Addition** and **Revision**, and nothing
// else. So "remove the unit on basics", "reorder these", "revise the one I
// finished" and "mark lesson two complete" all land in the same place — a
// **declined edit**: an ordinary reply that names what shaping *can* do, with no
// card under it and nothing changed behind it.
//
// What makes this a workflow rather than an error case is the third state. A
// learner can meet three different "no"s on this rail, and they must not read
// alike:
//
//  * a **declined edit** — the ask was understood and is out of vocabulary;
//  * a **failure** — the turn fell over and nothing was saved;
//  * a safety refusal — 2A's, unchanged.
//
// So this file asserts the first two side by side: the declined edit arrives as
// a *message*, in the thread, with no error frame anywhere; the forced failure
// arrives as an error frame with a retry, and nothing in the thread at all. The
// declined wording is pinned in full (`SHAPING_DECLINED_EDIT_REPLY`) because it
// is a product promise, and it is compared against the wire — the rail renders
// it through `markdown.tsx`, so the emphasis is markup on screen.
//
// Both are forced with question-text sentinels (TDD §11): free-text phrasing
// should not have to trip a real model's judgement in CI.
//
// > Extends the shaping suite AL-331 started (`w17.spec.ts`, `w19.spec.ts`).

import { expect, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import { ACTION_TIMEOUT, createPath, railLessons, uniqueTopic } from "../fixtures/journey";
import {
  FORCE_SHAPING_DECLINE,
  FORCE_SHAPING_FAILURE,
  SHAPING_DECLINED_EDIT_REPLY,
  SHAPING_UPSTREAM_FAILURE_COPY,
  applyProposal,
  askForAddition,
  askShaper,
  fetchChanges,
  fetchShapingConversation,
  ghostRows,
  openShapingRail,
  proposalCards,
  shapingReplies,
} from "../fixtures/shaping";
// Phase 1 wire reads (see the note in `w18.spec.ts`): a path payload is path
// state, not rail state.
import { type PathPayload, fetchPath } from "../fixtures/tutor";

test.use({ storageState: DEV_STORAGE_STATE });

/**
 * The four asks PRD §8 names, each one outside the vocabulary for its own
 * reason: removal and reordering are not operations at all, a finished lesson is
 * engaged, and progress is never shaping's to touch.
 */
const OUT_OF_VOCABULARY = [
  "Remove the unit on the basics, I already know it",
  "Reorder these lessons so the hard one comes last",
  "Revise the lesson I already finished so it goes deeper",
  "Mark lesson two complete for me",
];

/**
 * The path payload minus the two fields background generation moves on its own
 * — `progress.generated_lessons` and each lesson's `generation_state`. Prefetch
 * is running throughout a journey, so those two change without anything the
 * learner did; everything else is compared, **including fields this file has
 * never heard of**.
 *
 * The same denylist W12 built for the same reason (its header explains the
 * choice at length). Restated rather than imported: importing a helper out of a
 * 2A spec is how the freeze W21 protects starts leaking.
 */
function withoutGenerationAxis(payload: PathPayload): PathPayload {
  const { generated_lessons, ...progress } = payload.progress;
  return {
    ...payload,
    progress,
    units: payload.units.map((unit) => ({
      ...unit,
      lessons: unit.lessons.map(({ generation_state, ...lesson }) => lesson),
    })),
  };
}

test.describe("W20 out-of-vocabulary edits are declined", { tag: "@w20" }, () => {
  test("every out-of-vocabulary ask gets the declined edit, and nothing moves", async ({
    page,
  }) => {
    const pathId = await createPath(page, uniqueTopic("Ferry timetables"));
    const total = await railLessons(page).count();
    const pathBefore = withoutGenerationAxis(await fetchPath(page, pathId));

    await openShapingRail(page);
    for (const ask of OUT_OF_VOCABULARY) {
      await askShaper(page, `${ask} ${FORCE_SHAPING_DECLINE}`);
    }

    // --- four replies, four declined edits, no cards -------------------------
    await expect(shapingReplies(page)).toHaveCount(OUT_OF_VOCABULARY.length);
    await expect(proposalCards(page)).toHaveCount(0);
    await expect(ghostRows(page)).toHaveCount(0);
    // Not an error and not a dead turn: the thread is ordinary, the composer is
    // open, and nothing anywhere is wearing the failure frame.
    await expect(page.getByTestId("shaping-rail-error")).toHaveCount(0);
    await expect(page.getByTestId("shaping-rail-send")).toBeVisible();

    // On screen the reply is rendered Markdown, so the emphasis is markup: what
    // the learner reads is the wording without its asterisks.
    const firstReply = shapingReplies(page).first();
    await expect(firstReply).toContainText("That is not one of the changes I can make to a path");
    await expect(firstReply).toContainText("add lessons");
    await expect(firstReply).not.toContainText("**");
    // It reads as neither of the other two "no"s.
    await expect(firstReply).not.toContainText(SHAPING_UPSTREAM_FAILURE_COPY);

    // --- the wire: ordinary messages, and no proposal on any of them ---------
    const { messages } = await fetchShapingConversation(page, pathId);
    const replies = messages.filter((message) => message.role === "tutor");
    expect(replies).toHaveLength(OUT_OF_VOCABULARY.length);
    for (const reply of replies) {
      // The whole record of a declined edit is the text the learner read: no
      // payload, and no machine-readable marker of any kind (docs/api.md).
      expect(reply.proposal).toBeNull();
      expect(reply.content).toBe(SHAPING_DECLINED_EDIT_REPLY);
    }

    // --- zero mutations ------------------------------------------------------
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(0);
    await expect(railLessons(page)).toHaveCount(total);
    expect(withoutGenerationAxis(await fetchPath(page, pathId))).toEqual(pathBefore);
  });

  test("a failed turn is an error frame with a retry — never a declined edit", async ({ page }) => {
    const pathId = await createPath(page, uniqueTopic("Bridge cables"));
    await openShapingRail(page);

    const question = `Add a lesson on load paths ${FORCE_SHAPING_FAILURE}`;
    await page.getByTestId("shaping-rail-input").fill(question);
    await page.getByTestId("shaping-rail-send").click();

    // --- an error, with a retry, and the question preserved ------------------
    const failure = page.getByTestId("shaping-rail-error");
    await expect(failure).toBeVisible({ timeout: ACTION_TIMEOUT });
    await expect(failure).toHaveAttribute("role", "alert");
    // The server's own learner-facing copy, verbatim — and nothing like the
    // declined edit above it, which is the distinction this test is for.
    await expect(failure).toContainText(SHAPING_UPSTREAM_FAILURE_COPY);
    await expect(failure).toContainText("Your question is still here.");
    await expect(failure).not.toContainText("What shaping can do is");
    await expect(page.getByTestId("shaping-rail-retry")).toBeEnabled();
    await expect(page.getByTestId("shaping-rail-input")).toHaveValue(question);

    // Not a dead bubble and not half a reply: the partial deltas the stub had
    // already streamed are gone, and nothing was appended to the thread.
    await expect(page.getByTestId("shaping-rail-streaming")).toHaveCount(0);
    await expect(page.getByTestId("shaping-rail-message")).toHaveCount(0);
    await expect(proposalCards(page)).toHaveCount(0);
    await expect(ghostRows(page)).toHaveCount(0);

    // --- nothing was persisted (a turn exists whole or not at all) -----------
    expect((await fetchShapingConversation(page, pathId)).messages).toEqual([]);
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(0);

    // --- retrying is a real round trip ---------------------------------------
    // It lands back on the failure, because the sentinel rides the question and
    // the stub is stateless about it (the reason W14 gives at length: fail →
    // retry → success on one turn belongs to the integration tier, where the
    // failure can be made transient instead of question-borne).
    await page.getByTestId("shaping-rail-retry").click();
    await expect(page.getByTestId("shaping-rail-error")).toBeVisible({ timeout: ACTION_TIMEOUT });
    await expect(page.getByTestId("shaping-rail-input")).toHaveValue(question);
    await expect(page.getByTestId("shaping-rail-message")).toHaveCount(0);

    // --- the way out: an ask without the sentinel still shapes the path ------
    // The failure left no residue the next turn has to work around.
    const card = await askForAddition(page);
    await expect(page.getByTestId("shaping-rail-error")).toHaveCount(0);
    await applyProposal(page, card);
    expect((await fetchChanges(page, pathId)).changes).toHaveLength(1);
  });
});
