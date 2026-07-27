import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./markdown";

// The Markdown renderer used for generated lesson content (the Read passage and
// the Quick check explanation). Two things are under test: that the structure the
// lesson agent is told it may emit actually reaches the DOM as elements, and that
// the untrusted-input posture holds — model-generated Markdown must never become
// executable markup.

function renderMarkdown(source: string): HTMLElement {
  render(<Markdown testid="md">{source}</Markdown>);
  return screen.getByTestId("md");
}

describe("Markdown", () => {
  it("renders paragraphs, and separates them the way blank lines ask", () => {
    const root = renderMarkdown("First paragraph.\n\nSecond paragraph.");

    const paragraphs = root.querySelectorAll("p");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0].textContent).toBe("First paragraph.");
    expect(paragraphs[1].textContent).toBe("Second paragraph.");
  });

  it("renders the block vocabulary the lesson prompt allows", () => {
    const root = renderMarkdown(
      [
        "## Section",
        "",
        "### Subsection",
        "",
        "Prose with **bold**, *italic*, and `inline code`.",
        "",
        "- first bullet",
        "- second bullet",
        "",
        "1. first step",
        "2. second step",
        "",
        "> An aside.",
      ].join("\n"),
    );

    expect(root.querySelector("h2")?.textContent).toBe("Section");
    expect(root.querySelector("h3")?.textContent).toBe("Subsection");
    expect(root.querySelector("strong")?.textContent).toBe("bold");
    expect(root.querySelector("em")?.textContent).toBe("italic");
    expect(root.querySelector("code")?.textContent).toBe("inline code");
    expect(root.querySelectorAll("ul > li")).toHaveLength(2);
    expect(root.querySelectorAll("ol > li")).toHaveLength(2);
    expect(root.querySelector("blockquote")?.textContent).toContain("An aside.");
  });

  it("renders a fenced code block as a scrollable pre, contents verbatim", () => {
    const root = renderMarkdown(
      ["```python", "def add(a, b):", "    return a + b", "```"].join("\n"),
    );

    const pre = root.querySelector("pre");
    expect(pre).not.toBeNull();
    // Whitespace and line breaks survive — the point of a code block.
    expect(pre?.textContent).toBe("def add(a, b):\n    return a + b\n");
    // A phone-width column can't reflow code, so the block owns its own x-scroll.
    expect(pre?.className).toContain("overflow-x-auto");
  });

  it("[gfm] renders tables, which plain CommonMark would not", () => {
    const root = renderMarkdown(
      ["| Idea | Why |", "| --- | --- |", "| Ownership | Memory safety |"].join("\n"),
    );

    expect(root.querySelectorAll("th")).toHaveLength(2);
    expect(root.querySelectorAll("tbody td")).toHaveLength(2);
    expect(root.querySelector("tbody td")?.textContent).toBe("Ownership");
  });

  it("routes a ```mermaid fence to the diagram renderer, not to a code block", () => {
    const root = renderMarkdown(
      ["```mermaid", "flowchart TD", '    A["Start"] --> B["End"]', "```"].join("\n"),
    );

    // jsdom can't draw SVG, so the renderer sits in its source fallback — the
    // point here is only that the fence was routed to it at all.
    const diagram = root.querySelector("[data-testid='mermaid-diagram']");
    expect(diagram).not.toBeNull();
    expect(diagram?.textContent).toContain("flowchart TD");
  });

  it("leaves every other fenced block as an ordinary code block", () => {
    const root = renderMarkdown(
      ["```python", "x = 1", "```", "", "```", "no language", "```"].join("\n"),
    );

    expect(root.querySelector("[data-testid='mermaid-diagram']")).toBeNull();
    expect(root.querySelectorAll("pre")).toHaveLength(2);
  });

  it("[security] escapes raw HTML to text instead of rendering it", () => {
    // No rehype-raw: an LLM-authored (or prompt-injected) tag must not become
    // DOM. react-markdown escapes it, so it survives as inert visible text.
    const root = renderMarkdown('Before <img src="x" onerror="alert(1)"> after.');

    expect(root.querySelector("img")).toBeNull();
    expect(root.innerHTML).toContain("&lt;img");
    expect(root.textContent).toContain('<img src="x" onerror="alert(1)">');
  });

  it("[security] strips dangerous link protocols and isolates external links", () => {
    const root = renderMarkdown("[safe](https://example.com) and [unsafe](javascript:alert(1))");

    const [safe, unsafe] = Array.from(root.querySelectorAll("a"));
    expect(safe.getAttribute("href")).toBe("https://example.com");
    expect(safe.getAttribute("rel")).toBe("noopener noreferrer");
    expect(safe.getAttribute("target")).toBe("_blank");
    // react-markdown's default urlTransform empties a javascript: URL.
    expect(unsafe.getAttribute("href")).not.toContain("javascript:");
  });

  it("puts the testid on one stable container so callers keep their selectors", () => {
    const root = renderMarkdown("## Heading\n\nBody.");

    expect(screen.getAllByTestId("md")).toHaveLength(1);
    expect(root.textContent).toContain("Heading");
    expect(root.textContent).toContain("Body.");
  });
});
