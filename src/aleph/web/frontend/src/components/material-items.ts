// Pure rules for New path's Material field (docs/tdds/linked-material.md).
// The server decides what Material is readable; these only choose how an item
// is sent and how a failure reads. Keep them in step with `domains/material.py`.

import type { MaterialFailureReason, MaterialInput, MaterialKind } from "../lib/api";

export const MATERIAL_MAX_ITEMS = 5;
export const MATERIAL_TEXT_MAX_LENGTH = 200_000;
/** Below about this much text there is nothing to build lessons from. */
export const MATERIAL_MIN_WORDS = 50;

/** One item the learner added, before the path exists. */
export interface MaterialDraft extends MaterialInput {
  id: string;
}

/** A link to a player whose text the learner pastes (exploration decision 4). */
export type PasteSource = "video" | "podcast";

const PASTE_HOSTS: Record<string, PasteSource> = {
  "youtube.com": "video",
  "youtu.be": "video",
  "podcasts.apple.com": "podcast",
  "open.spotify.com": "podcast",
  "overcast.fm": "podcast",
  "pca.st": "podcast",
};

export function parseUrl(value: string): URL | null {
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

export function hostLabel(url: string): string {
  return parseUrl(url)?.hostname.replace(/^www\./, "") ?? url;
}

/** Video and podcast links open a transcript paste box instead of being read. */
export function pasteSource(url: string): PasteSource | null {
  const host = parseUrl(url)?.hostname.toLowerCase() ?? "";
  for (const [domain, source] of Object.entries(PASTE_HOSTS)) {
    if (host === domain || host.endsWith(`.${domain}`)) return source;
  }
  return null;
}

/** A first guess the learner can change: docs hosts and `/docs` paths crawl. */
export function guessLinkKind(url: string): Extract<MaterialKind, "article" | "docs"> {
  const parsed = parseUrl(url);
  if (!parsed) return "article";
  return parsed.hostname.startsWith("docs.") || /\/docs(\/|$)/.test(parsed.pathname)
    ? "docs"
    : "article";
}

export function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

export const KIND_LABEL: Record<MaterialKind, string> = {
  article: "Article",
  docs: "Docs",
  text: "Pasted text",
  ai_summary: "AI summary",
};

export const FAILURE_COPY: Record<MaterialFailureReason, string> = {
  unreadable: "Aleph couldn't read this page. Paste its text instead, or try another link.",
  empty:
    "This page had almost no text, often a paywall or a page that needs a browser. Paste its text instead.",
  paste_transcript: "Aleph can't read video or podcast links. Paste the transcript instead.",
  too_large: "There's more Material than one path can hold. Remove an item or trim the text.",
};

/** The wire item, without the client-only id. */
export function toInput({ id: _id, ...input }: MaterialDraft): MaterialInput {
  return input;
}
