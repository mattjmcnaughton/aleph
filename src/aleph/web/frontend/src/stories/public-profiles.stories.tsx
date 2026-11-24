import type { Meta, StoryObj } from "@storybook/react-vite";
import type { ReactNode } from "react";
import { ActivityStrip } from "../components/activity-strip";
import { AlephGlyph, AlephLogo } from "../components/aleph-logo";
import { LessonMarker } from "../components/lesson-marker";
import { ListRow, RowChevron, RowTitle } from "../components/list-row";
import { SectionHeader } from "../components/section-header";
import { PRIMARY_CTA, SECONDARY_CTA, Spinner, StateCard } from "../components/state-card";
import { StreakChip } from "../components/streak-chip";
import { StreakLine } from "../components/streak-line";
import { AppAt } from "./app-at";
import { worldProgress } from "./world";

// Concept: public profiles. Not built. A learner opts in to a public page at a
// Handle (aleph.app/@matt) that anyone can open without signing in. It shows the
// paths and Beats they chose to share, and a visitor can copy one into their own
// account. These are static specimens on the world's learner, drawn with the
// shipped tokens and row components, to decide the design before an API exists.
//
// Product decisions these frames assume (each one is open for review):
// - Off by default, and nothing is public until the learner picks it, one path or
//   Beat at a time. Unpicked paths do not exist to a visitor.
// - A visitor sees a shared path's creation inputs (Topic, Level, Guidance,
//   Teaching style), its outline (unit and lesson titles) and its progress.
//   Never a Read passage, a Quick check, a tutor Conversation or About you.
// - Copying a path copies its creation inputs, not its outline or lessons. It
//   opens the real New path form prefilled with them, exactly as a Learning
//   summary Suggestion does; the visitor can edit anything, and nothing is
//   created until they submit. Aleph then drafts a new outline for them, so the
//   copy can differ from the original.
// - Copying a Beat opens New beat prefilled with the same Topic, Level and
//   Guidance; the visitor picks their own Anchor day. Briefs start from #1.
// - A private profile, an unknown Handle and an unshared path all render the same
//   "not here" page, so the URL never confirms that something exists.

const HANDLE = "@matt";
const OWNER = "Matt McNaughton";

// --- Mock profile data (the world's learner, as a visitor would see him) --------

type LessonState = "complete" | "available" | "locked";

const SHARED_PATHS = [
  {
    id: "rust",
    title: "Rust ownership & lifetimes",
    level: "Some experience",
    done: 4,
    total: 11,
    streak: 12,
    copies: 14,
  },
  {
    id: "bayes",
    title: "Bayesian statistics",
    level: "Some experience",
    done: 7,
    total: 9,
    streak: 0,
    copies: 3,
  },
  {
    id: "printing",
    title: "The history of the printing press",
    level: "New to it",
    done: 8,
    total: 8,
    streak: 0,
    copies: 0,
  },
];

const SHARED_BEATS = [
  {
    id: "ai",
    topic: "AI infrastructure",
    cadence: "Weekly",
    latest: "Inference is getting cheaper faster than training",
    briefs: 3,
  },
  {
    id: "climate",
    topic: "Climate tech policy",
    cadence: "Weekly",
    latest: "What the new grid interconnection rules change",
    briefs: 1,
  },
];

const RUST_OUTLINE: { title: string; lessons: { title: string; state: LessonState }[] }[] = [
  {
    title: "Ownership from first principles",
    lessons: [
      { title: "Moves, copies and the stack", state: "complete" },
      { title: "The borrow checker's three rules", state: "complete" },
      { title: "Shared and mutable borrows", state: "complete" },
      { title: "Slices and borrowed views", state: "complete" },
    ],
  },
  {
    title: "Lifetimes in practice",
    lessons: [
      { title: "Reading lifetime annotations", state: "available" },
      { title: "Elision and when it fails", state: "locked" },
      { title: "Lifetimes in structs", state: "locked" },
      { title: "'static, honestly", state: "locked" },
    ],
  },
  {
    title: "Smart pointers",
    lessons: [
      { title: "Box and the heap", state: "locked" },
      { title: "Rc, Arc and shared ownership", state: "locked" },
      { title: "RefCell and interior mutability", state: "locked" },
    ],
  },
];

/** The world's own inputs for the Rust path (`world.ts`, `seedPaths`). */
const RUST_INPUTS = [
  { label: "Topic", value: "Rust ownership and lifetimes" },
  { label: "Level", value: "Some experience" },
  {
    label: "Guidance",
    value: "I write Go at work and keep fighting the borrow checker on side projects.",
  },
  {
    label: "Teaching style",
    value: "Short passages, lots of small code examples, compare to Go where it helps.",
  },
];

/** The prefilled New path form, as `?from=matt/<pathId>` would leave it. */
const COPY_URL = `/new?${new URLSearchParams({
  topic: RUST_INPUTS[0].value,
  level: "some_experience",
  guidance: RUST_INPUTS[2].value,
  teaching_style: RUST_INPUTS[3].value,
})}`;

const AI_BRIEFS = [
  { n: 3, date: "Nov 17", title: "Inference is getting cheaper faster than training", sources: 6 },
  { n: 2, date: "Nov 10", title: "Batching schedulers, explained", sources: 4 },
  { n: 1, date: "Nov 3", title: "Where the GPUs actually are", sources: 7 },
];

// --- Chrome ----------------------------------------------------------------------

/**
 * The header a visitor sees. Signed out it carries the one way in ("Sign in");
 * signed in it is the app's own header (brand + account initial), because a
 * learner browsing someone else's profile is still in their own Aleph.
 */
function PublicHeader({ signedIn }: { signedIn: boolean }) {
  return (
    <header className="sticky top-0 z-10 box-border h-[var(--app-header-h)] border-b border-divider bg-night/90 backdrop-blur">
      <div className="mx-auto flex h-full w-full max-w-[560px] items-center justify-between gap-3 px-5 lg:max-w-none lg:px-6">
        <AlephLogo />
        {signedIn ? (
          <span
            aria-hidden="true"
            className="grid h-8 w-8 place-items-center rounded-full bg-elevated text-sm font-semibold text-porcelain ring-1 ring-divider"
          >
            J
          </span>
        ) : (
          <button
            type="button"
            className="inline-flex min-h-[44px] items-center rounded-md px-2 text-sm font-semibold text-porcelain transition-colors hover:text-teal-bright"
          >
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}

function Screen({ signedIn = false, children }: { signedIn?: boolean; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-night text-porcelain">
      <PublicHeader signedIn={signedIn} />
      <main className="page">{children}</main>
    </div>
  );
}

/** Ancestors only, as the app's breadcrumb is. */
function Crumb({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm text-mist">
      <span className="text-porcelain">{children}</span>
    </p>
  );
}

function OwnerAvatar({ size = "md" }: { size?: "sm" | "md" }) {
  const box = size === "md" ? "h-14 w-14 text-xl" : "h-6 w-6 text-[11px]";
  return (
    <span
      aria-hidden="true"
      className={`grid ${box} shrink-0 place-items-center rounded-full bg-elevated font-serif font-medium text-porcelain ring-1 ring-divider`}
    >
      M
    </span>
  );
}

/** A visitor's one line of "whose is this", above a shared path or Beat. */
function SharedBy() {
  return (
    <p className="flex items-center gap-2 text-sm text-mist">
      <OwnerAvatar size="sm" />
      <span>
        Shared by <span className="font-semibold text-porcelain">{OWNER}</span> · {HANDLE}
      </span>
    </p>
  );
}

// --- Frame 1–2: Settings -------------------------------------------------------

function Toggle({ on, label }: { on: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors ${
        on ? "bg-teal" : "bg-elevated ring-1 ring-faint"
      }`}
    >
      <span
        className={`absolute h-5 w-5 rounded-full transition-transform ${
          on ? "translate-x-6 bg-night" : "translate-x-1 bg-mist"
        }`}
      />
    </button>
  );
}

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`grid h-5 w-5 shrink-0 place-items-center rounded-[5px] ${
        checked ? "bg-teal text-night" : "ring-1 ring-faint"
      }`}
    >
      {checked ? (
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
          <path
            d="M3.5 8.5l3 3 6-6.5"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : null}
    </span>
  );
}

function PickRow({ title, detail, checked }: { title: string; detail: string; checked: boolean }) {
  return (
    <li className="flex min-h-[56px] items-center gap-3 border-b border-divider py-3">
      <Checkbox checked={checked} />
      <div className="min-w-0">
        <p className="line-clamp-2 text-base font-semibold leading-snug">{title}</p>
        <p className="text-sm text-mist">{detail}</p>
      </div>
    </li>
  );
}

function SettingsPublicProfile({ on }: { on: boolean }) {
  return (
    <Screen signedIn>
      <Crumb>Settings</Crumb>
      <h1 className="page-title mt-2">Public profile</h1>
      <p className="mt-3 text-base leading-6 text-mist">
        A page anyone can open, without signing in, showing the paths and Beats you pick. Visitors
        can start their own copy of anything on it.
      </p>

      <div className="mt-7 flex items-center justify-between gap-4 rounded-lg border border-divider bg-surface px-4 py-4">
        <div>
          <p className="font-semibold">Show my public profile</p>
          <p className="text-sm text-mist">{on ? "On. Anyone with the link can see it." : "Off"}</p>
        </div>
        <Toggle on={on} label="Show my public profile" />
      </div>

      {on ? (
        <>
          <div className="mt-7">
            <p className="kicker">Your link</p>
            <span className="mt-2 flex min-h-12 items-center rounded-md border border-faint bg-surface px-3">
              <span className="text-mist">aleph.app/@</span>
              <span className="font-semibold">matt</span>
            </span>
          </div>
          <div className="mt-3 flex gap-2">
            <button type="button" className={`${SECONDARY_CTA} !min-h-11 !py-2 text-sm`}>
              Copy link
            </button>
            <button type="button" className={`${SECONDARY_CTA} !min-h-11 !py-2 text-sm`}>
              View as a visitor
            </button>
          </div>

          <SectionHeader kicker="Paths on your profile" spacing="mt-9" summary="3 of 5 shown" />
          <ul>
            <PickRow
              title="Rust ownership & lifetimes"
              detail="Some experience · 4 of 11"
              checked
            />
            <PickRow title="Bayesian statistics" detail="Some experience · 7 of 9" checked />
            <PickRow
              title="The history of the printing press"
              detail="New to it · Complete"
              checked
            />
            <PickRow
              title="Kubernetes for application developers"
              detail="New to it · Not started"
              checked={false}
            />
            <PickRow
              title="Category theory for programmers"
              detail="I work in it · Not started"
              checked={false}
            />
          </ul>

          <SectionHeader kicker="Beats on your profile" spacing="mt-9" summary="2 of 2 shown" />
          <ul>
            <PickRow title="AI infrastructure" detail="Weekly · 3 Briefs" checked />
            <PickRow title="Climate tech policy" detail="Weekly · 1 Brief" checked />
          </ul>

          <SectionHeader kicker="Also shown" spacing="mt-9" />
          <ul>
            <PickRow
              title="Daily streak and activity"
              detail="The last 7 weeks, across all your paths"
              checked
            />
          </ul>

          <p className="mt-6 text-sm leading-6 text-mist">
            On a shared path, visitors see how you started it (topic, level, guidance and teaching
            style), its outline and your progress, and can start their own from the same inputs.
            Your lessons, Quick check answers, tutor conversations and About you stay private.
            Briefs on a shared Beat are readable in full.
          </p>
        </>
      ) : (
        <p className="mt-6 text-sm leading-6 text-mist">
          Nothing is public until you turn this on and pick what to show.
        </p>
      )}
    </Screen>
  );
}

// --- Frame 3: The public profile ------------------------------------------------

function CopyCount({ copies }: { copies: number }) {
  if (copies === 0) return null;
  return (
    <span className="text-xs text-mist">
      {copies} {copies === 1 ? "copy" : "copies"}
    </span>
  );
}

function PublicProfile({ signedIn }: { signedIn: boolean }) {
  const progress = worldProgress();
  return (
    <Screen signedIn={signedIn}>
      <div className="flex items-center gap-4">
        <OwnerAvatar />
        <div className="min-w-0">
          <h1 className="page-title max-lg:text-2xl">{OWNER}</h1>
          <p className="text-sm text-mist">{HANDLE} · learning on Aleph since August</p>
        </div>
      </div>

      <div className="mt-7 flex items-center justify-between gap-6">
        <StreakLine summary={progress} />
        <ActivityStrip activity={progress.activity} />
      </div>

      <SectionHeader kicker="Learning" spacing="mt-8" summary="2 paths" />
      <ul>
        {SHARED_PATHS.slice(0, 2).map((path) => (
          <li key={path.id}>
            <PublicPathRow path={path} />
          </li>
        ))}
      </ul>

      <SectionHeader kicker="Finished" spacing="mt-8" />
      <ul className="opacity-70">
        <li>
          <PublicPathRow path={SHARED_PATHS[2]} />
        </li>
      </ul>

      <SectionHeader kicker="Beats" spacing="mt-8" summary="Topics Matt keeps watch on" />
      <ul>
        {SHARED_BEATS.map((beat) => (
          <li key={beat.id}>
            <ListRow
              testid="public-beat-row"
              main={
                <RowTitle title={beat.topic} titleTestid="public-beat-topic">
                  <p className="line-clamp-1 text-sm text-mist">Latest: {beat.latest}</p>
                </RowTitle>
              }
              meta={<p className="mt-1 text-sm text-mist lg:mt-0">{beat.cadence}</p>}
              status={
                <span className="hidden lg:inline">
                  {beat.briefs} {beat.briefs === 1 ? "Brief" : "Briefs"}
                </span>
              }
              actions={
                <span aria-hidden="true" className="shrink-0 pt-2 text-slate lg:pt-0">
                  <RowChevron />
                </span>
              }
            />
          </li>
        ))}
      </ul>

      {signedIn ? null : <JoinFooter />}
    </Screen>
  );
}

function PublicPathRow({ path }: { path: (typeof SHARED_PATHS)[number] }) {
  const percent = Math.round((path.done / path.total) * 100);
  const finished = path.done === path.total;
  return (
    <ListRow
      testid="public-path-row"
      main={
        <RowTitle title={path.title} titleTestid="public-path-title">
          <p className="text-sm text-mist">{path.level}</p>
          <StreakChip days={path.streak} />
          <CopyCount copies={path.copies} />
        </RowTitle>
      }
      meta={
        <div className="mt-3 flex items-center gap-3 lg:mt-0 lg:block">
          <div
            aria-hidden="true"
            className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-porcelain/10"
          >
            <span className="block h-full bg-porcelain/45" style={{ width: `${percent}%` }} />
          </div>
          <p className="shrink-0 text-sm text-mist lg:mt-2">
            {path.done} of {path.total} lessons
          </p>
        </div>
      }
      status={<span className="hidden lg:inline">{finished ? "Complete" : "In progress"}</span>}
      actions={
        <span aria-hidden="true" className="shrink-0 pt-2 text-slate lg:pt-0">
          <RowChevron />
        </span>
      }
    />
  );
}

/** The quiet invitation at the foot of a page a signed-out visitor is reading. */
function JoinFooter() {
  return (
    <div className="mt-12 border-t border-divider pt-8 text-center">
      <div className="flex justify-center">
        <AlephGlyph size="sm" />
      </div>
      <p className="mt-3 font-serif text-lg font-medium">Learn anything, lesson by lesson.</p>
      <p className="mx-auto mt-1 max-w-[22rem] text-sm leading-6 text-mist">
        Aleph writes a learning path for any topic you name. Start one of Matt's, or your own.
      </p>
    </div>
  );
}

// --- Frame 4: A shared path ---------------------------------------------------

function PublicPath({ signedIn }: { signedIn: boolean }) {
  return (
    <Screen signedIn={signedIn}>
      <Crumb>{OWNER}</Crumb>
      <h1 className="page-title mt-2">Rust ownership &amp; lifetimes</h1>
      <div className="mt-3">
        <SharedBy />
      </div>
      <p className="mt-2 text-sm text-mist">Some experience · 11 lessons in 3 units · 4 complete</p>

      {/* The one card on the page: the thing a visitor acts on (design.md rule 2). */}
      <section className="mt-6 rounded-lg border border-divider bg-surface p-5">
        <p className="font-semibold">Learn this yourself</p>
        <p className="mt-1 text-sm leading-6 text-mist">
          Start your own path from what Matt asked for. You can change any of it first, and Aleph
          drafts a new outline for you.
        </p>
        <button type="button" className={`mt-4 ${PRIMARY_CTA}`}>
          {signedIn ? "Start this path" : "Sign in to start this path"}
        </button>
        <p className="mt-3 text-center text-xs text-mist">14 people have started a copy</p>
      </section>

      {/* The creation inputs are what a copy carries, so they come first. */}
      <SectionHeader kicker="How Matt started it" spacing="mt-9" />
      <dl className="mt-1">
        {RUST_INPUTS.map((input) => (
          <div key={input.label} className="border-b border-divider py-3">
            <dt className="kicker">{input.label}</dt>
            <dd className="mt-0.5 text-[15px] leading-6">{input.value}</dd>
          </div>
        ))}
      </dl>

      <SectionHeader kicker="Matt's outline" spacing="mt-9" />
      {RUST_OUTLINE.map((unit, u) => (
        <section key={unit.title} className="mt-5">
          <p className="kicker">Unit {u + 1}</p>
          <p className="font-serif text-lg font-medium">{unit.title}</p>
          <ul className="mt-2">
            {unit.lessons.map((lesson) => (
              <li
                key={lesson.title}
                className="flex min-h-[44px] items-center gap-3 border-b border-divider py-2"
              >
                {/* Matt's position is not the visitor's, so it is not teal:
                    a check for done, a hollow ring for the rest. */}
                {lesson.state === "complete" ? (
                  <LessonMarker state="complete" size="sm" />
                ) : (
                  <span aria-hidden="true" className="grid h-5 w-5 shrink-0 place-items-center">
                    <span className="h-2 w-2 rounded-full ring-1 ring-faint" />
                  </span>
                )}
                <span className={lesson.state === "complete" ? "text-mist" : "text-porcelain"}>
                  {lesson.title}
                </span>
                {lesson.state === "available" ? (
                  <span className="ml-auto shrink-0 text-xs text-mist">Matt is here</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="mt-5 text-sm leading-6 text-mist">
        Visitors see the outline, not the lessons. Your copy gets its own outline, drafted from the
        inputs above, so it may not match this one.
      </p>
    </Screen>
  );
}

// --- Frame 5: A shared Beat ------------------------------------------------------

function PublicBeat({ signedIn }: { signedIn: boolean }) {
  return (
    <Screen signedIn={signedIn}>
      <Crumb>{OWNER}</Crumb>
      <h1 className="page-title mt-2">AI infrastructure</h1>
      <div className="mt-3">
        <SharedBy />
      </div>
      <p className="mt-2 text-sm text-mist">I work in it · Weekly · 3 Briefs since November 3</p>

      <section className="mt-6 rounded-lg border border-divider bg-surface p-5">
        <p className="font-semibold">Keep watch on this too</p>
        <p className="mt-1 text-sm leading-6 text-mist">
          Deploy your own analyst with the same topic, level and guidance. It reports on the day you
          pick, starting from Brief #1.
        </p>
        <button type="button" className={`mt-4 ${PRIMARY_CTA}`}>
          {signedIn ? "Deploy this Beat" : "Sign in to deploy this Beat"}
        </button>
      </section>

      <SectionHeader kicker="Briefs" spacing="mt-9" />
      <ul>
        {AI_BRIEFS.map((brief) => (
          <li key={brief.n}>
            <ListRow
              testid="public-brief-row"
              main={
                <RowTitle title={brief.title} titleTestid="public-brief-title">
                  <p className="text-sm text-mist">
                    Brief #{brief.n} · {brief.date} · {brief.sources} sources
                  </p>
                </RowTitle>
              }
              actions={
                <span aria-hidden="true" className="shrink-0 pt-2 text-slate lg:pt-0">
                  <RowChevron />
                </span>
              }
            />
          </li>
        ))}
      </ul>
    </Screen>
  );
}

// --- Frame 7: The copy in the learner's own account -----------------------------

function CopiedPath() {
  return (
    <Screen signedIn>
      <Crumb>Your paths</Crumb>
      <h1 className="page-title mt-2">Rust ownership and lifetimes</h1>
      <p className="mt-2 text-sm text-mist">Some experience</p>
      <p className="mt-1 flex items-center gap-2 text-sm text-mist">
        <OwnerAvatar size="sm" />
        Started from {OWNER}'s path
      </p>
      <StateCard testid="copied-drafting" spacing="mt-6">
        <Spinner />
        <h2 className="mt-4 font-serif text-xl font-medium">Drafting your path…</h2>
        <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
          A new outline from the same topic and guidance, written for you. It usually takes under a
          minute.
        </p>
      </StateCard>
    </Screen>
  );
}

// --- Frame 8: Nothing here -------------------------------------------------------

function NotHere() {
  return (
    <Screen>
      <StateCard testid="public-not-here" spacing="mt-10">
        <div className="flex justify-center">
          <AlephGlyph size="md" />
        </div>
        <h1 className="mt-4 font-serif text-xl font-medium">Nothing to see here</h1>
        <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
          This link doesn't go to a public profile or path. It may have been made private.
        </p>
        <button type="button" className={`mt-5 ${PRIMARY_CTA}`}>
          Start your own path
        </button>
      </StateCard>
    </Screen>
  );
}

// --- The storyboard: every frame in order ---------------------------------------

const FRAMES: { step: string; caption: string; story: string }[] = [
  {
    step: "1. Settings, off",
    caption: "Off by default. Nothing is public until Matt turns it on.",
    story: "settings-off",
  },
  {
    step: "2. Settings, on",
    caption: "He picks a link and which paths and Beats to show, one at a time.",
    story: "settings-on",
  },
  {
    step: "3. Profile, visitor",
    caption: "Anyone can open aleph.app/@matt without an account.",
    story: "profile-visitor",
  },
  {
    step: "4. Shared path, visitor",
    caption: "How Matt started it, his outline and progress. Lessons stay private.",
    story: "shared-path-visitor",
  },
  {
    step: "5. Shared Beat, visitor",
    caption: "The Beat and its Briefs. A visitor can deploy their own.",
    story: "shared-beat-visitor",
  },
  {
    step: "6. New path, prefilled",
    caption: "The real New path form, prefilled with Matt's inputs. Edit, then submit.",
    story: "copy-path",
  },
  {
    step: "7. The copy",
    caption: "A new path drafting its own outline, credited to Matt.",
    story: "copied",
  },
  {
    step: "8. Not here",
    caption: "Private, unknown and unshared all look the same.",
    story: "not-found",
  },
];

function Storyboard() {
  return (
    <div className="min-h-screen bg-[#0b0f13] px-8 py-10 text-porcelain">
      <h1 className="page-title">Public profiles: storyboard</h1>
      <p className="mt-2 max-w-[60rem] text-sm leading-6 text-mist">
        A concept, not built. Matt shares what he is learning; a visitor reads it without signing in
        and copies a path into their own account. Each frame is also its own story in this folder,
        at the iPhone viewport.
      </p>
      <ol className="mt-8 flex gap-8 overflow-x-auto pb-6">
        {FRAMES.map((frame) => (
          <li key={frame.step} className="w-[393px] shrink-0">
            <p className="text-sm font-semibold">{frame.step}</p>
            <p className="mt-1 h-10 text-sm leading-5 text-mist">{frame.caption}</p>
            {/* Each frame is its own story in an iPhone-sized iframe: the
                app's \`lg:\` breakpoints read the window, so a frame drawn
                inline on this wide page would get the desktop layout. */}
            <iframe
              title={frame.step}
              src={`iframe.html?id=concepts-public-profiles--${frame.story}&viewMode=story`}
              className="mt-3 h-[852px] w-[393px] rounded-[28px] border border-faint"
            />
          </li>
        ))}
      </ol>
    </div>
  );
}

// --- Stories -------------------------------------------------------------------

const meta = {
  title: "Concepts/Public profiles",
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const StoryboardOverview: Story = {
  name: "Storyboard",
  render: () => <Storyboard />,
  globals: { viewport: { value: "desktop", isRotated: false } },
};

export const SettingsOff: Story = {
  name: "1. Settings — profile off",
  render: () => <SettingsPublicProfile on={false} />,
};

export const SettingsOn: Story = {
  name: "2. Settings — choosing what to show",
  render: () => <SettingsPublicProfile on />,
};

export const ProfileVisitor: Story = {
  name: "3. Public profile — signed-out visitor",
  render: () => <PublicProfile signedIn={false} />,
};

export const ProfileSignedIn: Story = {
  name: "3b. Public profile — signed-in visitor",
  render: () => <PublicProfile signedIn />,
};

export const SharedPathVisitor: Story = {
  name: "4. Shared path — signed-out visitor",
  render: () => <PublicPath signedIn={false} />,
};

export const SharedBeatVisitor: Story = {
  name: "5. Shared Beat — signed-out visitor",
  render: () => <PublicBeat signedIn={false} />,
};

export const CopyPath: Story = {
  name: "6. Copy a path — New path, prefilled",
  // The real route, showing the form as it ends up. Built, the link would be
  // `/new?from=matt/<pathId>` and the form would fetch the inputs from the
  // public endpoint. `from` doesn't exist yet, so this story passes the inputs
  // as the search params Learning summary Suggestions already use; the screen
  // is the same.
  render: () => <AppAt url={COPY_URL} />,
};

export const Copied: Story = {
  name: "7. The copy, drafting its outline",
  render: () => <CopiedPath />,
};

export const NotFound: Story = {
  name: "8. Not here (private or unknown)",
  render: () => <NotHere />,
};
