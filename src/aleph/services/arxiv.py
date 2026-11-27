"""Reading an arXiv paper as Material (docs/tdds/papers-material.md D1, D2).

Aleph reads arXiv's own HTML rendering of a paper (LaTeXML), which keeps the
section structure and every equation as LaTeX in ``<math alttext>``. The
request is built from a validated paper id on ``arxiv.org`` only, never from
the learner's URL, and redirects are not followed, so this is the one bounded
exception to Linked Material's "Aleph never fetches a learner-supplied URL".
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from bs4 import BeautifulSoup, Tag

from aleph.domains.material import MaterialFailure
from aleph.services.retrieval import RetrievalUnavailableError

_ARXIV_HTML = "https://arxiv.org/html/{id}"
_TIMEOUT_SECONDS = 30.0
_MAX_HTML_BYTES = 15_000_000
# arXiv asks automated clients for no more than one request every 3 seconds.
_MIN_INTERVAL_SECONDS = 3.0
# More broken math than this means LaTeXML failed on the paper (D1).
_MAX_ERROR_SHARE = 0.05
USER_AGENT = "Aleph/1.0 (learning paths; https://aleph.mattjmcnaughton.com)"


@dataclass(frozen=True)
class Paper:
    """A paper's title, the URL actually read, and its text as Markdown."""

    title: str
    source_url: str
    markdown: str


class PaperReader(Protocol):
    """Reads one arXiv paper by id; raises ``RetrievalUnavailableError`` on outage."""

    async def read(self, paper_id: str) -> Paper | MaterialFailure: ...


def latexml_to_markdown(html: str) -> tuple[str, str, float]:
    """``(title, markdown, error share)`` for an arXiv LaTeXML page.

    Headings become ``#``-headings, inline math ``$…$``, display equations a
    ``$$…$$`` paragraph, and each figure or table one ``[Figure n: caption]``
    line. Authors, the bibliography and page chrome are dropped. The error
    share is the fraction of math nodes LaTeXML could not convert.
    """
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article") or soup.body or soup
    title_tag = article.find(class_="ltx_title_document")
    title = title_tag.get_text(" ", strip=True) if title_tag else ""
    for tag in article.select(
        "script, style, nav, footer, .ltx_authors, .ltx_bibliography, "
        ".ltx_page_footer, .ltx_dates, .ltx_role_footnote, .ltx_tag_note"
    ):
        tag.decompose()
    maths = article.find_all("math")
    errors = len(article.select(".ltx_ERROR"))
    share = errors / max(1, len(maths) + errors) if errors else 0.0

    for table in article.select("table.ltx_equation, table.ltx_equationgroup"):
        tex = " \\\\ ".join(_alttext(m) for m in table.find_all("math"))
        table.replace_with(_paragraph(soup, f"$${tex}$$"))
    for math in article.find_all("math"):
        tex = _alttext(math)
        math.replace_with(f"${tex}$" if tex else "")
    for figure in article.find_all("figure"):
        caption = figure.find("figcaption")
        text = caption.get_text(" ", strip=True) if caption else ""
        figure.replace_with(_paragraph(soup, f"[{text}]" if text else "[Figure]"))
    for abstract in article.select(".ltx_abstract"):
        heading = abstract.find(["h6", "h2", "h3"])
        if heading is not None:
            heading.name = "h2"

    blocks: list[str] = []
    for element in article.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        if element.name == "li" and element.find("p"):
            continue  # its paragraphs are emitted on their own
        if element.name == "p" and element.find_parent("li") and blocks:
            text = element.get_text(" ", strip=True)
            if text:
                blocks.append(f"- {text}")
            continue
        text = " ".join(element.get_text(" ", strip=True).split())
        if not text:
            continue
        if element.name == "h1":
            continue  # the title is the item's title, not a section
        if element.name in ("h2", "h3", "h4"):
            blocks.append("#" * int(element.name[1]) + " " + text)
        elif element.name == "li":
            blocks.append(f"- {text}")
        else:
            blocks.append(text)
    return title, "\n\n".join(blocks), share


def _alttext(math: Tag) -> str:
    value = math.get("alttext")
    return value.strip() if isinstance(value, str) else ""


def _paragraph(soup: BeautifulSoup, text: str) -> Tag:
    tag = soup.new_tag("p")
    tag.string = text
    return tag


class ArxivReader:
    """arXiv's HTML version of a paper, one request at least 3 seconds apart."""

    _lock = asyncio.Lock()
    _last_request = 0.0

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def read(self, paper_id: str) -> Paper | MaterialFailure:
        url = _ARXIV_HTML.format(id=paper_id)
        async with ArxivReader._lock:
            wait = ArxivReader._last_request + _MIN_INTERVAL_SECONDS - time.monotonic()
            if wait > 0 and self._transport is None:
                await asyncio.sleep(wait)
            ArxivReader._last_request = time.monotonic()
            try:
                async with httpx.AsyncClient(
                    timeout=_TIMEOUT_SECONDS,
                    transport=self._transport,
                    follow_redirects=False,
                    headers={"User-Agent": USER_AGENT},
                ) as client:
                    response = await client.get(url)
            except httpx.HTTPError as exc:
                raise RetrievalUnavailableError(f"arXiv failed: {exc}") from exc
        if response.status_code == 404:
            return MaterialFailure.NO_HTML
        if response.status_code >= 500:
            raise RetrievalUnavailableError(f"arXiv returned {response.status_code}")
        if response.status_code != 200 or len(response.content) > _MAX_HTML_BYTES:
            return MaterialFailure.UNREADABLE
        title, markdown, share = latexml_to_markdown(response.text)
        if share > _MAX_ERROR_SHARE:
            return MaterialFailure.NO_HTML
        return Paper(
            title=title or f"arXiv {paper_id}", source_url=url, markdown=markdown
        )


class StubPaperReader:
    """A fixed paper with sections and equations, for dev, CI and e2e.

    An id ending in ``99999`` has no HTML version, so that failure is testable.
    """

    async def read(self, paper_id: str) -> Paper | MaterialFailure:
        if paper_id.split("v")[0].endswith("99999"):
            return MaterialFailure.NO_HTML
        body = " ".join(
            f"The paper's argument, step {n}, explained in plain words."
            for n in range(40)
        )
        sections = [
            ("Abstract", f"We propose a model built on attention alone. {body}"),
            ("1 Introduction", f"Recurrent models process tokens in order. {body}"),
            (
                "2 Scaled Dot-Product Attention",
                "$$\\mathrm{Attention}(Q,K,V)=\\mathrm{softmax}(\\frac{QK^{T}}"
                f"{{\\sqrt{{d_{{k}}}}}})V$$\n\n{body}",
            ),
            ("3 Multi-Head Attention", f"Several heads attend in parallel. {body}"),
            ("4 Positional Encoding", f"Sinusoids give each position. {body}"),
        ]
        markdown = "\n\n".join(f"## {title}\n\n{text}" for title, text in sections)
        return Paper(
            title=f"Stub paper {paper_id}",
            source_url=_ARXIV_HTML.format(id=paper_id),
            markdown=markdown,
        )
