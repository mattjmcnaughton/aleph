// Shared Nocturne primitives for the trigger+poll surfaces' non-ready states —
// onboarding (/new), the path view, and the lesson view all render the same
// bordered status card, spinner, teal CTA, retry notices, and inline icons.
// These are behaviour-preserving building blocks: every caller keeps its own
// testids, headings, and copy; only the repeated markup + styling live here.

import type { ReactNode } from "react";

/** Teal primary CTA, sans disabled styling — for links (which never disable). */
export const PRIMARY_CTA_BASE =
  "inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright";

/** Teal primary CTA for buttons (adds the disabled treatment). */
export const PRIMARY_CTA = `${PRIMARY_CTA_BASE} disabled:cursor-not-allowed disabled:opacity-50`;

/** Outline/secondary CTA (divider border) — the "back to your path" links. */
export const SECONDARY_CTA =
  "inline-flex w-full items-center justify-center rounded-md border border-divider px-4 py-3 text-sm font-semibold text-porcelain transition-colors hover:border-teal/40";

const CARD_VARIANT = {
  /** Ordinary status (generating / locked / unavailable / stalled). */
  neutral: "border-divider bg-surface",
  /** Generation failure (retryable). */
  error: "border-danger-border/60 bg-danger-bg",
  /** Safety refusal (terminal, graceful). */
  refusal: "border-iris-700 bg-iris-900",
} as const;

/**
 * The bordered, centered status card shell. `variant` picks the border/bg
 * treatment; `spacing` is the top margin (onboarding sits lower on the page, so
 * it passes `mt-8`). The heading + body copy are the caller's children.
 */
export function StateCard({
  variant = "neutral",
  testid,
  ariaLive,
  dataVariant,
  spacing = "mt-4",
  children,
}: {
  variant?: keyof typeof CARD_VARIANT;
  testid: string;
  ariaLive?: "polite" | "assertive";
  dataVariant?: string;
  spacing?: string;
  children: ReactNode;
}) {
  return (
    <section
      data-testid={testid}
      data-variant={dataVariant}
      aria-live={ariaLive}
      className={`${spacing} rounded-lg border ${CARD_VARIANT[variant]} p-6 text-center shadow-sm`}
    >
      {children}
    </section>
  );
}

/** The generation spinner shown while content is still being produced. */
export function Spinner() {
  return (
    <div
      className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-divider border-t-teal"
      aria-hidden="true"
    />
  );
}

/**
 * The rate-limited + generic retry-failure notices shared by all three failed
 * surfaces (§5.6/F1). `testidPrefix` keeps each surface's testids (e.g.
 * `lesson-retry-ratelimit`); the generic-error copy is identical everywhere, so
 * only the daily-cap message varies per surface.
 */
export function RetryNotices({
  testidPrefix,
  rateLimited,
  errored,
  rateLimitMessage,
}: {
  testidPrefix: string;
  rateLimited: boolean;
  errored: boolean;
  rateLimitMessage: string;
}) {
  return (
    <>
      {rateLimited ? (
        <p
          data-testid={`${testidPrefix}-retry-ratelimit`}
          className="mx-auto mt-4 max-w-[24rem] text-sm leading-6 text-danger"
        >
          {rateLimitMessage}
        </p>
      ) : null}
      {errored ? (
        <p
          data-testid={`${testidPrefix}-retry-error`}
          className="mx-auto mt-4 max-w-[24rem] text-sm leading-6 text-danger"
        >
          That retry didn't go through. Check your connection and try again.
        </p>
      ) : null}
    </>
  );
}

export function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
      <path
        d="M3.5 8.5l3 3 6-6.5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function LockIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
      <rect x="3.5" y="7" width="9" height="6" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5.5 7V5.5a2.5 2.5 0 0 1 5 0V7" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

export function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3 w-3" fill="currentColor" aria-hidden="true">
      <path d="M5 3.5v9l7-4.5-7-4.5z" />
    </svg>
  );
}
