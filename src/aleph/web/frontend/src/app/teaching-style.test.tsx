import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { appendStarter, starterFits } from "../components/teaching-style-field";
import { writtenAheadCount } from "../components/teaching-style-card";
import {
  COMPLETE_PATH_UNITS,
  FRESH_PATH_UNITS,
  MID_PATH_UNITS,
  configurePaths,
  seedPath,
  teachingStyleRequestBodies,
} from "../mocks/paths";
import type { PathUnit } from "../lib/api";
import { TEACHING_STYLE_MAX_LENGTH } from "../lib/onboarding";
import { App } from "./app";

// Teaching style (docs/CONTEXT.md): shown on the path view, editable in place,
// and forward-only. The card reads and writes through the real router, real
// TanStack Query cache, and the MSW fake's `PUT /paths/{id}/teaching-style`.

async function openPath(teachingStyle: string | null = null, units: PathUnit[] = MID_PATH_UNITS) {
  seedPath({
    id: "style-path",
    topic: "Rust ownership",
    level: "some_experience",
    units,
    teachingStyle,
  });
  window.history.pushState({}, "", "/paths/style-path");
  render(<App />);
  return screen.findByTestId("teaching-style");
}

/** `MID_PATH_UNITS` with its one unwritten lesson caught mid-generation. */
function withLastLessonGenerating(units: PathUnit[]): PathUnit[] {
  return units.map((unit) => ({
    ...unit,
    lessons: unit.lessons.map((lesson) =>
      lesson.generation_state === "ungenerated"
        ? { ...lesson, generation_state: "generating" as const }
        : lesson,
    ),
  }));
}

function styleInput() {
  return screen.getByLabelText("Teaching style") as HTMLTextAreaElement;
}

describe("Teaching style — path view", () => {
  it("shows an empty state with an Add action when none is set", async () => {
    await openPath();

    expect(screen.getByTestId("teaching-style-text").textContent).toMatch(/none yet/i);
    expect(screen.getByTestId("teaching-style-edit").textContent).toBe("Add");
  });

  it("shows the current style with an Edit action", async () => {
    await openPath("Show code for every idea.");

    expect(screen.getByTestId("teaching-style-text").textContent).toBe("Show code for every idea.");
    expect(screen.getByTestId("teaching-style-edit").textContent).toBe("Edit");
  });

  it("saves an edit, sends it trimmed, and shows the new value", async () => {
    await openPath("Show code for every idea.");

    fireEvent.click(screen.getByTestId("teaching-style-edit"));
    expect(styleInput().value).toBe("Show code for every idea.");
    fireEvent.change(styleInput(), { target: { value: "  Use real crates as examples.  " } });
    fireEvent.click(screen.getByTestId("teaching-style-save"));

    await waitFor(() =>
      expect(screen.getByTestId("teaching-style-text").textContent).toBe(
        "Use real crates as examples.",
      ),
    );
    expect(teachingStyleRequestBodies()).toEqual([
      { teaching_style: "Use real crates as examples." },
    ]);
  });

  it("clears the style by sending null when the box is saved empty", async () => {
    await openPath("Show code for every idea.");

    fireEvent.click(screen.getByTestId("teaching-style-edit"));
    fireEvent.change(styleInput(), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("teaching-style-save"));

    await waitFor(() =>
      expect(screen.getByTestId("teaching-style-text").textContent).toMatch(/none yet/i),
    );
    expect(teachingStyleRequestBodies()).toEqual([{ teaching_style: null }]);
  });

  it("Cancel sends nothing and keeps the old value", async () => {
    await openPath("Show code for every idea.");

    fireEvent.click(screen.getByTestId("teaching-style-edit"));
    fireEvent.change(styleInput(), { target: { value: "Something else" } });
    fireEvent.click(screen.getByTestId("teaching-style-cancel"));

    expect(screen.getByTestId("teaching-style-text").textContent).toBe("Show code for every idea.");
    expect(teachingStyleRequestBodies()).toEqual([]);
  });

  it("keeps the form open with the draft and shows an error when saving fails", async () => {
    configurePaths({ teachingStyleFails: true });
    await openPath();

    fireEvent.click(screen.getByTestId("teaching-style-edit"));
    fireEvent.change(styleInput(), { target: { value: "More analogies." } });
    fireEvent.click(screen.getByTestId("teaching-style-save"));

    await screen.findByTestId("teaching-style-error");
    expect(styleInput().value).toBe("More analogies.");
  });

  it("says the change is forward-only and counts the lessons it will not reach", async () => {
    await openPath();

    fireEvent.click(screen.getByTestId("teaching-style-edit"));

    expect(screen.getByTestId("teaching-style-scope").textContent).toBe(
      "Applies to lessons written from now on. 1 lesson is already written and keeps its current style.",
    );
  });

  it("counts a lesson being written as one that keeps its style (plural copy)", async () => {
    await openPath(null, withLastLessonGenerating(MID_PATH_UNITS));

    fireEvent.click(screen.getByTestId("teaching-style-edit"));

    expect(screen.getByTestId("teaching-style-scope").textContent).toBe(
      "Applies to lessons written from now on. 2 lessons are already written and keep their current style.",
    );
  });

  it("disables a starter that would push the text past the limit", async () => {
    await openPath();

    fireEvent.click(screen.getByTestId("teaching-style-edit"));
    const nearlyFull = "x".repeat(TEACHING_STYLE_MAX_LENGTH - 10);
    fireEvent.change(styleInput(), { target: { value: nearlyFull } });
    const codeFirst = screen.getByRole("button", { name: "Code first" }) as HTMLButtonElement;
    fireEvent.click(codeFirst);

    expect(codeFirst.disabled).toBe(true);
    expect(styleInput().value).toBe(nearlyFull);
  });

  it("a starter appends its text to what is already typed", async () => {
    await openPath();

    fireEvent.click(screen.getByTestId("teaching-style-edit"));
    fireEvent.change(styleInput(), { target: { value: "Compare to C++." } });
    fireEvent.click(screen.getByRole("button", { name: "Code first" }));

    expect(styleInput().value).toBe("Compare to C++. Show code for every idea.");
  });
});

describe("appendStarter", () => {
  it("adds to an empty box without leading space", () => {
    expect(appendStarter("  ", "More examples.")).toBe("More examples.");
  });

  it("does not add the same starter twice", () => {
    expect(appendStarter("More examples.", "More examples.")).toBe("More examples.");
  });
});

describe("starterFits", () => {
  it("allows a starter that lands exactly on the limit and refuses one past it", () => {
    const text = "Show code for every idea.";
    const exact = "x".repeat(TEACHING_STYLE_MAX_LENGTH - text.length - 1);
    expect(starterFits(exact, text)).toBe(true);
    expect(starterFits(`${exact}x`, text)).toBe(false);
  });
});

describe("writtenAheadCount", () => {
  it("counts written lessons the learner has not finished", () => {
    expect(writtenAheadCount({ units: MID_PATH_UNITS })).toBe(1);
  });

  it("counts a lesson that is being written", () => {
    expect(writtenAheadCount({ units: withLastLessonGenerating(MID_PATH_UNITS) })).toBe(2);
  });

  it("is zero when nothing ahead is written or everything is complete", () => {
    expect(writtenAheadCount({ units: COMPLETE_PATH_UNITS })).toBe(0);
    expect(
      writtenAheadCount({
        units: FRESH_PATH_UNITS.map((unit) => ({
          ...unit,
          lessons: unit.lessons.map((lesson) => ({
            ...lesson,
            generation_state: "ungenerated" as const,
          })),
        })),
      }),
    ).toBe(0);
  });
});
