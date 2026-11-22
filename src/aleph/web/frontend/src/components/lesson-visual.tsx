// An interactive lesson Visual (CONTEXT.md: Visual; docs/tdds/lesson-visuals.md).
//
// The source is agent-written HTML + script: untrusted, and executable. It is
// never inserted into this document. Instead it runs in an iframe that is
//
// * `sandbox="allow-scripts"` and **not** `allow-same-origin`, so the frame has
//   an opaque origin: no cookies, no storage, no `parent.document`, no
//   credentialed calls to our API, no navigation of the page, no popups;
// * `srcdoc`, wrapped in the versioned kit below, whose CSP
//   (`default-src 'none'`) blocks every network load — fetch, images, fonts;
// * `referrerpolicy="no-referrer"`.
//
// The frame talks back only through two message shapes, accepted only when
// `event.source` is this frame's own `contentWindow`. An error, or no `ok` within
// `VISUAL_READY_TIMEOUT_MS`, removes the frame and shows the stored alt text —
// the same posture `mermaid.tsx` takes when a chart will not parse.

import { useEffect, useRef, useState } from "react";
import type { LessonVisual as Visual } from "../lib/api";

export const VISUAL_READY_TIMEOUT_MS = 3000;
const MIN_HEIGHT = 60;
const MAX_HEIGHT = 640;

// Kit v1: the Nocturne tokens (tailwind.config.ts) as CSS variables, system font
// fallbacks (the CSP blocks font loads), a few classes and `window.kit`.
// Height is measured on <body> and observed after DOMContentLoaded — <html> is
// pinned to the frame's height, so observing it never fires.
const KIT_V1 = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--night:#11161b;--surface:#182027;--elevated:#202a32;--porcelain:#ebe6da;--mist:#a5a89f;--slate:#7f847d;--faint:#49535a;--teal:#56b3ab;--iris:#9a92d8;--divider:#ebe6da1f;--danger:#ef8f82;color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0;background:var(--night);color:var(--porcelain);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body{padding:14px 16px}
button{background:var(--teal);color:var(--night);border:0;border-radius:8px;padding:7px 12px;font:600 13px system-ui,-apple-system,sans-serif;cursor:pointer}
button.ghost{background:transparent;color:var(--mist);border:1px solid var(--divider)}
button:disabled{opacity:.4;cursor:default}
input[type=range]{accent-color:var(--teal);width:100%;margin:6px 0}
.row{display:flex;gap:8px;align-items:center}
.muted{color:var(--mist);font-size:12px}
</style>
<script>
(function(){
  function post(m){ parent.postMessage(m, "*"); }
  function height(){ post({type:"height", height: Math.ceil(document.body.getBoundingClientRect().height)}); }
  window.kit = {
    ready: function(){ post({type:"status", status:"ok"}); height(); },
    fail: function(){ post({type:"status", status:"error"}); }
  };
  window.addEventListener("error", function(){ window.kit.fail(); });
  document.addEventListener("DOMContentLoaded", function(){
    new ResizeObserver(height).observe(document.body);
  });
})();
</script>`;

/** The srcdoc for a visual body: the kit's head around the agent's body. */
export function buildKitDocument(body: string): string {
  return `<!doctype html><html><head>${KIT_V1}</head><body>${body}</body></html>`;
}

type Status = "loading" | "ready" | "failed";

function clampHeight(height: number): number {
  return Math.min(Math.max(Math.ceil(height), MIN_HEIGHT), MAX_HEIGHT);
}

/**
 * One Visual. Mount it with `key={visual.id}` so every visual gets a fresh
 * frame: a reused frame that was hidden can stay stuck at its minimum height.
 */
export function LessonVisual({ visual }: { visual: Visual }) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [height, setHeight] = useState<number | null>(null);

  useEffect(() => {
    let settled = false;
    const timer = window.setTimeout(() => {
      if (!settled) setStatus("failed");
    }, VISUAL_READY_TIMEOUT_MS);

    function onMessage(event: MessageEvent) {
      const own = frameRef.current?.contentWindow;
      if (own == null || event.source !== own) return;
      const data: unknown = event.data;
      if (typeof data !== "object" || data === null) return;
      const message = data as { type?: unknown; status?: unknown; height?: unknown };
      if (message.type === "height") {
        // A height of 0 means "not laid out yet", not "empty": Chrome throttles
        // an offscreen cross-origin frame, so `kit.ready()` measures 0 there and
        // the ResizeObserver reports the real height once it scrolls into view.
        // Keep the placeholder until then rather than collapsing to the minimum.
        if (
          typeof message.height === "number" &&
          Number.isFinite(message.height) &&
          message.height > 0
        ) {
          setHeight(clampHeight(message.height));
        }
      } else if (message.type === "status") {
        if (message.status === "ok" && !settled) {
          settled = true;
          window.clearTimeout(timer);
          setStatus("ready");
        } else if (message.status === "error") {
          settled = true;
          window.clearTimeout(timer);
          setStatus("failed");
        }
      }
    }

    window.addEventListener("message", onMessage);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
    };
  }, []);

  return (
    <figure
      data-testid="lesson-visual"
      data-visual-id={visual.id}
      data-visual-status={status}
      className="overflow-hidden rounded-md border border-divider font-sans"
    >
      {status === "failed" ? (
        <p
          data-testid="lesson-visual-fallback"
          className="bg-surface px-4 py-3.5 text-[15px] leading-6 text-mist"
        >
          {visual.alt}
        </p>
      ) : (
        <iframe
          ref={frameRef}
          title={visual.title}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          srcDoc={buildKitDocument(visual.source)}
          className="block h-[120px] w-full border-0 bg-night"
          style={height === null ? undefined : { height: `${height}px` }}
        />
      )}
      <figcaption className="border-t border-divider px-4 py-2 text-[13px] leading-5 text-slate">
        {visual.title}
      </figcaption>
    </figure>
  );
}
