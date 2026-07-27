import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Mermaid } from "./mermaid";

// The mermaid renderer behind ```mermaid fences. jsdom cannot lay out SVG, so the
// library itself is mocked here: what's under test is this module's contract —
// lazy load, render the sanitised SVG on success, and fall back to legible source
// on any failure — not mermaid's drawing. The real library is exercised in a real
// browser by the e2e suite, which renders the stub model's diagram.

const FLOWCHART = 'flowchart TD\n    A["Start"] --> B["End"]';

const parse = vi.fn();
const renderChart = vi.fn();

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    parse: (...args: unknown[]) => parse(...args),
    render: (...args: unknown[]) => renderChart(...args),
  },
}));

afterEach(() => {
  parse.mockReset();
  renderChart.mockReset();
});

describe("Mermaid", () => {
  it("renders the SVG mermaid returns", async () => {
    parse.mockResolvedValue(true);
    renderChart.mockResolvedValue({ svg: '<svg role="img"><title>diagram</title></svg>' });

    render(<Mermaid source={FLOWCHART} />);

    // Re-query rather than holding the node: success swaps the fallback `pre`
    // for a `figure`, so the first element found is not the final one.
    await waitFor(() =>
      expect(screen.getByTestId("mermaid-diagram").getAttribute("data-mermaid-status")).toBe(
        "rendered",
      ),
    );
    const figure = screen.getByTestId("mermaid-diagram");
    expect(figure.tagName).toBe("FIGURE");
    expect(figure.querySelector("svg")).not.toBeNull();
    // The chart source is validated before the expensive render.
    expect(parse).toHaveBeenCalledWith(FLOWCHART);
    expect(renderChart).toHaveBeenCalled();
  });

  it("shows the source while the lazy import is still resolving", () => {
    parse.mockReturnValue(new Promise(() => {}));

    render(<Mermaid source={FLOWCHART} />);

    const block = screen.getByTestId("mermaid-diagram");
    expect(block.getAttribute("data-mermaid-status")).toBe("pending");
    expect(block.textContent).toContain("flowchart TD");
  });

  it("falls back to the source when the chart does not parse", async () => {
    // The realistic failure: an LLM writes subtly invalid mermaid. The lesson
    // must stay readable, so the learner gets the diagram's text, not an error.
    parse.mockRejectedValue(new Error("Parse error on line 2"));

    render(<Mermaid source={"flowchart TD\n    A -->"} />);

    const block = await screen.findByTestId("mermaid-diagram");
    await waitFor(() => expect(block.getAttribute("data-mermaid-status")).toBe("failed"));
    expect(block.tagName).toBe("PRE");
    expect(block.textContent).toContain("flowchart TD");
    // A chart that fails to parse is never handed to the renderer.
    expect(renderChart).not.toHaveBeenCalled();
  });

  it("falls back to the source when rendering itself throws", async () => {
    parse.mockResolvedValue(true);
    renderChart.mockRejectedValue(new Error("getBBox is not a function"));

    render(<Mermaid source={FLOWCHART} />);

    const block = await screen.findByTestId("mermaid-diagram");
    await waitFor(() => expect(block.getAttribute("data-mermaid-status")).toBe("failed"));
    expect(block.textContent).toContain("flowchart TD");
  });
});
