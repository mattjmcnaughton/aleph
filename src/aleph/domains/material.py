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
# One paper, after its bibliography is dropped (papers TDD D4).
MAX_PAPER_CHARS = 150_000
# Below this an item is "empty": a paywall teaser or a page built in the
# browser. Failure, not a tiny success (Constitution §4).
MIN_ITEM_CHARS = 200


class MaterialKind(StrEnum):
    """How an item reaches Aleph: read from a link, or pasted."""

    ARTICLE = "article"
    DOCS = "docs"
    TEXT = "text"
    AI_SUMMARY = "ai_summary"
    # A research paper read from arXiv's own HTML (docs/tdds/papers-material.md).
    ARXIV = "arxiv"


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
    # arXiv has no HTML version of the paper (or LaTeXML failed on it).
    NO_HTML = "no_html"


LINK_KINDS = frozenset({MaterialKind.ARTICLE, MaterialKind.DOCS, MaterialKind.ARXIV})

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


# New-style arXiv ids (``2106.09685``, ``1706.03762v7``) and old-style ones
# (``hep-th/9901001``), with an optional version.
_ARXIV_ID_RE = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v\d{1,3})?$", re.IGNORECASE
)
_ARXIV_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
_ARXIV_PREFIXES = ("/abs/", "/pdf/", "/html/")


def arxiv_id(url: str) -> str | None:
    """The paper id of an arXiv ``/abs``, ``/pdf`` or ``/html`` link, else None.

    Aleph builds every arXiv request from this id on arXiv's own hosts, so the
    learner's URL is never fetched as given (papers TDD D2). Anything that is
    not exactly a paper link on an arXiv host is not a paper.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return None
    if (parsed.hostname or "").lower() not in _ARXIV_HOSTS:
        return None
    path = parsed.path
    prefix = next((p for p in _ARXIV_PREFIXES if path.startswith(p)), None)
    if prefix is None:
        return None
    candidate = path[len(prefix) :].rstrip("/").removesuffix(".pdf")
    return candidate if _ARXIV_ID_RE.fullmatch(candidate) else None


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


# --- papers: core concepts and prep lessons (docs/tdds/papers-material.md) ----

# At most this many prep lessons; 1 is the orientation allowance Teach this
# already had, the rest exist for prerequisites Guidance says the learner lacks.
MAX_PREP_LESSONS = 4


@dataclass(frozen=True)
class Concept:
    """One core concept of a paper, as its digest names it."""

    id: str
    name: str
    sections: tuple[int, ...]
    builds_on: tuple[str, ...]


def digest_problems(concepts: list[Concept], section_ids: frozenset[int]) -> list[str]:
    """Why a paper's digest is invalid, or an empty list when it is valid.

    Ids are unique, every section is the paper's own, ``builds_on`` names
    concepts in the same digest, and the dependencies form no cycle, so an
    order that teaches every concept after what it builds on always exists.
    """
    problems: list[str] = []
    ids = [concept.id for concept in concepts]
    if not concepts:
        problems.append("the digest names no concepts")
    if len(set(ids)) != len(ids):
        problems.append("concept ids repeat")
    known = set(ids)
    for concept in concepts:
        foreign = sorted(set(concept.sections) - section_ids)
        if foreign:
            problems.append(f"{concept.id} names sections not in the paper: {foreign}")
        if not concept.sections:
            problems.append(f"{concept.id} names no sections")
        unknown = sorted(set(concept.builds_on) - known)
        if unknown:
            problems.append(f"{concept.id} builds on unknown concepts: {unknown}")
    if not problems and _has_cycle(concepts):
        problems.append("builds_on forms a cycle")
    return problems


def _has_cycle(concepts: list[Concept]) -> bool:
    edges = {concept.id: concept.builds_on for concept in concepts}
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(node: str) -> bool:
        if node in done:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        cyclic = any(visit(parent) for parent in edges.get(node, ()))
        visiting.discard(node)
        done.add(node)
        return cyclic

    return any(visit(node) for node in edges)


@dataclass(frozen=True)
class PlannedLesson:
    """One outline lesson as the paper rules see it."""

    title: str
    concepts: tuple[str, ...]
    prerequisite: str | None


def concept_plan_problems(
    lessons: list[PlannedLesson],
    concepts: list[Concept],
    prerequisites: frozenset[str],
    *,
    teach: bool,
) -> list[str]:
    """Why an outline does not teach a paper incrementally, or ``[]``.

    Every named concept and prerequisite exists; in Teach this every concept is
    taught; no lesson teaches a concept before the concepts it builds on; and
    prep lessons teach one known prerequisite each, before any paper lesson,
    at most ``MAX_PREP_LESSONS`` of them.
    """
    problems: list[str] = []
    by_id = {concept.id: concept for concept in concepts}
    taught_at: dict[str, int] = {}
    seen_paper_lesson = False
    prep = 0
    for index, lesson in enumerate(lessons):
        if lesson.prerequisite is not None:
            prep += 1
            if lesson.prerequisite.strip().casefold() not in prerequisites:
                problems.append(
                    f"'{lesson.title}' teaches a prerequisite the digests do not "
                    f"list: {lesson.prerequisite!r}"
                )
            if lesson.concepts:
                problems.append(f"prep lesson '{lesson.title}' also names concepts")
            if seen_paper_lesson:
                problems.append(
                    f"prep lesson '{lesson.title}' comes after a lesson on the paper"
                )
            continue
        if lesson.concepts:
            seen_paper_lesson = True
        for concept_id in lesson.concepts:
            if concept_id not in by_id:
                problems.append(f"'{lesson.title}' names unknown concept {concept_id}")
                continue
            taught_at.setdefault(concept_id, index)
    if prep > MAX_PREP_LESSONS:
        problems.append(f"{prep} prep lessons; at most {MAX_PREP_LESSONS} are allowed")
    for concept_id, index in taught_at.items():
        for parent in by_id[concept_id].builds_on:
            parent_at = taught_at.get(parent)
            if parent_at is None or parent_at > index:
                problems.append(
                    f"{concept_id} ({by_id[concept_id].name}) is taught before "
                    f"{parent} ({by_id[parent].name}), which it builds on"
                )
    if teach:
        missing = sorted(set(by_id) - set(taught_at))
        if missing:
            problems.append(f"no lesson teaches these concepts: {missing}")
    return problems
