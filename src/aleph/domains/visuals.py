"""The ```visual fence grammar for a Read passage (CONTEXT.md: **Visual**).

A **Visual** is stored beside the Read passage, never inside it. The passage
names where it goes with an empty fenced block whose info string is exactly
``visual <ref>``::

    ```visual visual-1
    ```

The lesson agent writes a short local placeholder ref (``visual-1``) because a
model cannot mint identifiers reliably; when the lesson is persisted the
service creates the row and rewrites the fence to the row's UUID, so the API and
the client only ever see UUIDs (docs/tdds/lesson-visuals.md).

Three callers share this one grammar, which is why it lives below all of them:
the agent's output validator (every fence resolves), generation's persistence
(placeholder -> UUID) and the continuity prompt (fences stripped from earlier
lessons' passages, so a later lesson never copies another lesson's visual id).

Pure and stdlib-only (:mod:`aleph.domains`' boundary contract).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# A placeholder ref: a short lowercase slug the agent invents (``visual-1``).
_PLACEHOLDER_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
# A stored ref: the row's canonical lowercase UUID.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# Any line opening a ```visual fence, well formed or not; group 1 is the rest of
# the info string. Indented fences are deliberately not matched (CommonMark would
# read four spaces as a code block), and the info string is matched whole so
# ``visual-like`` languages (``visualbasic``) are not mistaken for one.
_OPEN_RE = re.compile(r"^```visual(?=\s|$)(.*)$")
_CLOSE = "```"


def is_placeholder_ref(ref: str) -> bool:
    """True when ``ref`` is a well-formed agent placeholder (``visual-1``)."""
    return _PLACEHOLDER_RE.fullmatch(ref) is not None


def is_stored_ref(ref: str) -> bool:
    """True when ``ref`` is a stored visual's canonical lowercase UUID."""
    return _UUID_RE.fullmatch(ref) is not None


def _parse_ref(info: str) -> str | None:
    ref = info.strip()
    if is_placeholder_ref(ref) or is_stored_ref(ref):
        return ref
    return None


def visual_fence_refs(passage: str) -> list[str | None]:
    """Every ```visual fence in ``passage``, in order, as its ref or ``None``.

    ``None`` marks a fence that cannot be resolved: no ref, more than one word,
    a ref of the wrong shape, or a non-empty body. Strict on purpose — the
    client routes a fence only by an exact UUID, so anything looser here would
    be text the learner sees as a broken block.
    """
    lines = passage.splitlines()
    refs: list[str | None] = []
    for index, line in enumerate(lines):
        match = _OPEN_RE.match(line)
        if match is None:
            continue
        closes_at_once = index + 1 < len(lines) and lines[index + 1].strip() == _CLOSE
        refs.append(_parse_ref(match.group(1)) if closes_at_once else None)
    return refs


def rewrite_visual_fences(passage: str, refs: Mapping[str, str]) -> str:
    """``passage`` with each fence's ref replaced through ``refs`` (old -> new).

    Fences whose ref is not in ``refs`` are left exactly as they are.
    """
    lines = passage.split("\n")
    for index, line in enumerate(lines):
        match = _OPEN_RE.match(line)
        if match is None:
            continue
        ref = match.group(1).strip()
        if ref in refs:
            lines[index] = f"```visual {refs[ref]}"
    return "\n".join(lines)


def strip_visual_fences(passage: str) -> str:
    """``passage`` with every well-formed, empty ```visual fence removed.

    Used for continuity: an earlier lesson's visual belongs to that lesson, so
    the later lesson's prompt carries the prose without the reference.
    """
    lines = passage.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            _OPEN_RE.match(line) is not None
            and index + 1 < len(lines)
            and lines[index + 1].strip() == _CLOSE
        ):
            index += 2
            continue
        kept.append(line)
        index += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
