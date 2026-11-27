"""Papers as Material: arXiv ids and reading, digests, and concept order.

docs/tdds/papers-material.md D1, D2, D7, D8.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRetry

from aleph.agents.outline import (
    LessonOutline,
    OutlineCaps,
    OutlineDeps,
    PathOutline,
    UnitOutline,
    build_outline_agent,
    build_outline_prompt,
    validate_outline,
)
from aleph.agents.paper_digest import (
    DigestConcept,
    DigestPrerequisite,
    PaperDigest,
    build_digest_agent,
    build_digest_prompt,
)
from aleph.domains.material import (
    Concept,
    MaterialFailure,
    MaterialKind,
    MaterialMode,
    PlannedLesson,
    arxiv_id,
    concept_plan_problems,
    digest_problems,
)
from aleph.dtos.material import MaterialInput
from aleph.repositories.materials import SectionRow
from aleph.services.arxiv import ArxivReader, StubPaperReader, latexml_to_markdown
from aleph.services.material import (
    MaterialItem,
    MaterialUnreadableError,
    outline_material,
    paper_digest_inputs,
    paper_plan,
    read_snapshot,
)
from aleph.services.stub_model import build_stub_model

if TYPE_CHECKING:
    from aleph.agents._material import OutlineMaterial

# --- arXiv ids -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762v7", "1706.03762v7"),
        ("https://www.arxiv.org/pdf/2106.09685v2", "2106.09685v2"),
        ("https://arxiv.org/pdf/2106.09685.pdf", "2106.09685"),
        ("https://arxiv.org/html/2310.06825v1/", "2310.06825v1"),
        ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001"),
        # Not a paper link on an arXiv host: never fetched as a paper.
        ("https://arxiv.org.evil.example/abs/1706.03762", None),
        ("https://evil.example/abs/1706.03762", None),
        ("https://arxiv.org/abs/1706.03762/../../admin", None),
        ("https://arxiv.org/list/cs.CL/recent", None),
        ("ftp://arxiv.org/abs/1706.03762", None),
    ],
)
def test_arxiv_id_accepts_only_paper_links_on_arxiv(
    url: str, expected: str | None
) -> None:
    assert arxiv_id(url) == expected


def test_an_arxiv_item_must_be_an_arxiv_paper_link() -> None:
    MaterialInput(kind=MaterialKind.ARXIV, url="https://arxiv.org/abs/1706.03762")
    with pytest.raises(ValidationError):
        MaterialInput(kind=MaterialKind.ARXIV, url="https://example.com/abs/1706.03762")


# --- reading arXiv HTML --------------------------------------------------------

LATEXML = """
<html><body><article>
<h1 class="ltx_title ltx_title_document">Attention Is All You Need</h1>
<div class="ltx_authors">Ashish Vaswani, Noam Shazeer</div>
<div class="ltx_abstract"><h6>Abstract</h6><p>We propose the Transformer.</p></div>
<section><h2>3 Model Architecture</h2>
<p>Queries of dimension <math alttext="d_{k}"><mi>d</mi></math> are scaled.</p>
<table class="ltx_equation"><tr><td><math alttext="\\mathrm{softmax}(QK^{T})V">
<mi>x</mi></math></td><td>(1)</td></tr></table>
<figure class="ltx_figure"><img src="x1.png">
<figcaption>Figure 1: The Transformer.</figcaption></figure>
</section>
<section class="ltx_bibliography"><h2>References</h2><p>[1] Some paper.</p></section>
</article></body></html>
"""


def test_latexml_keeps_headings_and_math_and_drops_authors_and_references() -> None:
    title, markdown, share = latexml_to_markdown(LATEXML)
    assert title == "Attention Is All You Need"
    assert share == 0.0
    assert markdown.split("\n\n") == [
        "## Abstract",
        "We propose the Transformer.",
        "## 3 Model Architecture",
        "Queries of dimension $d_{k}$ are scaled.",
        "$$\\mathrm{softmax}(QK^{T})V$$",
        "[Figure 1: The Transformer.]",
    ]


@pytest.mark.anyio
async def test_arxiv_reader_asks_arxiv_for_the_id_and_maps_404_to_no_html() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "99999" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, text=LATEXML)

    reader = ArxivReader(transport=httpx.MockTransport(handler))
    paper = await reader.read("1706.03762v7")
    assert not isinstance(paper, MaterialFailure)
    assert paper.title == "Attention Is All You Need"
    assert await reader.read("2401.99999") is MaterialFailure.NO_HTML
    assert seen == [
        "https://arxiv.org/html/1706.03762v7",
        "https://arxiv.org/html/2401.99999",
    ]


@pytest.mark.anyio
async def test_a_paper_without_html_fails_its_item_and_stores_nothing() -> None:
    items = [
        MaterialItem(kind=MaterialKind.ARXIV, url="https://arxiv.org/abs/1706.03762"),
        MaterialItem(kind=MaterialKind.ARXIV, url="https://arxiv.org/abs/2401.99999"),
    ]
    with pytest.raises(MaterialUnreadableError) as raised:
        await read_snapshot(items, MaterialMode.TEACH, None, StubPaperReader())
    assert [(f.index, f.reason) for f in raised.value.failures] == [
        (1, MaterialFailure.NO_HTML)
    ]


@pytest.mark.anyio
async def test_a_paper_is_cut_into_sections_along_its_headings() -> None:
    items = [
        MaterialItem(kind=MaterialKind.ARXIV, url="https://arxiv.org/abs/1706.03762")
    ]
    snapshot = await read_snapshot(items, MaterialMode.TEACH, None, StubPaperReader())
    (paper,) = snapshot.items
    assert paper.kind == "arxiv"
    assert [s.title for s in paper.sections][:3] == [
        "Abstract",
        "1 Introduction",
        "2 Scaled Dot-Product Attention",
    ]


# --- digest rules ----------------------------------------------------------------


def concept(cid: str, *, sections: tuple[int, ...] = (1,), on: tuple[str, ...] = ()):
    return Concept(id=cid, name=cid.upper(), sections=sections, builds_on=on)


def test_a_digest_rejects_foreign_sections_unknown_parents_and_cycles() -> None:
    sections = frozenset({1, 2})
    assert digest_problems([concept("a"), concept("b", on=("a",))], sections) == []
    assert digest_problems([concept("a", sections=(3,))], sections) == [
        "a names sections not in the paper: [3]"
    ]
    assert digest_problems([concept("a", on=("z",))], sections) == [
        "a builds on unknown concepts: ['z']"
    ]
    assert digest_problems(
        [concept("a", on=("c",)), concept("b", on=("a",)), concept("c", on=("b",))],
        sections,
    ) == ["builds_on forms a cycle"]


# --- concept order and prep lessons ----------------------------------------------

CONCEPTS = [concept("a"), concept("b", on=("a",)), concept("c", on=("a", "b"))]
PREREQS = frozenset({"softmax"})


def lesson(title: str, *concepts: str, prep: str | None = None) -> PlannedLesson:
    return PlannedLesson(title, concepts, prep)


def test_concepts_taught_after_what_they_build_on_pass() -> None:
    plan = [
        lesson("Softmax", prep="Softmax"),
        lesson("A", "a"),
        lesson("B and C", "b", "c"),  # c needs b: same lesson is not too early
    ]
    # ``c`` needs ``b`` too; teaching both in one lesson is allowed.
    assert concept_plan_problems(plan, CONCEPTS, PREREQS, teach=True) == []


def test_a_concept_before_one_it_builds_on_is_rejected() -> None:
    plan = [lesson("B", "b"), lesson("A", "a"), lesson("C", "c")]
    assert concept_plan_problems(plan, CONCEPTS, PREREQS, teach=True) == [
        "b (B) is taught before a (A), which it builds on"
    ]


def test_teach_this_must_cover_every_concept_start_from_this_need_not() -> None:
    plan = [lesson("A", "a"), lesson("B", "b")]
    assert concept_plan_problems(plan, CONCEPTS, PREREQS, teach=True) == [
        "no lesson teaches these concepts: ['c']"
    ]
    assert concept_plan_problems(plan, CONCEPTS, PREREQS, teach=False) == []


def test_prep_lessons_come_first_name_a_known_prerequisite_and_are_capped() -> None:
    late = [lesson("A", "a"), lesson("Softmax", prep="softmax"), lesson("BC", "b", "c")]
    assert concept_plan_problems(late, CONCEPTS, PREREQS, teach=True) == [
        "prep lesson 'Softmax' comes after a lesson on the paper"
    ]
    unknown = [lesson("Calculus", prep="Calculus"), lesson("ABC", "a", "b", "c")]
    assert concept_plan_problems(unknown, CONCEPTS, PREREQS, teach=True) == [
        "'Calculus' teaches a prerequisite the digests do not list: 'Calculus'"
    ]
    many = [lesson(f"P{n}", prep="softmax") for n in range(5)] + [
        lesson("ABC", "a", "b", "c")
    ]
    assert concept_plan_problems(many, CONCEPTS, PREREQS, teach=True) == [
        "5 prep lessons; at most 4 are allowed"
    ]


# --- through the agents -----------------------------------------------------------


def paper_rows() -> list[SectionRow]:
    return [
        SectionRow(n, "Attention", f"Section {n}", "text " * 300, "arxiv", 1)
        for n in range(1, 4)
    ]


def paper_material(prerequisites: tuple[str, ...] = ()) -> OutlineMaterial:
    """A Teach this digest for one paper whose concept c2 builds on c1."""
    digest = PaperDigest(
        concepts=[
            DigestConcept(id="c1", name="Attention", sections=[1]),
            DigestConcept(id="c2", name="Heads", sections=[2], builds_on=["c1"]),
        ],
        prerequisites=[DigestPrerequisite(name=p, why="eq. 1") for p in prerequisites],
    )
    return replace(
        outline_material("teach", paper_rows()),
        papers=(paper_plan(1, "Attention", digest),),
    )


def test_the_outline_validator_rejects_paper_lessons_out_of_concept_order() -> None:
    material = paper_material()
    wrong = PathOutline(
        units=[
            UnitOutline(
                title="U",
                summary="S",
                lessons=[
                    LessonOutline(
                        title="Heads", material_sections=[2], concepts=["p1.c2"]
                    ),
                    LessonOutline(
                        title="Attn", material_sections=[1], concepts=["p1.c1"]
                    ),
                ],
            )
        ]
    )
    with pytest.raises(ModelRetry, match="taught before p1.c1"):
        validate_outline(OutlineCaps(), wrong, material)


def test_a_prep_lesson_needs_no_section_in_teach_this() -> None:
    material = paper_material(prerequisites=("Softmax",))
    outline = PathOutline(
        units=[
            UnitOutline(
                title="U",
                summary="S",
                lessons=[
                    LessonOutline(title="Softmax first", prerequisite="Softmax"),
                    LessonOutline(
                        title="Attn", material_sections=[1], concepts=["p1.c1"]
                    ),
                    LessonOutline(
                        title="Heads", material_sections=[2], concepts=["p1.c2"]
                    ),
                ],
            )
        ]
    )
    assert validate_outline(OutlineCaps(), outline, material) is outline


@pytest.mark.anyio
async def test_stub_digest_and_outline_teach_concepts_in_order() -> None:
    model = build_stub_model()
    (deps,) = paper_digest_inputs(paper_rows())
    digest = await build_digest_agent().run(
        build_digest_prompt(deps), deps=deps, model=model
    )
    material = replace(
        outline_material("teach", paper_rows()),
        papers=(paper_plan(1, deps.title, digest.output),),
    )
    run = await build_outline_agent().run(
        build_outline_prompt("Attention", material=material),
        deps=OutlineDeps(level="beginner", material=material),
        model=model,
    )
    assert isinstance(run.output, PathOutline)
    taught = [
        c for u in run.output.units for lesson in u.lessons for c in lesson.concepts
    ]
    assert taught == ["p1.c1", "p1.c2", "p1.c3"]
