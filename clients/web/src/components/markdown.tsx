// Markdown rendering for generated lesson content, styled in Nocturne.
//
// The lesson agent writes the Read passage (and the Quick check explanation) as
// GitHub-Flavored Markdown so it can use headings, lists, tables, fenced code
// blocks, and mermaid diagrams instead of one undifferentiated wall of prose.
// This module is the single place that turns that Markdown into DOM — every
// surface renders it the same way.
//
// **Safety.** The content is model-generated, so it is untrusted input. We rely on
// react-markdown's default posture and deliberately keep it: no `rehype-raw`, so
// embedded raw HTML is escaped to inert text rather than executed, and the
// built-in `urlTransform` strips dangerous URL protocols (`javascript:`, `data:`)
// from links. No Markdown text ever reaches `dangerouslySetInnerHTML` — the sole
// exception in this pipeline is the SVG mermaid itself emits, which mermaid
// sanitises (see `mermaid.tsx`). Adding a raw-HTML plugin would hand an
// LLM-authored string a script tag — don't.
//
// **Mobile-first.** The lesson column is 480px at its widest, so the two elements
// that can't reflow — fenced code blocks and tables — scroll horizontally inside
// their own container rather than pushing the page sideways.

import { isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Mermaid } from "./mermaid";

// The plugin list is module-level (not inline) so its identity is stable across
// renders and react-markdown doesn't rebuild the processor on every poll tick.
const REMARK_PLUGINS = [remarkGfm];

/**
 * The chart source of a ```mermaid fence, or null for any other `pre`.
 *
 * react-markdown gives every fenced block the same shape — a `pre` wrapping a
 * single `code` whose className carries `language-<tag>` and whose children are
 * the block's text. This reads that shape defensively: anything that doesn't
 * match exactly falls through to null and renders as an ordinary code block.
 */
function mermaidSource(children: ReactNode): string | null {
  if (!isValidElement<{ className?: string; children?: ReactNode }>(children)) return null;
  const { className, children: code } = children.props;
  if (typeof className !== "string" || !className.split(/\s+/).includes("language-mermaid")) {
    return null;
  }
  return typeof code === "string" ? code : null;
}

// Nocturne treatments for the block/inline elements the agent is told it may use
// (see `SYSTEM_PROMPT` in src/aleph/agents/lesson.py). Anything not listed falls
// back to react-markdown's plain element, which inherits the container's type
// scale — fine, because the prompt constrains the vocabulary to this set.
//
// Vertical rhythm is `space-y-4` on the container rather than per-element top
// margins, so an unexpected element ordering can't collapse the spacing.
const COMPONENTS: Components = {
  // The lesson title is the page's h1, so Markdown headings start a level down:
  // an agent-emitted `#` renders at h2's weight to keep the outline honest.
  h1: ({ children }) => (
    <h2 className="mt-6 text-xl font-semibold leading-snug tracking-tight text-porcelain first:mt-0">
      {children}
    </h2>
  ),
  h2: ({ children }) => (
    <h2 className="mt-6 text-xl font-semibold leading-snug tracking-tight text-porcelain first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-5 text-lg font-semibold leading-snug text-porcelain first:mt-0">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-4 text-base font-semibold leading-snug text-porcelain first:mt-0">
      {children}
    </h4>
  ),
  p: ({ children }) => <p className="leading-7 text-porcelain">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-porcelain">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => (
    <ul className="list-disc space-y-2 pl-5 leading-7 text-porcelain marker:text-teal">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal space-y-2 pl-5 leading-7 text-porcelain marker:text-mist">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-teal/60 pl-4 text-mist [&>p]:text-mist">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-divider" />,
  a: ({ href, children }) => (
    // Generated links point off-app; open them in a new tab and sever the opener
    // so the target can never reach back into the lesson via `window.opener`.
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-teal underline underline-offset-2 transition-colors hover:text-teal-bright"
    >
      {children}
    </a>
  ),
  // Inline code. Fenced blocks reach here too, but wrapped in `pre`, whose
  // descendant selectors below neutralise this chip treatment.
  code: ({ children }) => (
    <code className="rounded-sm bg-elevated px-1.5 py-0.5 font-mono text-[0.9em] text-teal">
      {children}
    </code>
  ),
  // A fenced code block: the one element with no upper bound on line length, so
  // it scrolls on its own axis. The `[&_code]` resets undo the inline chip styling
  // for the `code` element react-markdown nests inside every `pre`.
  //
  // ```mermaid is the one fence that isn't code: it is diagram source, and it
  // routes to the lazily-loaded renderer instead (which draws its own container,
  // including the code-block fallback when a chart won't parse).
  pre: ({ children }) => {
    const chart = mermaidSource(children);
    if (chart !== null) return <Mermaid source={chart} />;
    return (
      <pre className="overflow-x-auto rounded-md border border-divider bg-elevated p-4 font-mono text-[13px] leading-6 text-porcelain [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-[inherit] [&_code]:text-porcelain">
        {children}
      </pre>
    );
  },
  // GFM tables (remark-gfm). Same reflow problem as code blocks: the wrapper owns
  // the horizontal scroll so a wide table never widens the page on a phone.
  table: ({ children }) => (
    <div className="overflow-x-auto rounded-md border border-divider">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-surface">{children}</thead>,
  th: ({ children }) => (
    <th className="border-b border-divider px-3 py-2 text-left font-semibold text-porcelain">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-divider/50 px-3 py-2 align-top text-mist">{children}</td>
  ),
};

/**
 * Render model-generated Markdown as Nocturne-styled DOM.
 *
 * `className` is appended to the block container, so a caller can set the type
 * scale it wants (the Read passage reads at `text-base`, the Quick check
 * explanation at `text-sm`) without restating the element treatments. `testid`
 * lands on that same container, keeping every existing `lesson-read-passage` /
 * `outcome-explanation` selector pointing at one stable node.
 */
export function Markdown({
  children,
  className = "",
  testid,
}: {
  children: string;
  className?: string;
  testid?: string;
}) {
  return (
    <div data-testid={testid} className={`space-y-4 ${className}`}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
