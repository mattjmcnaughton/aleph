// The end of a path (mock: `docs/mocks/aleph-path-complete.html`, direction
// "B into D"): the seal draws, then the receipt it drew on.
//
// Why this exists at all: `CompletedState` is one component for every lesson in
// the product, so finishing lesson 24 of 24 used to render the same card — "Head
// back to your path to keep going" included — as finishing lesson 3, at the one
// moment there is nothing left to keep going to. The path-level acknowledgement
// (`CompleteBanner`) does exist, but on the path view, a screen away from the
// tap that earned it.
//
// Three rules the design is built on:
//
// * **In place, never over.** No takeover, no scrim. Phase 3's drafts block
//   renders directly under the completion state and is the highest-value thing
//   on the screen at that moment; a celebration that covers it trades something
//   for nothing.
// * **Earned, not congratulated.** The ceremony is a ring drawing itself around
//   the product's own glyph, and it resolves into facts. It fires once per path,
//   ever, and nothing loops afterwards.
// * **Motion is never the message.** Every element's resting state *is* its
//   final state; the animations are `motion-safe:` additions on top. Under
//   `prefers-reduced-motion` the whole card simply exists, fully legible, with
//   no confetti spawned.

import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import { useFeatureFlag } from "../lib/feature-flags";
import { PRIMARY_CTA_BASE } from "./state-card";

/** Circumference of the seal's r=48 circle — matches `seal-draw`'s dash length. */
const SEAL_DASH = 302;

/** How many pieces the burst throws. Sparse on purpose (mock direction C). */
const CONFETTI_COUNT = 22;

const CONFETTI_COLORS = ["#4fb8c4", "#6fced9", "#9184d9", "#b5abfc", "#e9e9ed"];

// The one place the celebration reads the preference in JS. Everything else is
// a `motion-safe:` class, which CSS honours without asking — this exists only
// because confetti has to be *not created*, not merely not animated: 22 nodes
// parked at their start position would sit on top of the card forever.
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Whole **Days**, in the learner's local timezone, spanned by a path's work —
 * inclusive, so finishing a path the day you started it reads "1 day".
 *
 * A Day is a calendar day in the learner's local timezone (CONTEXT.md), which
 * is why the API sends the span's two ends as instants and the arithmetic
 * happens here: the completion route takes no `tz_offset_minutes`, and it does
 * not need to — the browser already knows. Both ends are floored to local
 * midnight before subtracting so a 20:00 → 09:00 finish counts as two days, not
 * one, and `Math.round` absorbs the 23/25-hour days a DST boundary produces.
 */
export function localDaySpan(fromIso: string, toIso: string): number {
  const from = new Date(fromIso);
  const to = new Date(toIso);
  const fromMidnight = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  const toMidnight = new Date(to.getFullYear(), to.getMonth(), to.getDate());
  const days = (toMidnight.getTime() - fromMidnight.getTime()) / 86_400_000;
  return Math.max(1, Math.round(days) + 1);
}

interface ConfettiPiece {
  id: string;
  dx: number;
  dy: number;
  rotation: number;
  duration: number;
  delay: number;
  left: number;
  color: string;
}

/**
 * One burst's worth of pieces, thrown up and out from the middle of the card.
 *
 * Angles sit in a fan around straight up (-115°…-65°) and every piece is given
 * a downward bias on its way out, so the burst reads as thrown-and-falling
 * rather than as an explosion in a vacuum.
 */
function makeConfetti(): ConfettiPiece[] {
  return Array.from({ length: CONFETTI_COUNT }, (_, index) => {
    const angle = ((-115 + Math.random() * 50) * Math.PI) / 180;
    const distance = 120 + Math.random() * 190;
    return {
      id: `conf-${index}`,
      dx: Math.round(Math.cos(angle) * distance),
      dy: Math.round(Math.sin(angle) * distance + 210),
      rotation: Math.round(Math.random() * 720 - 360),
      duration: Math.round(900 + Math.random() * 500),
      delay: Math.round(Math.random() * 160),
      left: Math.round(Math.random() * 60 - 30),
      color: CONFETTI_COLORS[index % CONFETTI_COLORS.length],
    };
  });
}

function Confetti() {
  // Rolled once per mount. The card only ever mounts on the completion that
  // finished the path, so "once per mount" is also once per path.
  const pieces = useMemo(() => (prefersReducedMotion() ? [] : makeConfetti()), []);
  if (pieces.length === 0) return null;

  return (
    <div
      aria-hidden="true"
      data-testid="path-complete-confetti"
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      {pieces.map((piece) => (
        <span
          key={piece.id}
          className="absolute top-1/2 h-[9px] w-[6px] rounded-[1px] animate-confetti"
          style={
            {
              left: `calc(50% + ${piece.left}px)`,
              background: piece.color,
              "--conf-dx": `${piece.dx}px`,
              "--conf-dy": `${piece.dy}px`,
              "--conf-rot": `${piece.rotation}deg`,
              "--conf-dur": `${piece.duration}ms`,
              animationDelay: `${piece.delay}ms`,
            } as React.CSSProperties
          }
        />
      ))}
    </div>
  );
}

/**
 * The seal: a teal ring drawing itself around the aleph glyph, then one pulse.
 *
 * The glyph is the product's own mark (iris, as everywhere else it appears) and
 * the ring is teal, keeping the two accents doing what they do everywhere in
 * Nocturne — teal for the path, iris for Aleph itself.
 */
function Seal() {
  return (
    <span aria-hidden="true" className="relative mx-auto block h-[108px] w-[108px]">
      <svg
        viewBox="0 0 108 108"
        aria-hidden="true"
        className="absolute inset-0 h-full w-full -rotate-90"
      >
        <circle cx="54" cy="54" r="48" fill="none" stroke="#ffffff14" strokeWidth="2" />
        <circle
          cx="54"
          cy="54"
          r="48"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray={SEAL_DASH}
          className="text-teal motion-safe:animate-seal-draw"
        />
      </svg>
      {/* The pulse. Resting state is invisible (the animation's `to`), so
          without motion there is simply no pulse rather than a stuck ring. */}
      <span className="absolute -inset-1.5 rounded-full opacity-0 shadow-glow motion-safe:animate-seal-halo motion-safe:[animation-delay:1s]" />
      <span className="absolute inset-0 grid place-items-center font-serif text-[44px] leading-none text-iris-400 motion-safe:animate-seal-glyph motion-safe:[animation-delay:0.7s]">
        א
      </span>
    </span>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="bg-surface px-3.5 py-3">
      <p className="text-2xl font-semibold leading-tight tracking-tight tabular-nums text-porcelain">
        {value}
      </p>
      <p className="mt-0.5 font-mono text-[11px] uppercase tracking-kicker text-slate">{label}</p>
    </div>
  );
}

const DOOR =
  "flex w-full items-center gap-3 rounded-lg bg-surface px-3.5 py-3 text-left text-[13px] text-porcelain shadow-[inset_0_0_0_1px_#ffffff12] transition-shadow hover:shadow-[inset_0_0_0_1px_rgba(79,184,196,0.4)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal focus-visible:ring-offset-2 focus-visible:ring-offset-night";

/**
 * A door's contents, without the link around it.
 *
 * Split this way because TanStack Router types `to`/`search`/`params` as one
 * unit — a wrapper that forwards them as loose props erases the pairing and the
 * route's own search shape stops being checked. The call sites below each write
 * their own `<Link>`, so a typo in a destination is a type error rather than a
 * dead tap.
 */
function DoorBody({
  icon,
  iconClass,
  title,
  subtitle,
}: {
  icon: string;
  iconClass: string;
  title: string;
  subtitle: string;
}) {
  return (
    <>
      <span
        aria-hidden="true"
        className={`grid h-[26px] w-[26px] shrink-0 place-items-center rounded-lg text-[13px] ${iconClass}`}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        {title}
        <span className="mt-0.5 block text-[11.5px] text-slate">{subtitle}</span>
      </span>
      <span aria-hidden="true" className="shrink-0 text-slate">
        ›
      </span>
    </>
  );
}

/**
 * The path-complete card, rendered in `CompletedState`'s place on the final
 * lesson of a path.
 *
 * `celebrate` separates the tap from the revisit: the motion and the burst
 * belong to the completion that earned them, so re-opening the finished lesson
 * later gets the same card standing still. It is not a "have we already
 * celebrated" flag — nothing is persisted — but simply whether this render came
 * from the mutation's own result.
 *
 * Keeps `lesson-completed` / `lesson-completed-back` as its testids: this *is*
 * the lesson-completed surface, in its path-finishing variant, and the way back
 * to the path is the same affordance under a name that now means something
 * ("See what you finished" rather than "keep going").
 */
export function PathCompleteCard({
  pathId,
  pathTitle,
  topic,
  lessonCount,
  days,
  celebrate,
}: {
  pathId: string;
  /** The learner-editable display label — never the Topic (CONTEXT.md). */
  pathTitle: string;
  /** The frozen generation input, prefilled into the doors below. */
  topic: string;
  lessonCount: number;
  /**
   * Local Days spanned, or `null` when they are not knowable — a learner
   * re-opening the final lesson later has the path detail (so a lesson count)
   * but not the completion response that carried the span, and a fabricated
   * number is worse than one fewer stat.
   */
  days: number | null;
  celebrate: boolean;
}) {
  const analystEnabled = useFeatureFlag("analyst");

  return (
    <section
      data-testid="lesson-completed"
      data-variant="path-complete"
      aria-live="polite"
      className="relative overflow-hidden rounded-lg border border-teal/40 bg-teal/10 p-5 pt-6 shadow-sm"
    >
      {celebrate ? <Confetti /> : null}

      <Seal />

      {/* One `rise-in` on the whole block below the seal, rather than a stagger
          per row: the seal is the moment, and four things arriving one after
          another behind it would extend the ceremony past the point it was
          made. */}
      <div
        className={
          celebrate ? "motion-safe:animate-rise-in motion-safe:[animation-delay:1.15s]" : ""
        }
      >
        <h2 className="mt-4 text-center text-lg font-semibold tracking-tight text-porcelain">
          Path complete.
        </h2>
        <p className="mt-2 text-center text-sm leading-6 text-mist">
          You finished <span className="font-semibold text-porcelain">{pathTitle}</span>.
        </p>

        <div
          data-testid="path-complete-stats"
          className={`mt-4 grid gap-px overflow-hidden rounded-[10px] bg-divider ${
            days === null ? "grid-cols-1" : "grid-cols-2"
          }`}
        >
          <Stat value={lessonCount} label={lessonCount === 1 ? "Lesson" : "Lessons"} />
          {days === null ? null : <Stat value={days} label={days === 1 ? "Day" : "Days"} />}
        </div>

        <div className="mt-3 flex flex-col gap-2">
          <Link to="/new" search={{ topic }} data-testid="path-complete-deeper" className={DOOR}>
            <DoorBody
              icon="↗"
              iconClass="bg-teal/15 text-teal"
              title={`Go deeper on ${topic}`}
              subtitle="Start another path, one level up"
            />
          </Link>
          {/* Gated like every other analyst surface: with the flag off there is
              no Beat to deploy, so the door is absent rather than disabled. */}
          {analystEnabled ? (
            <Link
              to="/beats/new"
              search={{ topic }}
              data-testid="path-complete-beat"
              className={DOOR}
            >
              <DoorBody
                icon="◈"
                iconClass="bg-iris/15 text-iris-400"
                title="Follow it as a Beat"
                subtitle="Weekly Briefs on what changes"
              />
            </Link>
          ) : null}
        </div>

        <Link
          to="/paths/$pathId"
          params={{ pathId }}
          data-testid="lesson-completed-back"
          className={`mt-4 ${PRIMARY_CTA_BASE}`}
        >
          See what you finished
        </Link>
      </div>
    </section>
  );
}
