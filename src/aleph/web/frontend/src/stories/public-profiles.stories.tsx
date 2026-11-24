/// <reference types="msw-storybook-addon/csf3" />
import type { Meta, StoryObj } from "@storybook/react-vite";
import type { AuthSession, AuthUser } from "../lib/api";
import { seedPath } from "../mocks/paths";
import {
  AI_BEAT_ID,
  AI_BRIEF_ID,
  MATT,
  RUST_PATH_ID,
  configurePublicSettings,
  seedPublicWorld,
} from "../mocks/public";
import { AppAt } from "./app-at";
import { sessionHandler, seedWorld, signedOutWorldSession, worldSession } from "./world";

// Public profiles (docs/tdds/public-profiles.md), every real screen and state,
// rendered by the real router against the fakes in `mocks/public.ts`. This
// replaces the concept storyboard the design was decided on. Check each at the
// iPhone and desktop viewports.

const worldUser = worldSession.user as AuthUser;
const profilesOnSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: {
    ...worldUser,
    feature_flags: { ...worldUser.feature_flags, public_profiles: true },
  },
};

const meta = {
  title: "Screens/Public profiles",
  component: AppAt,
  beforeEach: () => {
    seedWorld();
    seedPublicWorld();
  },
} satisfies Meta<typeof AppAt>;

export default meta;
type Story = StoryObj<typeof meta>;

const signedOut: Story["beforeEach"] = ({ msw }) => {
  msw.use(sessionHandler(signedOutWorldSession));
};

// --- The visitor's pages ---------------------------------------------------------

export const Profile: Story = {
  name: "Profile — signed-out visitor",
  args: { url: `/@${MATT.handle}` },
  beforeEach: signedOut,
};

export const ProfileSignedIn: Story = {
  name: "Profile — signed-in visitor",
  args: { url: `/@${MATT.handle}` },
};

export const SharedPath: Story = {
  name: "Shared path — signed-out visitor",
  args: { url: `/@${MATT.handle}/paths/${RUST_PATH_ID}` },
  beforeEach: signedOut,
};

export const SharedPathSignedIn: Story = {
  name: "Shared path — signed-in visitor",
  args: { url: `/@${MATT.handle}/paths/${RUST_PATH_ID}` },
};

export const SharedBeat: Story = {
  name: "Shared Beat — signed-out visitor",
  args: { url: `/@${MATT.handle}/beats/${AI_BEAT_ID}` },
  beforeEach: signedOut,
};

export const SharedBrief: Story = {
  name: "Shared Brief — signed-out visitor",
  args: { url: `/@${MATT.handle}/beats/${AI_BEAT_ID}/briefs/${AI_BRIEF_ID}` },
  beforeEach: signedOut,
};

export const NotHere: Story = {
  name: "Not here (private, unknown or unshared)",
  args: { url: "/@nobody" },
  beforeEach: signedOut,
};

// --- Copy ------------------------------------------------------------------------

export const CopyPath: Story = {
  name: "Copy a path — New path, prefilled",
  args: { url: `/new?from=${MATT.handle}/${RUST_PATH_ID}` },
};

export const CopyPathMissing: Story = {
  name: "Copy a path — source no longer shared",
  args: { url: `/new?from=${MATT.handle}/00000000-0000-4000-8000-000000000000` },
};

export const CopyBeat: Story = {
  name: "Copy a Beat — New beat, prefilled",
  args: { url: `/beats/new?from=${MATT.handle}/${AI_BEAT_ID}` },
};

export const Copied: Story = {
  name: "The copy, drafting its outline with credit",
  args: { url: "/paths/p-copied" },
  beforeEach: () => {
    seedPath({
      id: "p-copied",
      topic: "Rust ownership & lifetimes",
      level: "some_experience",
      pollsRemaining: 1_000_000,
      source: { display_name: MATT.display_name, handle: MATT.handle, path_id: RUST_PATH_ID },
    });
  },
};

// --- The owner's settings --------------------------------------------------------

export const SettingsOff: Story = {
  name: "Settings — profile off",
  args: { url: "/settings/public-profile" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(profilesOnSession));
  },
};

export const SettingsOn: Story = {
  name: "Settings — profile on, choosing what to share",
  args: { url: "/settings/public-profile" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(profilesOnSession));
    configurePublicSettings({ handle: MATT.handle, profile_public: true });
  },
};
