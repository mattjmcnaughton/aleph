// Motion helpers shared by the two celebrations (`components/path-complete.tsx`
// and the flow receipt, `routes/flow.done.tsx`).
//
// The house rule for motion (path-complete.tsx): every element's resting state
// *is* its final state, and animation is a `motion-safe:` addition on top. CSS
// honours `prefers-reduced-motion` without asking; the helpers here exist for
// the two things CSS cannot do — decide not to *create* nodes (confetti), and
// decide not to count a number up (a count-up is JS by nature).

import { useEffect, useState } from "react";

/** The one place the preference is read in JS. */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** How long one count-up runs, start to settle. */
export const COUNT_UP_MS = 900;

/**
 * A number that counts from zero to `target` — or simply *is* `target`.
 *
 * `animate` false (a revisit, an ended-early flow, reduced motion) returns the
 * target on the first render, so the DOM never shows a zero it did not mean.
 * `animate` true holds zero for `delayMs`, then eases out over `COUNT_UP_MS`
 * on animation frames. The stagger is the caller's: each tile passes its own
 * delay. A change of `target` mid-count restarts the count — it cannot happen
 * on the receipt (the record is frozen by then) but the hook is honest about it.
 */
export function useCountUp(target: number, animate: boolean, delayMs = 0): number {
  const [value, setValue] = useState(animate ? 0 : target);

  useEffect(() => {
    if (!animate) {
      setValue(target);
      return;
    }
    let frame = 0;
    let startedAt: number | null = null;
    const timer = setTimeout(() => {
      const tick = (now: number) => {
        if (startedAt === null) startedAt = now;
        const progress = Math.min(1, Math.max(0, (now - startedAt) / COUNT_UP_MS));
        const eased = 1 - (1 - progress) ** 3;
        setValue(Math.round(target * eased));
        if (progress < 1) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    }, delayMs);
    return () => {
      clearTimeout(timer);
      cancelAnimationFrame(frame);
    };
  }, [target, animate, delayMs]);

  return value;
}
