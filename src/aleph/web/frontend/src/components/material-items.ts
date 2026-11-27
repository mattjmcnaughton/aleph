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

// Mirrors `domains.material.arxiv_id`; the server decides.
const ARXIV_ID = /^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*\/\d{7})(?:v\d{1,3})?$/i;
const ARXIV_HOSTS = new Set(["arxiv.org", "www.arxiv.org", "export.arxiv.org"]);

/** A bare arXiv id (`1706.03762`) becomes its abstract page link. */
export function normalizeLink(value: string): string {
  const trimmed = value.trim();
  return ARXIV_ID.test(trimmed) ? `https://arxiv.org/abs/${trimmed}` : trimmed;
}

/** True for an arXiv `/abs`, `/pdf` or `/html` paper link (papers TDD D1). */
export function isArxivPaper(url: string): boolean {
  const parsed = parseUrl(url);
  if (!parsed || !ARXIV_HOSTS.has(parsed.hostname.toLowerCase())) return false;
  const match = /^\/(?:abs|pdf|html)\/(.+?)\/?$/.exec(parsed.pathname);
  return match !== null && ARXIV_ID.test(match[1].replace(/\.pdf$/, ""));
}

/** A first guess the learner can change: docs hosts and `/docs` paths crawl. */
export function guessLinkKind(url: string): Extract<MaterialKind, "article" | "docs" | "arxiv"> {
  if (isArxivPaper(url)) return "arxiv";
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
  arxiv: "Paper",
};

export const FAILURE_COPY: Record<MaterialFailureReason, string> = {
  unreadable: "Aleph couldn't read this page. Paste its text instead, or try another link.",
  empty:
    "This page had almost no text, often a paywall or a page that needs a browser. Paste its text instead.",
  paste_transcript: "Aleph can't read video or podcast links. Paste the transcript instead.",
  too_large: "There's more Material than one path can hold. Remove an item or trim the text.",
  no_html:
    "arXiv has no HTML version of this paper yet, so Aleph can't read it. Paste its text instead.",
};

/** The wire item, without the client-only id. */
export function toInput({ id: _id, ...input }: MaterialDraft): MaterialInput {
  return input;
}
