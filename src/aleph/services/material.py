"""Reading Material into a snapshot, and shaping it for generation.

At path creation the route calls :func:`read_snapshot`: links are read through
Exa (so Aleph's servers never fetch a learner-supplied URL), pasted text is
taken as given, and everything is cut into sections (``domains/material.py``).
Any item that cannot be read fails the whole request with a reason per item,
before a path exists (Constitution §4); an Exa outage is
:class:`RetrievalUnavailableError`, never an empty snapshot.

The orchestrator then calls :func:`outline_material` and
:func:`lesson_material` to turn stored sections into the agents' inputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

import httpx
import structlog

from aleph.agents._material import (
    LessonMaterial,
    MaterialPassage,
    MaterialSectionDigest,
    OutlineMaterial,
)
from aleph.config import STUB_MODEL_ID
from aleph.config import settings as global_settings
from aleph.domains.material import (
    LINK_KINDS,
    MAX_DOCS_PAGES,
    MAX_PATH_CHARS,
    MIN_ITEM_CHARS,
    MaterialFailure,
    MaterialKind,
    MaterialMode,
    Page,
    in_crawl_scope,
    needs_paste,
    split_pages,
    split_sections,
    teach_size,
)
from aleph.repositories.materials import NewMaterial, NewSection, SectionRow
from aleph.services.retrieval import RetrievalUnavailableError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from aleph.config import Settings

logger = structlog.get_logger(__name__)

# Below this the outline sees every section whole; above it, each section's
# opening only. A lesson always gets its mapped sections whole, up to its cap.
OUTLINE_FULL_TEXT_CHARS = 40_000
OUTLINE_EXCERPT_CHARS = 500
LESSON_MATERIAL_CHARS = 32_000

_EXA_BASE_URL = "https://api.exa.ai"
_EXA_CONTENTS_PATH = "/contents"
_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class MaterialItem:
    """One item as the learner sent it (``dtos/material.py`` validated it)."""

    kind: MaterialKind
    url: str | None = None
    title: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class ItemFailure:
    index: int
    reason: MaterialFailure


class MaterialUnreadableError(Exception):
    """At least one item could not be read; nothing was stored."""

    def __init__(self, failures: Sequence[ItemFailure]) -> None:
        super().__init__(f"{len(failures)} Material item(s) could not be read")
        self.failures = tuple(failures)


@dataclass(frozen=True)
class MaterialSnapshot:
    mode: MaterialMode
    items: tuple[NewMaterial, ...]


class MaterialReader(Protocol):
    """Reads one link: a single page, or a docs collection when ``crawl``.

    Returns the pages, or a :class:`MaterialFailure` for this link. Raises
    :class:`RetrievalUnavailableError` when the provider itself is down.
    """

    async def read(self, url: str, *, crawl: bool) -> list[Page] | MaterialFailure: ...


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


class ExaMaterialReader:
    """Exa ``/contents``: full text for one URL, plus up to 30 subpages to crawl."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._transport = transport

    async def read(self, url: str, *, crawl: bool) -> list[Page] | MaterialFailure:
        payload: dict[str, object] = {"urls": [url], "text": True}
        if crawl:
            payload["subpages"] = MAX_DOCS_PAGES
        try:
            async with httpx.AsyncClient(
                base_url=_EXA_BASE_URL,
                timeout=_TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    _EXA_CONTENTS_PATH,
                    json=payload,
                    headers={"x-api-key": self._api_key},
                )
            response.raise_for_status()
            data = response.json()
            results = data["results"]
            if not isinstance(results, list):
                raise TypeError("'results' is not a list")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise RetrievalUnavailableError(f"Exa contents failed: {exc}") from exc

        statuses = data.get("statuses") or []
        if (
            any(isinstance(s, dict) and s.get("status") == "error" for s in statuses)
            or not results
        ):
            return MaterialFailure.UNREADABLE

        pages: list[Page] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            entries = [item]
            if crawl:
                entries += [
                    s for s in item.get("subpages") or [] if isinstance(s, dict)
                ]
            for entry in entries:
                page_url = _str(entry.get("url")) or url
                if crawl and not in_crawl_scope(url, page_url):
                    continue
                if any(page.url == page_url for page in pages):
                    continue
                pages.append(
                    Page(
                        title=_str(entry.get("title")).strip() or _host(page_url),
                        url=page_url,
                        text=_str(entry.get("text")),
                    )
                )
        return pages[: MAX_DOCS_PAGES + 1]


class StubMaterialReader:
    """Deterministic pages for dev, CI and e2e, which never reach Exa.

    A URL containing ``unreadable`` fails as unreadable and one containing
    ``paywall`` comes back empty, so the failure paths can be exercised.
    """

    async def read(self, url: str, *, crawl: bool) -> list[Page] | MaterialFailure:
        if "unreadable" in url:
            return MaterialFailure.UNREADABLE
        if "paywall" in url:
            return [Page(title="Subscribe to keep reading", url=url, text="Subscribe.")]
        name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or _host(url)
        name = name.replace("-", " ").replace("_", " ").strip().capitalize()
        count = 4 if crawl else 1
        return [
            Page(
                title=f"{name}: part {n}" if crawl else name,
                url=f"{url.rstrip('/')}/part-{n}" if crawl and n > 1 else url,
                text=_stub_text(name, n, url),
            )
            for n in range(1, count + 1)
        ]


def _stub_text(name: str, n: int, url: str) -> str:
    seed = int.from_bytes(hashlib.sha256(f"{url}|{n}".encode()).digest()[:2], "big")
    paragraphs = " ".join(
        f"{name} idea {n}.{k} explains one step of the subject in plain words."
        for k in range(1, 30)
    )
    return (
        f"## Why {name.lower()} matters ({n})\n\n{paragraphs}\n\n"
        f"## How it works ({n})\n\n{paragraphs} Detail {seed}.\n\n"
        f"## Common mistakes ({n})\n\n{paragraphs}"
    )


def default_reader(config: Settings = global_settings) -> MaterialReader | None:
    """The stub when generation runs on the stub model, Exa when keyed, else none."""
    if config.model_default == STUB_MODEL_ID:
        return StubMaterialReader()
    if config.exa_api_key:
        return ExaMaterialReader(config.exa_api_key)
    return None


def _host(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def _pasted_title(item: MaterialItem) -> str:
    if item.title and item.title.strip():
        return item.title.strip()[:200]
    first = next(
        (line.strip() for line in (item.text or "").splitlines() if line.strip()), ""
    )
    label = "AI summary" if item.kind is MaterialKind.AI_SUMMARY else "Pasted text"
    return first.lstrip("# ")[:80] or label


async def read_snapshot(
    items: Sequence[MaterialItem],
    mode: MaterialMode,
    reader: MaterialReader | None,
) -> MaterialSnapshot:
    """Read every item into sections, or raise with a reason per failed item."""
    failures: list[ItemFailure] = []
    materials: list[NewMaterial] = []
    total = 0
    for index, item in enumerate(items):
        if item.kind in LINK_KINDS:
            url = item.url or ""
            if needs_paste(url):
                failures.append(ItemFailure(index, MaterialFailure.PASTE_TRANSCRIPT))
                continue
            if reader is None:
                raise RetrievalUnavailableError("Reading links is not configured")
            result = await reader.read(url, crawl=item.kind is MaterialKind.DOCS)
            if isinstance(result, MaterialFailure):
                failures.append(ItemFailure(index, result))
                continue
            pages = [
                page for page in result if len(page.text.strip()) >= MIN_ITEM_CHARS
            ]
            if not pages:
                failures.append(ItemFailure(index, MaterialFailure.EMPTY))
                continue
            sections = (
                split_pages(pages)
                if item.kind is MaterialKind.DOCS
                else split_sections(pages[0].text, fallback_title=pages[0].title)
            )
            title = item.title or (
                f"{pages[0].title} ({len(pages)} pages)"
                if item.kind is MaterialKind.DOCS
                else pages[0].title
            )
        else:
            title = _pasted_title(item)
            sections = split_sections(item.text or "", fallback_title=title)
            if not sections:
                failures.append(ItemFailure(index, MaterialFailure.EMPTY))
                continue
        total += sum(len(section.text) for section in sections)
        if total > MAX_PATH_CHARS:
            failures.append(ItemFailure(index, MaterialFailure.TOO_LARGE))
            continue
        materials.append(
            NewMaterial(
                kind=item.kind.value,
                title=title[:300],
                url=item.url,
                sections=[NewSection(s.title[:300], s.text) for s in sections],
            )
        )
    if failures:
        logger.info(
            "material_unreadable",
            reasons=[f"{f.index}:{f.reason.value}" for f in failures],
        )
        raise MaterialUnreadableError(failures)
    return MaterialSnapshot(mode=mode, items=tuple(materials))


def outline_material(mode: str, sections: Sequence[SectionRow]) -> OutlineMaterial:
    """The outline's digest: whole sections when small, openings when large."""
    total = sum(len(section.text) for section in sections)
    whole = total <= OUTLINE_FULL_TEXT_CHARS
    size = teach_size(total)
    return OutlineMaterial(
        mode="teach" if mode == MaterialMode.TEACH else "start",
        sections=tuple(
            MaterialSectionDigest(
                id=section.position,
                material_title=section.material_title,
                title=section.title,
                chars=len(section.text),
                text=section.text if whole else section.text[:OUTLINE_EXCERPT_CHARS],
            )
            for section in sections
        ),
        target_lessons=size.lessons,
        target_units=size.units,
    )


def lesson_material(mode: str, sections: Sequence[SectionRow]) -> LessonMaterial | None:
    """A lesson's mapped sections in full, up to ``LESSON_MATERIAL_CHARS``."""
    passages: list[MaterialPassage] = []
    budget = LESSON_MATERIAL_CHARS
    for section in sections:
        if budget <= 0:
            break
        passages.append(
            MaterialPassage(
                section.material_title, section.title, section.text[:budget]
            )
        )
        budget -= len(section.text)
    if not passages:
        return None
    return LessonMaterial(
        mode="teach" if mode == MaterialMode.TEACH else "start",
        passages=tuple(passages),
    )


def failure_details(failures: Sequence[ItemFailure]) -> Mapping[str, object]:
    """The ``422`` envelope detail: one coded reason per failed item."""
    return {
        "message": "Some Material could not be read.",
        "reason": "material_unreadable",
        "items": [{"index": f.index, "reason": f.reason.value} for f in failures],
    }
