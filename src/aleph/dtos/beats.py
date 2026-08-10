"""Beats & Briefs API DTOs (Phase 6 TDD §6, AL-522, issue #172, epic #163).

The wire contract for every route on ``routers/v1/beats.py``: deploy, list,
the detail-plus-rail read, delete, retry (Beats), and the Brief read plus its
read ping.

``TzOffsetMinutes`` is imported directly into the router from
``dtos/progress.py`` — it is a query parameter on several routes here, never a
body field a DTO in this module needs to carry, so it is not re-imported into
this file either (§6: "one constrained alias, one place for the sign
convention to be wrong"). ``AnchorWeekday`` below is the **one** new alias
this ticket adds. ``TopicStr``/``GuidanceStr`` are imported from
``dtos/paths.py`` rather than redeclared: CONTEXT.md's Topic/Guidance are one
concept with one shape, deployed here exactly as frozen as a path's are.

Mapping from ORM rows to these models is always explicit construction in
``routers/v1/beats.py`` (the ``_progress_dto`` style, CLAUDE.md/§6) — never
``model_validate(from_attributes=True)``.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aleph.dtos.paths import GuidanceStr, TopicStr
from aleph.models import BeatResearchState, Level

__all__ = [
    "AnchorWeekday",
    "BeatDetailDTO",
    "BeatListResponse",
    "BeatSummaryDTO",
    "BriefDetailDTO",
    "BriefEntryDTO",
    "BuildsOnDTO",
    "DeployBeatRequest",
    "PublishedEntryDTO",
    "ReadPingMarker",
    "ReadPingRequest",
    "SkippedEntryDTO",
    "SourceDTO",
]

# CONTEXT.md: Anchor day — Python's Monday == 0 convention (TDD §4/§6). The
# one new alias this ticket adds.
AnchorWeekday = Annotated[int, Field(ge=0, le=6)]

# TDD §4.11/§6: the only Cadence this slice ships. A ``Literal``, not a free
# string, so the wire contract states the constraint rather than merely
# documenting it.
Cadence = Literal["weekly"]


class DeployBeatRequest(BaseModel):
    """``POST /api/v1/beats`` body (§6): deploy an analyst.

    ``topic``/``guidance`` reuse ``dtos/paths.py``'s ``TopicStr``/
    ``GuidanceStr`` — CONTEXT.md's Topic/Guidance are one concept whether a
    Path or a Beat is reading them, and a Beat's copies are frozen at
    deployment exactly as a path's are (no route ever writes them after
    creation — CONTEXT.md: Beat). ``model_research``/``model_brief`` are the
    admin-only per-Beat picker overrides (TDD D7/§5.3):
    ``routers/v1/paths.py::validate_model_override`` enforces them (``403``
    non-admin, ``422`` off-allowlist) before any billed work, the identical
    enforcement ``POST /paths`` already runs for its own two slots.
    """

    # ``model_research``/``model_brief`` start with the ``model_`` prefix
    # pydantic protects by default; the picker wire contract fixes these
    # names (parity with the ``MODEL_RESEARCH``/``MODEL_BRIEF`` config
    # slots, ``CreatePathRequest``'s own precedent), so opt out of the
    # protected namespace rather than rename.
    model_config = ConfigDict(protected_namespaces=())

    topic: TopicStr
    level: Level
    anchor_weekday: AnchorWeekday
    guidance: GuidanceStr | None = None
    model_research: str | None = None
    model_brief: str | None = None


class PublishedEntryDTO(BaseModel):
    """A published Brief's row in the Beat rail (TDD §6's `GET /beats/{id}`
    example).

    ``kind`` doubles as the discriminator (:data:`BriefEntryDTO`) and as the
    literal wire tag the frontend switches on — the ``CitationDTO`` shape in
    ``dtos/flashcards.py``, one entity over. Carries no ``skip_line`` field
    at all (not a nullable one): a published entry has nothing to skip.
    """

    kind: Literal["published"] = "published"
    id: UUID
    number: int
    published_on: date
    title: str
    read_at: datetime | None


class SkippedEntryDTO(BaseModel):
    """A Skipped period's row in the Beat rail (D2, CONTEXT.md: Skipped).

    ``number`` is carried explicitly as ``None`` — present and ``null`` on
    the wire, matching TDD §6's own example line (``"number": null``) —
    because a Skipped entry is still numbered *among* the rail's positions
    conceptually even though it has no Brief number of its own (D2's sparse
    numbering). Deliberately carries **no** ``title``/``read_at`` fields at
    all: a Skipped entry has no body to title and nothing to mark read
    (D11's two columns are Brief-wide, but the rail never invites a read
    ping on a row with nothing to open).
    """

    kind: Literal["skipped"] = "skipped"
    id: UUID
    number: None = None
    published_on: date
    skip_line: str


# The discriminated union (§6: "entries is ONE list of both kinds, never two
# arrays"). Pydantic dispatches on ``kind`` at validation time, and — the
# point of choosing this over a single flat/nullable model — a
# ``SkippedEntryDTO`` instance genuinely has no ``title``/``read_at``
# attributes to accidentally serialize, matching TDD §6's example exactly
# (the skipped row carries no ``title``/``read_at`` keys at all, and the
# published row carries no ``skip_line``).
BriefEntryDTO = Annotated[
    PublishedEntryDTO | SkippedEntryDTO, Field(discriminator="kind")
]


class BeatDetailDTO(BaseModel):
    """``GET /api/v1/beats/{id}`` body (§6) — the poll target and the rail.

    ``research_state`` is ``idle | researching | failed | refused`` (D3).
    ``refusal_message`` is non-null only when ``research_state == refused``
    (the ``PathDetailResponse.refusal_message`` precedent, one phase over).
    ``entries`` is newest first, **never locked** (PRD §3) — every entry in
    the list is always fully rendered, unlike a path's lesson list.
    """

    id: UUID
    topic: str
    level: Level
    guidance: str | None
    anchor_weekday: AnchorWeekday
    cadence: Cadence
    research_state: BeatResearchState
    research_started_at: datetime | None
    refusal_message: str | None
    entries: list[BriefEntryDTO]


class BeatSummaryDTO(BaseModel):
    """One row of ``GET /api/v1/beats`` (§6: "the learner's Beats with unread
    counts and research state").

    ``unread_count`` counts entries of **either** kind with ``read_at IS
    NULL`` for this Beat (``BriefRepository.unread_counts_by_beat``) — the
    figure behind the home card's "3 new briefs" copy (§8); it does not
    itself distinguish which entries are unread, only how many.
    """

    id: UUID
    topic: str
    level: Level
    anchor_weekday: AnchorWeekday
    cadence: Cadence
    research_state: BeatResearchState
    research_started_at: datetime | None
    refusal_message: str | None
    unread_count: int


class BeatListResponse(BaseModel):
    """``GET /api/v1/beats`` body: the learner's Beats.

    Wrapped in an object (never a bare top-level array), the
    ``PathListResponse`` precedent, so the payload can grow fields without a
    breaking shape change.
    """

    beats: list[BeatSummaryDTO]


class SourceDTO(BaseModel):
    """One Source in a Brief's Sources block (§6's `GET /briefs/{id}` example,
    CONTEXT.md: Source).

    Every field is metadata a service joined from the retriever's own
    ``RetrievedDocument`` at persist time (TDD §5.5: "a Source's metadata is
    never model-written") — this DTO only carries it to the wire, unchanged.
    """

    position: int
    publisher: str
    title: str
    published_on: date
    url: str


class BuildsOnDTO(BaseModel):
    """The `Builds on Brief #N` line's data (CONTEXT.md: Brief continuity).

    ``None`` on the parent ``BriefDetailDTO`` for Brief #1 and for every
    Skipped entry (D1: "no `builds_on_brief_id`" — this is a derived
    ``number < :n`` read, never a stored edge).
    """

    id: UUID
    number: int
    published_on: date


class BriefDetailDTO(BaseModel):
    """``GET /api/v1/briefs/{id}`` body (§6): body Markdown, Sources,
    `builds_on`.

    ``number``/``title``/``body_markdown`` are nullable — not because the
    common case (a published Brief, TDD §6's own example) ever leaves them
    unset, but because this same route also resolves a Skipped entry's id
    (its rail row links nowhere in the shipped frontend, §8, but the API
    itself draws no such line): a Skipped row's own ``CHECK`` constraint
    (D2/§4) guarantees exactly these three columns are ``NULL`` at the
    storage layer, and this DTO mirrors that truthfully rather than
    fabricating placeholder text.
    """

    id: UUID
    beat_id: UUID
    number: int | None
    published_on: date
    title: str | None
    body_markdown: str | None
    builds_on: BuildsOnDTO | None
    sources: list[SourceDTO]


# ``POST /api/v1/briefs/{id}/read`` body marker (D11, §6, §9's `marker`
# field). A ``Literal``, so an unrecognized marker is a ``422
# validation_error`` before the route body ever runs, not a string the
# service has to defensively branch on.
ReadPingMarker = Literal["opened", "sources"]


class ReadPingRequest(BaseModel):
    """``POST /api/v1/briefs/{id}/read`` body (D11): which ping fired.

    ``opened`` and ``sources`` are independent first-write-wins columns
    (``read_at``/``sources_seen_at``) — this DTO carries exactly one marker
    per call, matching the frontend's own two separate call sites (open, and
    the Sources block's `IntersectionObserver`, §8).
    """

    marker: ReadPingMarker
