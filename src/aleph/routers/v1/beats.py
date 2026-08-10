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
one explicit retry trigger (``BriefingService.trigger_retry``, mirroring
``GenerationOrchestrator.trigger_outline_retry``'s split of claim from
pipeline): its response is built from a re-read taken *after* the claim
half of ``trigger_retry`` is awaited (code-review FIX 9), the identical
drain-then-refresh shape the two ``GET`` routes already use — only the
pipeline itself (retrieval + the two model calls) is spawned and left for
the client's poll of ``GET /beats/{id}`` to observe finishing.

**The arrival drain (D15, §5.6, §7) — DRAIN, THEN re-read, in that order**
(code-review FIX 1, correcting this router's original ordering). Both
``GET`` routes now call ``BriefingService.drain_claimable`` **first** and
build their response from a read taken *after* it, the opposite of an
earlier version of this file (and the opposite of TDD §3's architecture
diagram, which shows ``load_beats(...)`` preceding ``drain_claimable(...)``
— see the correction below). A GET built from a pre-drain read is stale in
exactly the case that matters most: a Beat's pre-run state (``idle``) is
*also* its post-success state, and ``lib/polling.ts`` stops polling the
instant it sees any terminal state — ``idle`` included. A learner who opens
`/beats/{id}` for the first time (a deep link, a PWA restore, a refresh) would
see the response claim ``idle`` while the drain it just triggered committed
``researching`` underneath it: polling never starts, ``state-card.tsx`` never
renders "Researching…", and the Brief lands with nothing on screen — verbatim
the defect AL-521's own FIX 1 eliminated one layer down, reintroduced here by
reading before draining instead of after.

**Resolving the conflict with TDD §7 explicitly, since it is what the old
ordering was defending.** §7's "a `GET` that returns the same body whether or
not it triggered work" is a *rhetorical* defence of a GET having a side
effect at all — nothing downstream depends on this response being
byte-identical to a hypothetical no-drain response, and reading after the
drain is still fully deterministic, it just returns the newer, truer state
of the very row this request's own arrival just changed. §7's other,
load-bearing property — "nothing polls the beats list… a Beat that starts
researching does so because this learner's own arrival triggered it, so the
client already knows" — actually *requires* the response to reflect the
trigger; it cannot hold if the response still says ``idle``. TDD §8's home
card copy ("Researching… · started 30s ago") is unreachable from a Beat's own
detail poll under the old ordering, for the same reason.

``get_beat`` re-reads by ``session.refresh(beat)`` — the ``OwnedBeat``
dependency resolved (and cached in this session's identity map) *before* the
drain runs, so without an explicit refresh it would keep echoing the
pre-drain row exactly as ``deploy_beat`` already has to guard against below
(``db.py``'s ``expire_on_commit=False`` is what makes the refresh
necessary rather than automatic — the same note ``services/briefing.py``'s
own docstring makes about a caller-held identity going stale mid-handler).
``list_beats`` re-reads via a fresh ``list_for_user`` query, which needs no
explicit refresh: those rows were never in this session's identity map
before this call. Note the internal inconsistency this fix removes:
``deploy_beat`` already drained-then-refreshed-then-returned ``researching``;
the two ``GET`` routes were the only ones still returning the opposite
semantics for the identical DTO shape.

**The daily research cap never 429s a `GET`; it now can 429 the retry route**
(code-review FIX 2, correcting TDD §7's blanket "never at the route" and the
in-code claim that used to sit here). §7's own reason for "never at the
route" is that the drain's claim is "a side effect of a read the learner did
not explicitly ask to be billed for" — that reasoning is specific to a `GET`
the learner did not ask to trigger research, and does not extend to
``POST /beats/{id}/retry``, an *explicit* billed trigger the learner asked
for by name, on the `POST /paths/{id}/retry` precedent
(``routers/v1/paths.py::retry_path``, which checks
``check_outline_generation`` before triggering for exactly this reason).
``BriefingService.drain_claimable`` still calls the drain's own,
**non-raising** ``DailyRateLimiter.brief_research_capacity_available`` inside
every claim it makes on a `GET`; ``retry_beat`` below calls a **raising**
check of its own, ``DailyRateLimiter.check_brief_research_retry``, and only
when the retry is about to do real work (see ``retry_beat``'s own docstring
for why a non-``failed`` Beat never reaches that check at all). ``POST
/beats`` carries a third, different cap — the **stock** Beat-count cap
(``check_beat_creation``) — checked explicitly there, before creation. Three
caps, three different shapes, on purpose: a stock cap on creation, a
non-raising degrade on the arrival read, a raising cap on the one explicit
billed trigger.
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

from aleph import events
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
    BeatResearchState,
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

    Called *before* the caller builds its response (code-review FIX 1),
    never after: that ordering is what makes the response reflect the claim
    this very request's own arrival triggered, so a first-ever poll sees
    ``researching`` rather than a stale, terminal-looking ``idle``.
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
    exempt) is then checked before the row is created. This is this route's
    only ``429`` (the daily research cap never raises here — TDD §7 — it can
    only ever 429 ``POST /beats/{id}/retry``, code-review FIX 2, see that
    route's own docstring).

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

    # AL-540: the Beat is created, committed, and its first run already
    # claimed and spawned by the drain above — Beat survival's own
    # denominator and the deployment-mix datum (TDD §9).
    events.emit_beat_deployed(
        account_id=user.id,
        beat_id=beat.id,
        beat_level=beat.level.value,
        anchor_weekday=beat.anchor_weekday,
        has_guidance=beat.guidance is not None,
    )

    return _beat_detail_dto(beat, entries=[])


@router.get("/beats")
async def list_beats(
    user: CurrentUser, session: Session, tz_offset_minutes: TzOffsetMinutes = 0
) -> BeatListResponse:
    """The learner's Beats, newest first, with unread counts (§6). Drains.

    Drain, THEN re-read (code-review FIX 1, see the module docstring): the
    response below is built from ``beats``/``unread`` read *after* ``_drain``
    has run, so this request's own arrival-triggered claim (if any) is
    already reflected in what it returns — a freshly claimed Beat reads
    ``research_state: "researching"`` in this very response, not a stale
    ``idle`` a first poll would treat as terminal.
    """
    await _drain(session, user, tz_offset_minutes)

    beats = await BeatRepository(session).list_for_user(user_id=user.id)
    unread = await BriefRepository(session).unread_counts_by_beat(
        [beat.id for beat in beats]
    )
    return BeatListResponse(
        beats=[
            _beat_summary_dto(beat, unread_count=unread.get(beat.id, 0))
            # ``list_for_user`` orders oldest-first (the drain's own shape,
            # ``repositories/beats.py``'s docstring) — reversed here for
            # display, the "Your paths" switcher's newest-first convention
            # (TDD §8: the analyst surfaces deliberately rhyme with paths').
            for beat in reversed(beats)
        ]
    )


@router.get("/beats/{beat_id}")
async def get_beat(
    beat: OwnedBeat,
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
) -> BeatDetailDTO:
    """One Beat: standing orders, research state, the rail (§6). Drains.

    Ownership via ``OwnedBeat`` (``404`` otherwise). Drain, THEN re-read
    (code-review FIX 1, see the module docstring): ``beat`` was resolved by
    the ``OwnedBeat`` dependency *before* the drain runs, so it is already
    sitting in this session's identity map — ``session.refresh`` is what
    picks up the claim's write (it landed on the drain's own short
    transaction, TDD §5.6's FIX C, not this request's session), exactly the
    guard ``deploy_beat`` above already needs for the identical reason.
    Without it this route would keep echoing the stale, pre-drain
    ``research_state`` regardless of what the drain just committed.
    """
    await _drain(session, user, tz_offset_minutes)
    await session.refresh(beat)

    entries = await BriefRepository(session).list_for_beat(beat.id)
    return _beat_detail_dto(beat, entries)


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
    beat: OwnedBeat,
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
) -> BeatDetailDTO:
    """Re-claim a ``failed`` run -> ``202`` (D3; trigger + poll; §6).

    Ownership via ``OwnedBeat`` (``404`` otherwise). **Real work only when
    the Beat is actually ``failed`` (code-review FIX 2b).** The repository's
    own retry predicate (``_RETRY_CLAIMABLE_STATES = (idle, failed)``,
    ``repositories/beats.py`` D3) stays faithful to the TDD, which is right
    at that layer — but the product rule the *route* must enforce is
    narrower: unlike a path, whose auto-claimable ``pending`` is a
    pre-run, non-terminal state (so a stray retry is a genuine no-op),
    ``idle`` is a Beat's **healthy steady state** — the very row a fresh
    arrival will claim on its own next Anchor day. Letting a stray retry win
    that claim would drive a full billed research run and publish an
    off-cadence Brief that then resets D4's cadence floor early, for no
    learner-visible reason. So on any state other than ``failed`` — ``idle``,
    ``researching`` (already in flight), ``refused`` (terminal, PRD §2's
    safety branch) — this route is a **genuine no-op**: no cap check, no
    claim, no spawn, no billing, just the Beat and its rail as they already
    stand. ``202`` either way (the ``POST /paths/{id}/retry`` precedent: a
    stray retry there is *also* a silent no-op that still answers ``202``,
    since the response is a status snapshot for the poller either way, never
    an assertion that new work started).

    **The daily research cap (code-review FIX 2a), checked only on the real
    path.** TDD §7's "never at the route" is the arrival drain's rule, and
    its own reasoning — "the drain is a side effect of a read the learner did
    not explicitly ask to be billed for" — is precisely backwards for this
    route: an explicit ``POST`` *is* billing the learner asked for, the
    ``POST /paths/{id}/retry`` shape (``check_outline_generation``, called
    before ``trigger_outline_retry``). ``check_brief_research_retry`` mirrors
    that: called right here, before the claim, only once the Beat is known
    to be ``failed`` — a breach must not cost a no-op its own quota unit.

    On the real path: the claim half of ``trigger_retry`` is awaited before
    this route builds its response (code-review FIX 9), so ``session.refresh``
    below picks up the claim's write exactly as ``deploy_beat``/``get_beat``
    already refresh after their own drain — only the pipeline itself
    (retrieval + the two model calls) is left running in the background for
    the client's poll of ``GET /beats/{id}`` to observe. No drain of its own
    either way (this is an explicit, single-Beat action, not an arrival
    read).
    """
    if beat.research_state is not BeatResearchState.FAILED:
        # Not a real failure to retry: no cap check, no claim, no spawn, no
        # billing — see the docstring's FIX 2b note for why every other
        # state (idle above all) must be a genuine no-op here.
        entries = await BriefRepository(session).list_for_beat(beat.id)
        return _beat_detail_dto(beat, entries)

    limiter = build_daily_rate_limiter(session)
    await limiter.check_brief_research_retry(
        user_id=user.id, is_admin=is_admin(user, settings)
    )

    local_today = _local_today(tz_offset_minutes)
    await briefing_service.trigger_retry(beat.id, local_today)
    # Re-read after triggering (code-review FIX 9, the AL-522 GET precedent):
    # the claim inside `trigger_retry` is awaited and already committed by
    # the time it returns, so this refresh picks up "researching" rather
    # than echoing the stale, pre-claim "failed" this request started with.
    await session.refresh(beat)
    entries = await BriefRepository(session).list_for_beat(beat.id)
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
    brief: OwnedBrief,
    body: ReadPingRequest,
    user: CurrentUser,
    session: Session,
    tz_offset_minutes: TzOffsetMinutes = 0,
) -> Response:
    """Ping a Brief read -> ``204`` (D11, §6/§9).

    Ownership via ``OwnedBrief`` (``404`` otherwise). ``opened`` and
    ``sources`` are independent, first-write-wins columns
    (``mark_read``/``mark_sources_seen``) — idempotent per marker: a repeat
    ping with the same ``marker`` is still ``204`` and never moves the
    timestamp (the north-star metric, §9, asks *when a learner first opened*
    a Brief). An unrecognized ``marker`` is a ``422 validation_error`` before
    this body ever runs (``ReadPingMarker``'s ``Literal``).

    **AL-540:** ``brief_read`` fires only on the real, first-write-wins
    transition — the ``quick_check_attempted`` precedent (a repeat ping is
    not a second read) — after the ping's own commit, so a raising sink
    cannot turn an already-recorded read into a ``500``.

    **``age_days`` is computed against the learner's LOCAL day** (FIX 9,
    code-review — corrected from an earlier version that always used
    ``datetime.now(UTC).date()``). ``published_on`` is itself the learner's
    local day at the moment the arrival that produced it ran (D4a) — comparing
    it against UTC's *today* is not a rounding nicety, it is comparing two
    different calendars. For a learner east of UTC reading a fresh Brief in
    their own morning, UTC's *today* can still be *yesterday*, which made
    ``age_days`` **negative** — a value with no honest reading (an age of
    ``-1`` is not "no age yet", `age_days` has no ``NULL``/sentinel
    convention anywhere else this field is used). ``tz_offset_minutes`` is
    already a query param on every other route in this file
    (``TzOffsetMinutes``, §7's shared ``local_today`` derivation), so this was
    a self-imposed gap, not a missing capability. Defaults to ``0`` (UTC) so
    an old client that has not been updated to send it keeps today's exact
    behavior. **AL-531 must send this param from the client** for the fix to
    reach real learners — see the frontend read-ping call site.

    **A ping targeting a Skipped Brief is also a ``204`` no-op (code-review
    FIX 2, AL-531).** Deliberately the same shape as a repeat ping, not a
    ``404``: ``GET /briefs/{id}`` already resolves a Skipped id successfully
    (nulling ``number``/``title``/``body_markdown`` rather than 404ing —
    ``get_brief``'s own docstring), so this route stays consistent with that
    precedent rather than introducing a second, kind-dependent meaning for
    ``404`` on the same id. The no-op itself lives one layer down —
    ``BriefRepository.mark_read``/``mark_sources_seen`` now filter to
    ``BriefKind.PUBLISHED`` — so this handler does not need to branch on
    ``brief.kind`` at all; it already cannot stamp a Skipped row's
    ``read_at``/``sources_seen_at`` no matter what a caller (a client bug, or
    a request crafted by hand) sends. The shipped client also guards this on
    its own side (``routes/briefs.$briefId.tsx``'s effect never fires
    `opened` for a Brief with no body) — this is defense in depth for the
    data-integrity invariant ``repositories/briefs.py`` states outright: a
    Skipped period is never a read (PRD §4.6), and ``brief_read_rate.sql``
    depends on that holding at the row level, not just at the one client this
    ticket shipped.
    """
    briefs = BriefRepository(session)
    if body.marker == "opened":
        changed = await briefs.mark_read(brief.id)
    else:
        changed = await briefs.mark_sources_seen(brief.id)
    await session.commit()

    if changed:
        events.emit_brief_read(
            account_id=user.id,
            beat_id=brief.beat_id,
            brief_id=brief.id,
            marker=body.marker,
            age_days=(_local_today(tz_offset_minutes) - brief.published_on).days,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
