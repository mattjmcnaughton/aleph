"""Beats & Briefs API (AL-522, issue #172, epic #163, Phase 6 TDD §6).

Session-cookie protected (``get_current_user`` -> ``401`` through the shared
envelope). All addressing is by UUID; another learner's Beat or Brief reads as
``404`` (never ``403`` — its existence is not disclosed, TDD §6), resolved
once by the shared ``OwnedBeat``/``OwnedBrief`` dependencies, the
``OwnedPath``/``OwnedLessonForDrafts`` precedent.

**The flag gate.** Every route here hangs off ``require_analyst_enabled``,
mounted **router-level** (TDD D12) so a future route added to this file
inherits the gate by construction — the same posture ``tutor``/``shaping``/
``streaks``/``flashcards`` all took. Off -> ``404`` for every route, before
any work: ``get_current_user`` runs first (an anonymous request is ``401``
before the flag is ever consulted), and the flag check itself does no I/O
beyond resolving the caller's flags — no repository read, no drain, no spawn.

**Trigger + poll (D15), with one wrinkle `POST /paths` does not have.**
``POST /beats`` deploys AND claims the first run in the same request (PRD
§3: "researched immediately, not at the first Anchor day") by calling the
SAME arrival drain the two ``GET`` routes use — a fresh Beat's cadence
(``domains/cadence.py::is_claimable(None, ...)``) is unconditionally true
(D4), so no separate claim path is needed. ``POST /beats/{id}/retry`` is the
one true fire-and-forget trigger (``BriefingService.trigger_retry``,
mirroring ``GenerationOrchestrator.trigger_outline_retry``): its response is
built from state read *before* the retry is even spawned, since — unlike
deploy — nothing in this ticket's acceptance criteria requires the retry
response to reflect the claim synchronously, and the client already polls
``GET /beats/{id}`` for the outcome.

**The arrival drain (D15, §5.6, §7) — read, THEN drain, in that order.**
Both ``GET`` routes build their response from a read taken *before* calling
``BriefingService.drain_claimable``, mirroring TDD §3's architecture
diagram verbatim (``load_beats(...)`` precedes ``drain_claimable(...)``).
This is not a stylistic choice: it is what makes §7's defence of a
``GET``-with-a-side-effect literally true by construction — "a `GET` that
returns the same body whether or not it triggered work" — because the
response object already exists, fully built, before the drain's claim (which
runs in its own short transaction, TDD §5.6's FIX C) can possibly touch the
row this response was built from. A second, freshly-issued `GET` on the same
Beat right after would of course observe the drain's effect — the DB state
really did change — but *this* request's own response never does.

**The daily research cap never 429s a `GET` or the retry route.** TDD §7:
"the research cap is checked inside the drain, before the claim — never at
the route" (``BriefingService.drain_claimable`` calls
``DailyRateLimiter.brief_research_capacity_available``, which is
non-raising). ``POST /beats`` carries its **own**, different cap — the
**stock** Beat-count cap (``check_beat_creation``) — checked explicitly here,
before creation, and it is the *only* ``429`` this router's routes can ever
raise.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves route-param annotations.

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings
from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.beats import (
    BeatDetailDTO,
    BeatListResponse,
    BeatSummaryDTO,
    BriefDetailDTO,
    BriefEntryDTO,
    BuildsOnDTO,
    DeployBeatRequest,
    PublishedEntryDTO,
    ReadPingRequest,
    SkippedEntryDTO,
    SourceDTO,
)
from aleph.dtos.progress import TzOffsetMinutes  # noqa: TC001 - FastAPI resolves it.
from aleph.models import (  # noqa: TC001 - FastAPI resolves annotations.
    Beat,
    Brief,
    BriefKind,
    BriefSource,
    User,
)
from aleph.repositories import BeatRepository, BriefRepository
from aleph.routers.v1.paths import validate_model_override
from aleph.services.briefing import briefing_service
from aleph.services.feature_flags import FeatureFlag, FeatureFlagService
from aleph.services.rate_limit import build_daily_rate_limiter

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]

# TDD §4.11/§6: the only Cadence this slice ships.
_CADENCE = "weekly"


def _not_found() -> HTTPException:
    """A ``404`` rendered through the shared envelope as ``not_found``."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


async def require_analyst_enabled(user: CurrentUser, session: Session) -> None:
    """Hide the entire analyst surface unless the ``analyst`` flag resolves on.

    Mounted as a **router-level** dependency (see the module docstring), so
    every route in this file — present and future — inherits the gate by
    construction (TDD D12's whole point). Off (dark-by-default, the
    ``tutor``/``shaping``/``streaks``/``flashcards`` posture) -> ``404`` for
    every route, before any work — ``get_current_user`` runs first, so an
    anonymous request is already ``401`` before the flag is ever consulted.
    """
    flags = await FeatureFlagService(session).resolve_for_user(user)
    if not flags.get(FeatureFlag.ANALYST, False):
        raise _not_found()


async def _owned_beat(beat_id: UUID, user: CurrentUser, session: Session) -> Beat:
    """Resolve ``beat_id`` only if it belongs to the caller, else ``404``."""
    beat = await BeatRepository(session).get_for_user(beat_id=beat_id, user_id=user.id)
    if beat is None:
        raise _not_found()
    return beat


OwnedBeat = Annotated[Beat, Depends(_owned_beat)]


async def _owned_brief(brief_id: UUID, user: CurrentUser, session: Session) -> Brief:
    """Resolve ``brief_id`` only if its Beat belongs to the caller, else ``404``."""
    brief = await BriefRepository(session).get_for_user(
        brief_id=brief_id, user_id=user.id
    )
    if brief is None:
        raise _not_found()
    return brief


OwnedBrief = Annotated[Brief, Depends(_owned_brief)]


router = APIRouter(
    prefix="/api/v1",
    tags=["beats"],
    dependencies=[Depends(require_analyst_enabled)],
)


# --------------------------------------------------------------------------- #
# Wire mapping — explicit construction throughout (``_progress_dto``'s style,
# CLAUDE.md/§6), never ``model_validate(from_attributes=True)``.
# --------------------------------------------------------------------------- #


def _entry_dto(brief: Brief) -> BriefEntryDTO:
    """One rail row, dispatched on ``kind`` (D2, §6's discriminated shape)."""
    if brief.kind is BriefKind.PUBLISHED:
        assert brief.number is not None, "a published Brief always has a number"
        assert brief.title is not None, "a published Brief always has a title"
        return PublishedEntryDTO(
            id=brief.id,
            number=brief.number,
            published_on=brief.published_on,
            title=brief.title,
            read_at=brief.read_at,
        )
    assert brief.skip_line is not None, "a Skipped entry always has a skip_line"
    return SkippedEntryDTO(
        id=brief.id, published_on=brief.published_on, skip_line=brief.skip_line
    )


def _beat_detail_dto(beat: Beat, entries: list[Brief]) -> BeatDetailDTO:
    return BeatDetailDTO(
        id=beat.id,
        topic=beat.topic,
        level=beat.level,
        guidance=beat.guidance,
        anchor_weekday=beat.anchor_weekday,
        cadence=_CADENCE,
        research_state=beat.research_state,
        research_started_at=beat.research_started_at,
        refusal_message=beat.refusal_message,
        entries=[_entry_dto(entry) for entry in entries],
    )


def _beat_summary_dto(beat: Beat, *, unread_count: int) -> BeatSummaryDTO:
    return BeatSummaryDTO(
        id=beat.id,
        topic=beat.topic,
        level=beat.level,
        anchor_weekday=beat.anchor_weekday,
        cadence=_CADENCE,
        research_state=beat.research_state,
        research_started_at=beat.research_started_at,
        refusal_message=beat.refusal_message,
        unread_count=unread_count,
    )


def _source_dto(source: BriefSource) -> SourceDTO:
    return SourceDTO(
        position=source.position,
        publisher=source.publisher,
        title=source.title,
        published_on=source.published_on,
        url=source.url,
    )


async def _drain(session: Session, user: User, tz_offset_minutes: int) -> None:
    """The arrival trigger (D15) — see the module docstring's ordering note.

    Always called *after* the caller has already built its response from a
    prior read, never before: that ordering is what makes §7's "a `GET`
    returns the same body whether or not it triggered work" true for the
    request that triggers it, not merely for a later one that observes the
    result.
    """
    await briefing_service.drain_claimable(
        session,
        user_id=user.id,
        tz_offset_minutes=tz_offset_minutes,
        is_admin=is_admin(user, settings),
    )


def _local_today(tz_offset_minutes: int) -> date:
    """The arrival's local day (D5) — one line, duplicated deliberately.

    The same arithmetic ``services/briefing.py::_local_today`` and
    ``services/progress_read.py`` each already carry their own copy of (see
    ``services/briefing.py``'s own docstring: "``services/progress_read.py``'s
    exact arithmetic, reused rather than re-derived"). There is no shared,
    importable module beneath both a service and this router to hang one
    copy from, so this is this router's own copy of one line — needed only
    by ``POST /beats/{id}/retry``, which does not go through
    ``BriefingService.drain_claimable`` (that method derives its own
    ``local_today`` internally from the same ``tz_offset_minutes``).
    """
    return (datetime.now(UTC) - timedelta(minutes=tz_offset_minutes)).date()


# --------------------------------------------------------------------------- #
# Beats
# --------------------------------------------------------------------------- #


@router.post("/beats", status_code=status.HTTP_202_ACCEPTED)
async def deploy_beat(
    body: DeployBeatRequest,
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
) -> BeatDetailDTO:
    """Deploy an analyst -> ``202``, first run already claimed (PRD §3, §6;
    D15's trigger + poll, verbatim — the same status Phase 1's `POST /paths`
    uses for the identical "the row exists, the work is still in flight"
    shape).

    Model overrides are enforced **first** — admin-only (``403``),
    allowlist-bound (``422``) — the identical ``validate_model_override``
    ``POST /paths`` already runs for its own two slots, so an unauthorized or
    off-allowlist request is rejected before any quota is spent. The **stock**
    Beat cap (``check_beat_creation``, ``MAX_BEATS_PER_LEARNER``, admins
    exempt) is then checked before the row is created — the only ``429``
    this router raises (TDD §7: the daily research cap never does).

    On pass the Beat is created and committed, then the SAME arrival drain
    the two ``GET`` routes use (``BriefingService.drain_claimable``) claims
    and spawns its first research run — a fresh Beat's cadence is
    unconditionally due (D4: "a Beat with no entries is claimable
    immediately"), so no separate claim path exists or is needed. The
    research itself is spawned, never awaited: this route returns as soon as
    the claim (a fast, atomic ``UPDATE``) resolves, and the client polls
    ``GET /beats/{id}`` for the Brief. ``session.refresh`` picks up the
    claim's write — it landed on the drain's own short transaction (TDD
    §5.6's FIX C), not this request's session, so without it the response
    would echo the stale, pre-claim ``research_state`` straight back.
    """
    admin = is_admin(user, settings)
    model_research = validate_model_override(
        body.model_research, is_admin=admin, allowed=settings.allowlist_ids
    )
    model_brief = validate_model_override(
        body.model_brief, is_admin=admin, allowed=settings.allowlist_ids
    )

    limiter = build_daily_rate_limiter(session)
    await limiter.check_beat_creation(user_id=user.id, is_admin=admin)

    beat = await BeatRepository(session).create(
        user_id=user.id,
        topic=body.topic,
        level=body.level,
        anchor_weekday=body.anchor_weekday,
        guidance=body.guidance,
        model_research=model_research,
        model_brief=model_brief,
    )
    await session.commit()

    await _drain(session, user, tz_offset_minutes)
    await session.refresh(beat)

    return _beat_detail_dto(beat, entries=[])


@router.get("/beats")
async def list_beats(
    user: CurrentUser, session: Session, tz_offset_minutes: TzOffsetMinutes = 0
) -> BeatListResponse:
    """The learner's Beats, newest first, with unread counts (§6). Drains.

    Read, THEN drain (see the module docstring): the response below is fully
    built from ``beats``/``unread`` before ``_drain`` ever runs, so this
    request's own arrival-triggered claim (if any) cannot change what it
    returns.
    """
    beats = await BeatRepository(session).list_for_user(user_id=user.id)
    unread = await BriefRepository(session).unread_counts_by_beat(
        [beat.id for beat in beats]
    )
    response = BeatListResponse(
        beats=[
            _beat_summary_dto(beat, unread_count=unread.get(beat.id, 0))
            # ``list_for_user`` orders oldest-first (the drain's own shape,
            # ``repositories/beats.py``'s docstring) — reversed here for
            # display, the "Your paths" switcher's newest-first convention
            # (TDD §8: the analyst surfaces deliberately rhyme with paths').
            for beat in reversed(beats)
        ]
    )

    await _drain(session, user, tz_offset_minutes)
    return response


@router.get("/beats/{beat_id}")
async def get_beat(
    beat: OwnedBeat,
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
) -> BeatDetailDTO:
    """One Beat: standing orders, research state, the rail (§6). Drains.

    Ownership via ``OwnedBeat`` (``404`` otherwise). Read, THEN drain (see
    the module docstring) — ``response`` is built from ``beat``/``entries``
    before ``_drain`` runs, so this request's own arrival-triggered claim
    cannot change what it returns.
    """
    entries = await BriefRepository(session).list_for_beat(beat.id)
    response = _beat_detail_dto(beat, entries)

    await _drain(session, user, tz_offset_minutes)
    return response


@router.delete("/beats/{beat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_beat(beat: OwnedBeat, session: Session) -> Response:
    """Hard-delete a Beat; cascades to its Briefs and Sources (§4, §6).

    Ownership via ``OwnedBeat`` (``404`` otherwise). This is also how
    standing orders change (CONTEXT.md: Beat — delete and redeploy, PRD
    §4.11). Not undoable (the UI confirms). No drain: deleting is not a read
    the learner is waiting on an answer from.
    """
    await BeatRepository(session).delete(beat.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/beats/{beat_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_beat(
    beat: OwnedBeat, session: Session, tz_offset_minutes: TzOffsetMinutes = 0
) -> BeatDetailDTO:
    """Re-claim a ``failed`` run -> ``202`` (D3; trigger + poll; §6).

    Ownership via ``OwnedBeat`` (``404`` otherwise). The **only** route that
    re-claims a real ``failed`` run (``BriefingService.trigger_retry`` uses
    the retry predicate, D3) — an ordinary arrival never does, so a
    retrieval outage never silently bills a fresh run on every page load.
    Fire-and-forget: the claim and the pipeline both run inside the spawned
    task (mirrors ``POST /paths/{id}/retry``'s own ``trigger_outline_retry``
    shape), so this route's response is built from the row as read *before*
    the retry is spawned — the client polls ``GET /beats/{id}`` for the
    outcome, exactly as a poll-as-trigger route. Carries **no** rate limit
    (TDD §7: never at the route) and **no** drain (this is an explicit,
    single-Beat action, not an arrival read).
    """
    entries = await BriefRepository(session).list_for_beat(beat.id)
    local_today = _local_today(tz_offset_minutes)
    briefing_service.trigger_retry(beat.id, local_today)
    return _beat_detail_dto(beat, entries)


# --------------------------------------------------------------------------- #
# Briefs
# --------------------------------------------------------------------------- #


@router.get("/briefs/{brief_id}")
async def get_brief(brief: OwnedBrief, session: Session) -> BriefDetailDTO:
    """A Brief: body Markdown, Sources, ``builds_on`` (§6).

    Ownership via ``OwnedBrief`` (``404`` otherwise, walking Brief -> Beat ->
    user — TDD §6). ``builds_on`` resolves only for a **published** Brief
    (``BriefRepository.previous_published``, the highest-numbered published
    Brief strictly below this one's ``number``) — ``None`` on Brief #1 (no
    such row exists) and, by construction here (never queried at all), on
    every Skipped entry, which has no ``number`` of its own to search below.
    ``sources`` is ``[]`` for a Skipped entry (no ``brief_sources`` rows
    exist for one, D2).
    """
    sources = await BriefRepository(session).sources_for_brief(brief.id)
    builds_on: BuildsOnDTO | None = None
    if brief.kind is BriefKind.PUBLISHED:
        assert brief.number is not None, "a published Brief always has a number"
        previous = await BriefRepository(session).previous_published(
            beat_id=brief.beat_id, number=brief.number
        )
        if previous is not None:
            assert previous.number is not None
            builds_on = BuildsOnDTO(
                id=previous.id,
                number=previous.number,
                published_on=previous.published_on,
            )
    return BriefDetailDTO(
        id=brief.id,
        beat_id=brief.beat_id,
        number=brief.number,
        published_on=brief.published_on,
        title=brief.title,
        body_markdown=brief.body_markdown,
        builds_on=builds_on,
        sources=[_source_dto(source) for source in sources],
    )


@router.post("/briefs/{brief_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_brief(
    brief: OwnedBrief, body: ReadPingRequest, session: Session
) -> Response:
    """Ping a Brief read -> ``204`` (D11, §6/§9).

    Ownership via ``OwnedBrief`` (``404`` otherwise). ``opened`` and
    ``sources`` are independent, first-write-wins columns
    (``mark_read``/``mark_sources_seen``) — idempotent per marker: a repeat
    ping with the same ``marker`` is still ``204`` and never moves the
    timestamp (the north-star metric, §9, asks *when a learner first opened*
    a Brief). An unrecognized ``marker`` is a ``422 validation_error`` before
    this body ever runs (``ReadPingMarker``'s ``Literal``).
    """
    briefs = BriefRepository(session)
    if body.marker == "opened":
        await briefs.mark_read(brief.id)
    else:
        await briefs.mark_sources_seen(brief.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
