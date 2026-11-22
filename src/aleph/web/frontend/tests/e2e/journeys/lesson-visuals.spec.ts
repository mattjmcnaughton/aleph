// Lesson visuals (docs/tdds/lesson-visuals.md), in a real browser against the
// stub backend.
//
// * The stub gives lesson 1 a compound-interest Visual. It must reach `ready`
//   (the frame's own `kit.ready()` crossed the sandbox boundary as a message),
//   size itself, and respond to the learner: dragging a slider inside the frame
//   changes what the frame draws.
// * `[force-visual-probe]` swaps in a hostile visual that tries every way out of
//   the sandbox and writes the result into its own DOM. Every attempt must read
//   `blocked`, and the parent page must be untouched. This is the claim the
//   whole security model rests on (opaque origin + no-network CSP), so it is
//   asserted in Chromium itself rather than in jsdom.

import { expect, type FrameLocator, type Page, test } from "@playwright/test";
import { DEV_STORAGE_STATE } from "../fixtures/auth";
import {
  ACTION_TIMEOUT,
  createPath,
  expectLessonContent,
  openLessonAt,
  uniqueTopic,
} from "../fixtures/journey";

test.use({ storageState: DEV_STORAGE_STATE });

function visualFrame(page: Page): FrameLocator {
  return page.getByTestId("lesson-visual").frameLocator("iframe");
}

async function expectVisualReady(page: Page): Promise<void> {
  const figure = page.getByTestId("lesson-visual");
  await expect(figure).toHaveAttribute("data-visual-status", "ready", {
    timeout: ACTION_TIMEOUT,
  });
  const iframe = figure.locator("iframe");
  // Chrome lays out an offscreen cross-origin frame only once it is in view, so
  // its real height arrives when the learner reaches it — scroll there, as they would.
  await iframe.scrollIntoViewIfNeeded();
  // The regression guard, in the real DOM: never allow-same-origin.
  await expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
  // Sized by the frame's own height message, not stuck at the 120px default.
  await expect
    .poll(async () => (await iframe.boundingBox())?.height ?? 0, { timeout: ACTION_TIMEOUT })
    .toBeGreaterThan(150);
}

test.describe("Lesson visuals", { tag: "@visuals" }, () => {
  test("a generated lesson's visual becomes interactive", async ({ page }) => {
    await createPath(page, uniqueTopic("Compound interest"));
    await openLessonAt(page, 0);
    await expectLessonContent(page);

    // The fence was resolved to a stored UUID, not the agent's placeholder.
    const id = await page.getByTestId("lesson-visual").getAttribute("data-visual-id");
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    await expectVisualReady(page);

    const frame = visualFrame(page);
    const compound = frame.locator("#compound-v");
    await expect(frame.locator("#years-v")).toHaveText("10 years");
    await expect(compound).toHaveText("$1,967");

    // Drag the Years slider to its far end, inside the frame.
    const slider = frame.locator("#years");
    const box = await slider.boundingBox();
    if (box === null) throw new Error("the Years slider has no box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width + 40, box.y + box.height / 2, { steps: 12 });
    await page.mouse.up();

    await expect(frame.locator("#years-v")).toHaveText("40 years");
    await expect(compound).toHaveText("$14,974");
    await expect(frame.locator("#simple-v")).toHaveText("$3,800");
  });

  test("a hostile visual cannot reach the page, its cookies, storage or the network", async ({
    page,
  }) => {
    await createPath(page, uniqueTopic("Sandbox [force-visual-probe]"));
    await openLessonAt(page, 0);
    await expectLessonContent(page);
    const titleBefore = await page.title();
    await expectVisualReady(page);

    const frame = visualFrame(page);
    const probes = [
      "parent.document",
      "parent.document.title",
      "top.location",
      "document.cookie",
      "localStorage",
      "fetch",
    ];
    for (const probe of probes) {
      await expect(frame.locator(`li[data-probe="${probe}"]`)).toHaveText(`${probe}: blocked`, {
        timeout: ACTION_TIMEOUT,
      });
    }
    // And from the outside: the page's title was not rewritten, and it is
    // still the page it was.
    expect(await page.title()).toBe(titleBefore);
    await expect(page.getByTestId("lesson-read-passage")).toBeVisible();
  });
});
