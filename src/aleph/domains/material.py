"""Material rules (CONTEXT.md: *Material*, *Material snapshot*, *Material mode*).

Pure: no I/O. The service reads Material and stores the snapshot; this module
decides what a valid item is, how its text is cut into **sections**, what a
docs crawl may include, and how big a Teach this path should be.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

# Limits for the first slice (docs/tdds/linked-material.md §Limits).
MAX_ITEMS = 5
MAX_TEXT_CHARS = 200_000
MAX_PATH_CHARS = 400_000
MAX_DOCS_PAGES = 30
MAX_SECTION_CHARS = 8_000
# Below this an item is "empty": a paywall teaser or a page built in the
# browser. Failure, not a tiny success (Constitution §4).
MIN_ITEM_CHARS = 200


class MaterialKind(StrEnum):
    """How an item reaches Aleph: read from a link, or pasted."""

    ARTICLE = "article"
    DOCS = "docs"
    TEXT = "text"
    AI_SUMMARY = "ai_summary"


class MaterialMode(StrEnum):
    """*Teach this* stays inside the Material; *Start from this* builds around it."""

    TEACH = "teach"
    START = "start"


class MaterialFailure(StrEnum):
    """Why one item could not become part of a snapshot."""

    UNREADABLE = "unreadable"
    EMPTY = "empty"
    PASTE_TRANSCRIPT = "paste_transcript"
    TOO_LARGE = "too_large"


LINK_KINDS = frozenset({MaterialKind.ARTICLE, MaterialKind.DOCS})

# Hosts whose links carry no readable text for us: video and podcast players.
# The learner pastes the transcript instead (exploration decision 4).
_PASTE_HOSTS = (
    "youtube.com",
    "youtu.be",
    "podcasts.apple.com",
    "open.spotify.com",
    "overcast.fm",
    "pca.st",
)


@dataclass(frozen=True)
class Section:
    """One piece of a Material snapshot, cut along a boundary the text has."""

    title: str
    text: str


@dataclass(frozen=True)
class Page:
    """One page of a docs collection, as read."""

    title: str
    url: str
    text: str


def needs_paste(url: str) -> bool:
    """True for video and podcast links, which are taught from a pasted transcript."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in _PASTE_HOSTS)


def in_crawl_scope(root_url: str, page_url: str) -> bool:
    """A docs crawl keeps only pages on the root's host under the root's path."""
    root, page = urlparse(root_url), urlparse(page_url)
    if (root.hostname or "").lower() != (page.hostname or "").lower():
        return False
    prefix = root.path.rstrip("/")
    path = page.path.rstrip("/")
    return path == prefix or path.startswith(f"{prefix}/")


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_TIMESTAMP_RE = re.compile(r"^\s*\[?((?:\d{1,2}:)?\d{1,2}:\d{2})\]?\b", re.MULTILINE)
# Transcript windows: about five minutes of talk each.
_WINDOW_SECONDS = 300


def _seconds(stamp: str) -> int:
    total = 0
    for part in stamp.split(":"):
        total = total * 60 + int(part)
    return total


def _by_headings(text: str, fallback_title: str) -> list[Section]:
    matches = list(_HEADING_RE.finditer(text))
    sections: list[Section] = []
    lead = text[: matches[0].start()].strip()
    if lead:
        sections.append(Section(fallback_title, lead))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if body:
            sections.append(Section(match.group(2).strip(), body))
    return sections


def _by_timestamps(text: str) -> list[Section]:
    sections: list[Section] = []
    start_stamp: str | None = None
    start_seconds = 0
    lines: list[str] = []
    for line in text.splitlines():
        match = _TIMESTAMP_RE.match(line)
        if match:
            seconds = _seconds(match.group(1))
            if start_stamp is None:
                start_stamp, start_seconds = match.group(1), seconds
            elif seconds - start_seconds >= _WINDOW_SECONDS and lines:
                sections.append(Section(f"From {start_stamp}", "\n".join(lines)))
                start_stamp, start_seconds, lines = match.group(1), seconds, []
        lines.append(line)
    if lines:
        sections.append(Section(f"From {start_stamp or '0:00'}", "\n".join(lines)))
    return sections


def _cap_size(section: Section) -> list[Section]:
    """Split an oversized section on paragraph breaks into numbered parts."""
    if len(section.text) <= MAX_SECTION_CHARS:
        return [section]
    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", section.text):
        while len(paragraph) > MAX_SECTION_CHARS:
            if current:
                parts.append(current)
                current = ""
            parts.append(paragraph[:MAX_SECTION_CHARS])
            paragraph = paragraph[MAX_SECTION_CHARS:]
        joined = f"{current}\n\n{paragraph}" if current else paragraph
        if len(joined) > MAX_SECTION_CHARS:
            parts.append(current)
            current = paragraph
        else:
            current = joined
    if current:
        parts.append(current)
    return [
        Section(f"{section.title} ({n}/{len(parts)})", part.strip())
        for n, part in enumerate(parts, start=1)
    ]


def split_sections(text: str, *, fallback_title: str) -> list[Section]:
    """Cut one item's text into sections along the breaks it already has.

    Markdown headings first (articles, docs pages, AI summaries), then
    transcript timestamps in five-minute windows, then the whole text. Any
    section past ``MAX_SECTION_CHARS`` is split on paragraph breaks, so a
    lesson's mapped sections stay a bounded prompt.
    """
    stripped = text.strip()
    if not stripped:
        return []
    sections: list[Section] = []
    if _HEADING_RE.search(stripped):
        sections = _by_headings(stripped, fallback_title)
    elif len(_TIMESTAMP_RE.findall(stripped)) >= 2:
        sections = _by_timestamps(stripped)
    if not sections:
        sections = [Section(fallback_title, stripped)]
    return [part for section in sections for part in _cap_size(section)]


def split_pages(pages: list[Page]) -> list[Section]:
    """A docs collection: one or more sections per page, titled by the page."""
    sections: list[Section] = []
    for page in pages:
        for section in split_sections(page.text, fallback_title=page.title):
            title = section.title
            if title != page.title and not title.startswith(page.title):
                title = f"{page.title}: {title}"
            sections.append(Section(title, section.text))
    return sections


@dataclass(frozen=True)
class TeachSize:
    """Prompt targets for a Teach this outline, derived from snapshot length."""

    lessons: int
    units: int


def teach_size(total_chars: int) -> TeachSize:
    """Short Material makes a short path: about one lesson per 6k characters.

    At least 2 lessons (one article is still a lesson and a recap) and at most
    30, in units of about five lessons.
    """
    lessons = max(2, min(30, round(total_chars / 6_000)))
    return TeachSize(lessons=lessons, units=max(1, math.ceil(lessons / 5)))
