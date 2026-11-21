import type { Meta, StoryObj } from "@storybook/react-vite";
import { ActivityStrip } from "../components/activity-strip";
import { AlephLogo } from "../components/aleph-logo";
import { Markdown } from "../components/markdown";
import { StateCard } from "../components/state-card";
import { StreakChip } from "../components/streak-chip";
import { LIFETIMES_PASSAGE, worldProgress } from "./world";

// Shared building blocks in isolation, for work on one component at a time.
// Screens (`screens.stories.tsx`) show them in context. Add a story here when a
// redesign touches a component that several screens reuse.

function Frame({ children }: { children: React.ReactNode }) {
  return <div className="min-h-screen bg-night p-5 text-porcelain">{children}</div>;
}

const meta = {
  title: "Components",
  decorators: [(Story) => <Frame>{Story()}</Frame>],
} satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

export const Logo: Story = { render: () => <AlephLogo /> };

export const ReadPassage: Story = {
  name: "Markdown — Read passage",
  render: () => <Markdown className="text-base">{LIFETIMES_PASSAGE}</Markdown>,
};

export const Activity: Story = {
  name: "Activity strip and streak chip",
  render: () => (
    <div className="space-y-4">
      <StreakChip days={12} />
      <ActivityStrip activity={worldProgress().activity} />
    </div>
  ),
};

export const StateCards: Story = {
  name: "State cards",
  render: () => (
    <div>
      {(["neutral", "error", "refusal"] as const).map((variant) => (
        <StateCard key={variant} variant={variant} testid={`state-${variant}`}>
          <h2 className="text-lg font-semibold">{variant} card</h2>
          <p className="mt-2 text-sm text-mist">
            Body copy explaining what happened and what to do.
          </p>
        </StateCard>
      ))}
    </div>
  ),
};
