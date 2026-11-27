/// <reference types="msw-storybook-addon/csf3" />
import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { MaterialField, type MaterialFieldProps } from "../components/material-field";
import type { MaterialDraft } from "../components/material-items";
import type { AuthSession, AuthUser, MaterialMode } from "../lib/api";
import { AppAt } from "./app-at";
import { sessionHandler, seedWorld, worldSession } from "./world";

// Linked Material (docs/tdds/linked-material.md): the real Material field on
// New path, and its states. Replaces the exploration's concept storyboard.
// Check each at the iPhone and desktop viewports.

const DOCS: MaterialDraft = { id: "m1", kind: "docs", url: "https://docs.warp.dev/factories" };
const ARTICLE: MaterialDraft = {
  id: "m2",
  kind: "article",
  url: "https://martinfowler.com/articles/software-factory.html",
};
const SUMMARY: MaterialDraft = {
  id: "m3",
  kind: "ai_summary",
  title: "NotebookLM summary: Accelerate",
  text: "word ".repeat(2600).trim(),
};
const PAYWALLED: MaterialDraft = {
  id: "m4",
  kind: "article",
  url: "https://www.nytimes.com/2026/11/01/technology/ai-agents.html",
};

function Field(props: Omit<MaterialFieldProps, "onItemsChange" | "onModeChange">) {
  const [items, setItems] = useState(props.items);
  const [mode, setMode] = useState<MaterialMode>(props.mode);
  return (
    <main className="page">
      <MaterialField
        {...props}
        items={items}
        onItemsChange={setItems}
        mode={mode}
        onModeChange={setMode}
      />
    </main>
  );
}

const meta = {
  title: "Screens/Linked Material",
  component: Field,
  args: { items: [], mode: "teach" },
} satisfies Meta<typeof Field>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = { name: "1. Empty" };

export const ItemsAndMode: Story = {
  name: "2. Docs, article and AI summary added",
  args: { items: [DOCS, ARTICLE, SUMMARY], mode: "teach" },
};

export const VideoPaste: Story = {
  name: "3. A video link opens the transcript box",
  args: {
    initialEntry: {
      state: "paste",
      kind: "text",
      source: "video",
      url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    },
  },
};

export const SummaryPaste: Story = {
  name: "4. Paste an AI summary",
  args: { items: [DOCS], initialEntry: { state: "paste", kind: "ai_summary" } },
};

export const ReadFailed: Story = {
  name: "5. A page Aleph couldn't read",
  args: { items: [DOCS, PAYWALLED], mode: "start", failures: { 1: "empty" } },
};

const materialSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: {
    ...(worldSession.user as AuthUser),
    feature_flags: { ...(worldSession.user as AuthUser).feature_flags, material: true },
  },
};

export const NewPathScreen: StoryObj<typeof AppAt> = {
  name: "6. On the New path form",
  render: () => <AppAt url="/new?topic=Warp%20factories" />,
  beforeEach: ({ msw }) => {
    seedWorld();
    msw.use(sessionHandler(materialSession));
  },
};
