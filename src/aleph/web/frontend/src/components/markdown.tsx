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

import { createContext, isValidElement, type ReactNode, useContext } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LessonVisual as Visual } from "../lib/api";
import { LessonVisual } from "./lesson-visual";
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

// A stored visual's id: the canonical lowercase UUID the server writes into the
// fence (`domains/visuals.py`). Anything else in a ```visual info string is not
// an id we can resolve.
const VISUAL_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * For a ```visual fence: the id it names (or null when malformed). Undefined for
 * any other `pre`. The id is the fence's info-string *meta* — mdast keeps the
 * first word as the language and the rest on `node.data.meta`.
 */
function visualFenceId(children: ReactNode): string | null | undefined {
  if (
    !isValidElement<{
      className?: string;
      node?: { data?: { meta?: unknown } };
    }>(children)
  ) {
    return undefined;
  }
  const { className, node } = children.props;
  if (typeof className !== "string" || !className.split(/\s+/).includes("language-visual")) {
    return undefined;
  }
  const meta = node?.data?.meta;
  const id = typeof meta === "string" ? meta.trim() : "";
  return VISUAL_ID_RE.test(id) ? id : null;
}

// The visuals a passage's fences may name, provided by `Markdown` so the
// module-level `COMPONENTS` can stay stable across renders.
const VisualsContext = createContext<readonly Visual[]>([]);

/** A ```visual fence: the named visual in its sandbox, or nothing at all. */
function VisualFence({ id }: { id: string | null }) {
  const visuals = useContext(VisualsContext);
  const visual = id === null ? undefined : visuals.find((candidate) => candidate.id === id);
  // An unknown or malformed fence renders nothing: the passage teaches without
  // it, and a broken block would only be noise.
  if (visual === undefined) return null;
  return <LessonVisual key={visual.id} visual={visual} />;
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
    <h2 className="!mt-9 font-serif text-[1.3125rem] font-semibold leading-snug tracking-[-0.01em] text-porcelain first:!mt-0">
      {children}
    </h2>
  ),
  h2: ({ children }) => (
    <h2 className="!mt-9 font-serif text-[1.3125rem] font-semibold leading-snug tracking-[-0.01em] text-porcelain first:!mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="!mt-7 font-serif text-lg font-semibold leading-snug text-porcelain first:!mt-0">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="!mt-6 text-base font-semibold leading-snug text-porcelain first:!mt-0">
      {children}
    </h4>
  ),
  p: ({ children }) => <p className="text-porcelain">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-porcelain">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => (
    <ul className="list-disc space-y-1.5 pl-5 text-porcelain marker:text-slate">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal space-y-1.5 pl-5 text-porcelain marker:text-slate">{children}</ol>
  ),
  li: ({ children }) => <li className="pl-1">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-faint pl-5 text-mist [&>p]:text-mist">
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
      className="text-porcelain underline decoration-teal decoration-1 underline-offset-[3px] transition-colors hover:text-teal-bright"
    >
      {children}
    </a>
  ),
  // Inline code. Fenced blocks reach here too, but wrapped in `pre`, whose
  // descendant selectors below neutralise this chip treatment.
  code: ({ children }) => (
    <code className="rounded bg-porcelain/[0.09] px-[0.35em] py-[0.12em] font-mono text-[0.82em] text-porcelain">
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
    // ```visual <id> places an interactive Visual (lesson-visual.tsx): its
    // source runs only in that component's sandboxed frame, never here.
    const visualId = visualFenceId(children);
    if (visualId !== undefined) return <VisualFence id={visualId} />;
    const chart = mermaidSource(children);
    if (chart !== null) return <Mermaid source={chart} />;
    return (
      <pre className="overflow-x-auto rounded-md bg-surface p-4 font-mono text-[13px] leading-6 text-porcelain [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-[inherit] [&_code]:text-porcelain">
        {children}
      </pre>
    );
  },
  // GFM tables (remark-gfm). Same reflow problem as code blocks: the wrapper owns
  // the horizontal scroll so a wide table never widens the page on a phone.
  table: ({ children }) => (
    <div className="overflow-x-auto border-y border-divider font-sans">
      <table className="w-full border-collapse text-[15px] leading-6 [&_tr:last-child>td]:border-b-0">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => <thead>{children}</thead>,
  th: ({ children }) => (
    <th className="border-b border-divider py-2.5 pr-4 text-left text-[13px] font-semibold text-mist last:pr-0">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-divider/60 py-2.5 pr-4 align-top text-porcelain last:pr-0">
      {children}
    </td>
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
  visuals = NO_VISUALS,
}: {
  children: string;
  className?: string;
  testid?: string;
  /** The Visuals the passage's ```visual <id> fences may name (lesson detail). */
  visuals?: readonly Visual[];
}) {
  return (
    <div data-testid={testid} className={`space-y-[1.05em] ${className}`}>
      <VisualsContext.Provider value={visuals}>
        <ReactMarkdown
          remarkPlugins={REMARK_PLUGINS}
          components={COMPONENTS}
          disallowedElements={["img"]}
        >
          {children}
        </ReactMarkdown>
      </VisualsContext.Provider>
    </div>
  );
}

const NO_VISUALS: readonly Visual[] = [];
