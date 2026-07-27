// Mermaid diagram rendering for generated lesson content.
//
// The lesson agent may emit a ```mermaid fenced block when a diagram teaches
// something prose can't — a state machine, a request flow, a type hierarchy.
// `components/markdown.tsx` routes those blocks here; everything else about the
// Markdown pipeline is unchanged.
//
// **Mermaid is loaded lazily and never eagerly.** The library is ~500 kB of the
// bundle, and most lessons have no diagram at all, so the `import("mermaid")`
// below is dynamic: Vite code-splits it, and a learner only pays for it on a
// lesson that actually draws something. `MERMAID` memoises the import + one-time
// `initialize` so N diagrams on one page share a single load.
//
// **Safety.** A diagram is model-generated text, same as the passage around it.
// `securityLevel: "strict"` is mermaid's own sandbox: it runs DOMPurify over the
// SVG it produces, disables HTML labels, and drops `click` interaction
// directives. That sanitised SVG string is the one place in this codebase where
// `dangerouslySetInnerHTML` is used — mermaid's API returns markup, not nodes,
// and there is no way to mount it otherwise. The invariant to preserve: only
// mermaid's own output is ever passed to it, never the raw chart source.
//
// **Failure degrades, it never dead-ends.** LLM-written mermaid is often subtly
// invalid, and a lesson must still be readable when the diagram isn't. A parse or
// render failure falls back to the source rendered as a plain code block, so the
// learner sees the diagram's text instead of an error box — and
// `suppressErrorRendering` stops mermaid from injecting its own error graphic
// into the document when that happens.

import { useEffect, useRef, useState } from "react";

// Nocturne, expressed in the theme variables mermaid understands. Colours are the
// same tokens tailwind.config.ts defines; mermaid needs literals, not classes.
const THEME_VARIABLES = {
  background: "#161826", // night
  primaryColor: "#232532", // surface — node fill
  primaryTextColor: "#e9e9ed", // porcelain
  primaryBorderColor: "#4fb8c4", // teal
  secondaryColor: "#2b2741", // iris-900
  tertiaryColor: "#292b31", // elevated
  lineColor: "#9397ab", // mist — edges
  textColor: "#e9e9ed",
  mainBkg: "#232532",
  nodeBorder: "#4fb8c4",
  clusterBkg: "#161826",
  clusterBorder: "#e9e9ed29", // divider
  titleColor: "#e9e9ed",
  edgeLabelBackground: "#161826",
  fontSize: "14px",
} as const;

// One load + one initialize per page, shared by every diagram on it. Kept as a
// promise (not a resolved module) so concurrent mounts await the same import.
let MERMAID: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid(): Promise<typeof import("mermaid").default> {
  if (MERMAID === null) {
    MERMAID = import("mermaid").then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "dark",
        themeVariables: THEME_VARIABLES,
        fontFamily: "Inter, system-ui, -apple-system, sans-serif",
        // We render the fallback ourselves; mermaid must not inject its own
        // error graphic into the page when a chart doesn't parse.
        suppressErrorRendering: true,
      });
      return mermaid;
    });
  }
  return MERMAID;
}

// `mermaid.render` puts its id into CSS selectors, so it must be a valid, unique
// identifier. React's `useId` is neither (it contains `:`), hence a counter.
let diagramSeq = 0;

type Status = "pending" | "rendered" | "failed";

/**
 * Render one mermaid chart, falling back to its source on any failure.
 *
 * `source` is the fenced block's contents, verbatim. Until the lazy import
 * resolves — and forever, if the chart is invalid or the environment can't draw
 * it — the source renders as a code block, so the block always says something
 * true about the lesson.
 */
export function Mermaid({ source }: { source: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("pending");
  // Guards a setState after unmount when a learner navigates mid-render.
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    diagramSeq += 1;
    const id = `mermaid-diagram-${diagramSeq}`;

    loadMermaid()
      // `parse` is the cheap, side-effect-free validity check; `render` is what
      // costs. Both are inside the same chain so either failing lands in `catch`.
      .then(async (mermaid) => {
        await mermaid.parse(source);
        return mermaid.render(id, source);
      })
      .then(({ svg: rendered }) => {
        if (!mounted.current) return;
        setSvg(rendered);
        setStatus("rendered");
      })
      .catch(() => {
        if (!mounted.current) return;
        setStatus("failed");
      });

    return () => {
      mounted.current = false;
    };
  }, [source]);

  if (status === "rendered" && svg !== null) {
    return (
      <figure
        data-testid="mermaid-diagram"
        data-mermaid-status="rendered"
        // A diagram has a natural width the 480px lesson column can't reflow, so
        // like code blocks and tables it scrolls on its own axis. `[&_svg]` caps
        // the height on a phone without distorting the aspect ratio.
        className="overflow-x-auto rounded-md border border-divider bg-night p-3 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-none"
        // Mermaid's own DOMPurify-sanitised output (securityLevel: "strict") —
        // never the raw chart source. See the module header.
        // biome-ignore lint/security/noDangerouslySetInnerHtml: mermaid returns a sanitised SVG string, not nodes
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }

  // `pending` and `failed` render identically: the diagram's source, legible as
  // text. A learner waiting on the lazy chunk sees content rather than a gap, and
  // an unparseable chart degrades to something readable instead of an error.
  return (
    <pre
      data-testid="mermaid-diagram"
      data-mermaid-status={status}
      className="overflow-x-auto rounded-md border border-divider bg-elevated p-4 font-mono text-[13px] leading-6 text-porcelain"
    >
      <code>{source}</code>
    </pre>
  );
}
