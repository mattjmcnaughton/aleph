import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  EXAMPLE_FIRST_DRAFT,
  EXAMPLE_IDEA,
  EXAMPLE_QUESTIONS,
  EXAMPLE_SHARPENED_DRAFT,
  configurePathDrafts,
  draftQuestionBodies,
  pathDraftBodies,
} from "../mocks/path-drafts";
import { createPathBodies } from "../mocks/paths";
import { App } from "./app";

// New path, drafted from an idea (CONTEXT.md: *Idea*, *Path draft*,
// *Interview*). Driven through the real router and MSW: nothing reaches the
// form until "Use this draft", and nothing is created until "Build my path".

function goto(url: string) {
  window.history.pushState({}, "", url);
  render(<App />);
}

async function draftFromIdea() {
  goto("/new");
  const idea = await screen.findByRole("textbox", { name: "Start from an idea" });
  fireEvent.change(idea, { target: { value: EXAMPLE_IDEA } });
  fireEvent.click(screen.getByRole("button", { name: "Draft it for me" }));
  return screen.findByTestId("path-draft");
}

function click(name: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}

describe("New path — draft from an idea", () => {
  it("opens on the idea step, with the form one tap away", async () => {
    goto("/new");
    await screen.findByTestId("path-draft-idea");
    expect(screen.queryByRole("textbox", { name: /^topic$/i })).toBeNull();

    click("I'll fill in the form myself");
    await screen.findByRole("textbox", { name: /^topic$/i });
    expect(screen.queryByTestId("draft-filled-notice")).toBeNull();
  });

  it("drafts from the idea, and Use this draft fills the form without creating anything", async () => {
    const card = await draftFromIdea();
    expect(pathDraftBodies()).toEqual([{ idea: EXAMPLE_IDEA }]);
    expect(card.textContent).toContain(EXAMPLE_FIRST_DRAFT.topic);
    expect(card.textContent).toContain("Some experience");

    click("Use this draft");

    const topic = (await screen.findByRole("textbox", { name: /^topic$/i })) as HTMLInputElement;
    expect(topic.value).toBe(EXAMPLE_FIRST_DRAFT.topic);
    expect(
      (screen.getByRole("textbox", { name: "Additional guidance" }) as HTMLTextAreaElement).value,
    ).toBe(EXAMPLE_FIRST_DRAFT.guidance);
    expect(
      (screen.getByRole("textbox", { name: "Teaching style" }) as HTMLTextAreaElement).value,
    ).toBe(EXAMPLE_FIRST_DRAFT.teaching_style);
    expect(
      (screen.getByRole("radio", { name: /some experience/i }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(screen.getByTestId("draft-filled-notice").textContent).toContain(
      "Filled from Aleph's draft. Edit anything before you build.",
    );
    expect(createPathBodies()).toHaveLength(0);

    // See draft goes back to the same draft; nothing is redrafted.
    click("See draft");
    await screen.findByTestId("path-draft");
    expect(pathDraftBodies()).toHaveLength(1);

    click("Use this draft");
    fireEvent.click(await screen.findByRole("button", { name: /build my path/i }));
    await waitFor(() => expect(createPathBodies()).toHaveLength(1));
    expect(createPathBodies()[0]).toMatchObject({
      topic: EXAMPLE_FIRST_DRAFT.topic,
      level: "some_experience",
      guidance: EXAMPLE_FIRST_DRAFT.guidance,
      teaching_style: EXAMPLE_FIRST_DRAFT.teaching_style,
    });
  });

  it("interviews one question at a time, then redrafts with the answers and marks what changed", async () => {
    await draftFromIdea();
    click("Sharpen it: ask me a few questions");

    const interview = await screen.findByTestId("draft-interview");
    expect(draftQuestionBodies()).toEqual([
      { idea: EXAMPLE_IDEA, draft: EXAMPLE_FIRST_DRAFT, answers: [] },
    ]);
    expect(interview.textContent).toContain(`Question 1 of ${EXAMPLE_QUESTIONS.length}`);

    fireEvent.click(screen.getByRole("radio", { name: EXAMPLE_QUESTIONS[0].options[0] }));
    click("Next");
    // Question 2: own words only.
    await screen.findByRole("heading", { name: EXAMPLE_QUESTIONS[1].prompt });
    fireEvent.change(screen.getByRole("textbox", { name: "Or say it in your own words…" }), {
      target: { value: "I can run it" },
    });
    click("Next");
    // Question 3: skipped.
    click("Next");
    // Question 4 is multi-select.
    await screen.findByRole("heading", { name: EXAMPLE_QUESTIONS[3].prompt });
    fireEvent.click(screen.getByRole("checkbox", { name: EXAMPLE_QUESTIONS[3].options[1] }));
    fireEvent.click(screen.getByRole("checkbox", { name: EXAMPLE_QUESTIONS[3].options[0] }));
    click("Redraft");

    const card = await screen.findByTestId("path-draft");
    expect(pathDraftBodies()[1]).toEqual({
      idea: EXAMPLE_IDEA,
      previous: EXAMPLE_FIRST_DRAFT,
      answers: [
        { question: EXAMPLE_QUESTIONS[0].prompt, answer: EXAMPLE_QUESTIONS[0].options[0] },
        { question: EXAMPLE_QUESTIONS[1].prompt, answer: "I can run it" },
        {
          question: EXAMPLE_QUESTIONS[3].prompt,
          answer: `${EXAMPLE_QUESTIONS[3].options[0]}; ${EXAMPLE_QUESTIONS[3].options[1]}`,
        },
      ],
    });
    expect(card.textContent).toContain("Aleph's draft, sharpened");
    expect(card.textContent).toContain("Redrafted from your idea and 3 answers.");
    expect(card.textContent).toContain(EXAMPLE_SHARPENED_DRAFT.topic);
    expect(within(card).getByTestId("topic-changed").textContent).toBe("From your answers");
    const marked = within(card)
      .getAllByTestId("guidance-line")
      .filter((line) => line.getAttribute("data-changed") === "true")
      .map((line) => line.textContent);
    expect(marked).toHaveLength(3);
    expect(marked[0]).toMatch(/^You can run EXPLAIN/);

    // Another round carries every earlier answer.
    click("Ask me more");
    await screen.findByTestId("draft-interview");
    expect(draftQuestionBodies()[1].answers).toHaveLength(3);
    expect(draftQuestionBodies()[1].draft).toEqual(EXAMPLE_SHARPENED_DRAFT);
  });

  it("can stop the interview early and redraft with what was said", async () => {
    await draftFromIdea();
    click("Sharpen it: ask me a few questions");
    await screen.findByTestId("draft-interview");
    fireEvent.click(screen.getByRole("radio", { name: EXAMPLE_QUESTIONS[0].options[1] }));
    click("That's enough, redraft with what I've said");

    await screen.findByText("Aleph's draft, sharpened");
    expect(pathDraftBodies()[1].answers).toEqual([
      { question: EXAMPLE_QUESTIONS[0].prompt, answer: EXAMPLE_QUESTIONS[0].options[1] },
    ]);
    click("Back to the first draft");
    const card = await screen.findByTestId("path-draft");
    expect(card.textContent).toContain(EXAMPLE_FIRST_DRAFT.topic);
    expect(card.textContent).toContain("Aleph's draft");
    expect(within(card).queryByTestId("topic-changed")).toBeNull();
  });

  it("shows a failed draft inline with Try again, keeping the idea", async () => {
    configurePathDrafts({ draftFailures: 1 });
    goto("/new");
    const idea = (await screen.findByRole("textbox", {
      name: "Start from an idea",
    })) as HTMLTextAreaElement;
    fireEvent.change(idea, { target: { value: EXAMPLE_IDEA } });
    click("Draft it for me");

    const alert = await screen.findByTestId("path-draft-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(
      (screen.getByRole("textbox", { name: "Start from an idea" }) as HTMLTextAreaElement).value,
    ).toBe(EXAMPLE_IDEA);

    click("Try again");
    await screen.findByTestId("path-draft");
    expect(pathDraftBodies()).toHaveLength(2);
  });

  it("keeps the answers when a redraft fails, and retries with them", async () => {
    await draftFromIdea();
    click("Sharpen it: ask me a few questions");
    await screen.findByTestId("draft-interview");
    fireEvent.click(screen.getByRole("radio", { name: EXAMPLE_QUESTIONS[0].options[0] }));
    configurePathDrafts({ draftFailures: 1 });
    click("That's enough, redraft with what I've said");

    await screen.findByTestId("path-draft-error");
    click("Try again");
    await screen.findByText("Aleph's draft, sharpened");
    expect(pathDraftBodies()[2].answers).toEqual(pathDraftBodies()[1].answers);
    expect(pathDraftBodies()[2].answers).toHaveLength(1);
  });

  it("shows failed questions inline with Try again", async () => {
    configurePathDrafts({ questionFailures: 1 });
    await draftFromIdea();
    click("Sharpen it: ask me a few questions");

    await screen.findByTestId("path-draft-error");
    click("Try again");
    await screen.findByRole("heading", { name: EXAMPLE_QUESTIONS[0].prompt });
  });

  it("skips the idea step when /new is prefilled", async () => {
    goto("/new?topic=Async%20Rust");
    const topic = (await screen.findByRole("textbox", { name: /^topic$/i })) as HTMLInputElement;
    expect(topic.value).toBe("Async Rust");
    expect(screen.queryByTestId("path-draft-idea")).toBeNull();
    expect(screen.queryByTestId("draft-filled-notice")).toBeNull();
  });
});
