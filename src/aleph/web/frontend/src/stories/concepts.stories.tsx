import type { Meta, StoryObj } from "@storybook/react-vite";
import type { CSSProperties } from "react";

// Design directions considered for the Nocturne redesign, drawn on the same
// content so they can be compared side by side (docs/design.md records why
// B was chosen). These are static specimens, not app screens: the Screens
// stories show the direction that shipped. Each one renders the three moments
// that carry the product: resuming from home, the syllabus, and reading.

interface Direction {
  name: string;
  verdict: string;
  bg: string;
  surface: string;
  text: string;
  muted: string;
  rule: string;
  accent: string;
  onAccent: string;
  tutor: string;
  ui: string;
  reading: string;
  mono: string;
  titleWeight: number;
}

const SYSTEM_SANS = "system-ui, -apple-system, sans-serif";
const UI_SANS = '"Aleph Interpunct", "Instrument Sans Variable", system-ui, sans-serif';
const READING_SERIF = '"Aleph Interpunct", "Literata Variable", Charter, Georgia, serif';
const CODE_MONO = '"JetBrains Mono Variable", ui-monospace, monospace';

const DIRECTIONS = {
  tightened: {
    name: "A. Nocturne, tightened",
    verdict:
      "The shipped palette and system sans, with only the structure fixed (no capital-letter labels, ruled lists, one primary action). Cheapest, but it still reads as a dark-mode dashboard: the violet-navy and bright cyan are what date it.",
    bg: "#161826",
    surface: "#232532",
    text: "#e9e9ed",
    muted: "#9397ab",
    rule: "#e9e9ed29",
    accent: "#4fb8c4",
    onAccent: "#161826",
    tutor: "#b5abfc",
    ui: SYSTEM_SANS,
    reading: SYSTEM_SANS,
    mono: "ui-monospace, monospace",
    titleWeight: 600,
  },
  study: {
    name: "B. Study (recommended, shipped)",
    verdict:
      "Neutral ink, warm paper text, a serif made for reading on screens, and accents that each mean one thing. It keeps Nocturne's promise (dark, teal, phone first) and reads as a place to study rather than a game.",
    bg: "#11161b",
    surface: "#182027",
    text: "#ebe6da",
    muted: "#a5a89f",
    rule: "#ebe6da1f",
    accent: "#56b3ab",
    onAccent: "#11161b",
    tutor: "#b3acea",
    ui: UI_SANS,
    reading: READING_SERIF,
    mono: CODE_MONO,
    titleWeight: 500,
  },
  daylight: {
    name: "C. Daylight",
    verdict:
      "Direction B on paper. Long passages are easier in daylight and many readers expect the choice, but Nocturne is defined as dark and every surface would need a second pass. Worth doing next as an optional light theme, not instead of B.",
    bg: "#f3f3ef",
    surface: "#ffffff",
    text: "#1a2026",
    muted: "#5d666c",
    rule: "#1a202624",
    accent: "#1c7b72",
    onAccent: "#ffffff",
    tutor: "#5b50a8",
    ui: UI_SANS,
    reading: READING_SERIF,
    mono: CODE_MONO,
    titleWeight: 500,
  },
} satisfies Record<string, Direction>;

const SYLLABUS = [
  { title: "The borrow checker's three rules", state: "done" },
  { title: "Reading lifetime annotations", state: "current" },
  { title: "Lifetimes in structs", state: "locked" },
] as const;

function Specimen({ direction }: { direction: Direction }) {
  const d = direction;
  const rule: CSSProperties = { borderColor: d.rule };
  const title: CSSProperties = {
    fontFamily: d.reading,
    fontWeight: d.titleWeight,
    letterSpacing: "-0.012em",
  };

  return (
    <div
      className="min-h-screen px-5 py-6"
      style={{ background: d.bg, color: d.text, fontFamily: d.ui }}
    >
      <div className="mx-auto max-w-[560px]">
        <p className="text-sm font-semibold">{d.name}</p>
        <p className="mt-1 text-sm leading-6" style={{ color: d.muted }}>
          {d.verdict}
        </p>

        {/* Resuming from home */}
        <div className="mt-8 rounded-xl border p-5" style={{ ...rule, background: d.surface }}>
          <p className="text-sm" style={{ color: d.muted }}>
            Rust ownership &amp; lifetimes · 4 of 11 complete
          </p>
          <p className="mt-2 text-[1.375rem] leading-snug" style={title}>
            Reading lifetime annotations
          </p>
          <div className="mt-5 flex items-center gap-4">
            <span
              className="h-1 flex-1 overflow-hidden rounded-full"
              style={{ background: d.rule }}
            >
              <span className="block h-full w-[36%]" style={{ background: d.accent }} />
            </span>
            <span
              className="inline-flex min-h-11 items-center rounded-lg px-4 text-[15px] font-semibold"
              style={{ background: d.accent, color: d.onAccent }}
            >
              Continue
            </span>
          </div>
        </div>

        {/* The syllabus */}
        <p className="mt-10 text-[13px] font-medium" style={{ color: d.muted }}>
          Unit 2
        </p>
        <p className="mt-1 text-xl leading-snug" style={title}>
          Lifetimes in practice
        </p>
        <ul className="mt-3 border-t" style={rule}>
          {SYLLABUS.map((lesson) => (
            <li
              key={lesson.title}
              className="flex min-h-[52px] items-center gap-3 border-b px-2 text-[15px]"
              style={{
                ...rule,
                color: lesson.state === "current" ? d.text : d.muted,
                fontWeight: lesson.state === "current" ? 600 : 400,
                boxShadow: lesson.state === "current" ? `inset 2px 0 0 ${d.accent}` : undefined,
                opacity: lesson.state === "locked" ? 0.7 : 1,
              }}
            >
              <span
                aria-hidden="true"
                className="h-2 w-2 shrink-0 rounded-full"
                style={{
                  background: lesson.state === "current" ? d.accent : "transparent",
                  border: lesson.state === "current" ? undefined : `1px solid ${d.muted}`,
                }}
              />
              {lesson.title}
            </li>
          ))}
        </ul>

        {/* Reading */}
        <p className="mt-10 text-[13px] font-medium" style={{ color: d.muted }}>
          Lesson 5 of 11, Lifetimes in practice
        </p>
        <h1 className="mt-2 text-[1.75rem] leading-[1.16]" style={title}>
          Reading lifetime annotations
        </h1>
        <p className="mt-5 text-[1.0625rem] leading-[1.7]" style={{ fontFamily: d.reading }}>
          A <strong>lifetime</strong> is the compiler's name for{" "}
          <em>how long a reference is guaranteed to point at valid data</em>. Read{" "}
          <code
            className="rounded px-1 py-0.5 text-[0.82em]"
            style={{ fontFamily: d.mono, background: d.rule }}
          >
            'a
          </code>{" "}
          as a promise: the returned reference lives no longer than the shorter of its inputs.
        </p>
        <div
          className="mt-6 flex h-12 items-center gap-2.5 rounded-xl border px-3.5 text-[15px]"
          style={{ ...rule, background: d.surface, color: d.muted }}
        >
          <span
            aria-hidden="true"
            className="h-2.5 w-2.5 rounded-full"
            style={{ background: d.tutor }}
          />
          Ask the tutor about this lesson
        </div>
      </div>
    </div>
  );
}

const meta = {
  title: "Concepts/Directions",
  component: Specimen,
} satisfies Meta<typeof Specimen>;
export default meta;
type Story = StoryObj<typeof meta>;

export const NocturneTightened: Story = {
  name: "A. Nocturne, tightened",
  args: { direction: DIRECTIONS.tightened },
};

export const Study: Story = {
  name: "B. Study (recommended)",
  args: { direction: DIRECTIONS.study },
};

export const Daylight: Story = {
  name: "C. Daylight",
  args: { direction: DIRECTIONS.daylight },
};

/** All three at once, for a single-glance comparison at desktop widths. */
export const SideBySide: Story = {
  name: "Side by side",
  args: { direction: DIRECTIONS.study },
  parameters: { viewport: { value: "desktop" } },
  globals: { viewport: { value: "desktop", isRotated: false } },
  render: () => (
    <div className="grid min-w-[1200px] grid-cols-3">
      <Specimen direction={DIRECTIONS.tightened} />
      <Specimen direction={DIRECTIONS.study} />
      <Specimen direction={DIRECTIONS.daylight} />
    </div>
  ),
};
