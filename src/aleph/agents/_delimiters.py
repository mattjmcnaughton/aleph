"""Neutralise learner text that would forge the tags around its own prompt block.

Learner free text (a path's Guidance, its Teaching style) is interpolated raw
between a pair of tags such as ``<guidance>``/``</guidance>``. Without this, a
learner could write their own closing tag and continue with text the model
would read as outside the block, i.e. as if the prompt itself had said it.

Shared by ``agents/outline.py`` and ``agents/lesson.py`` so the two blocks are
defended by one rule. The match is tolerant on purpose: whitespace inside the
tag (``</ guidance >``, ``< /guidance>``), attributes (``<guidance x="1">``) and
any case are all struck, because a model reads each of those as the tag.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"


def neutralize_delimiters(text: str, tag: str) -> str:
    """Replace every opening or closing ``tag`` token in ``text`` with a marker."""
    pattern = re.compile(rf"<\s*/?\s*{re.escape(tag)}\b[^>]*>", re.IGNORECASE)
    return pattern.sub(REDACTED, text)
