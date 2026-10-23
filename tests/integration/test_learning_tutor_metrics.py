"""Execute saved LT5 SQL on disposable PostgreSQL, not a Python imitation."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path as FsPath

import pytest
from sqlalchemy import delete, text

from aleph import db, events
from aleph.models import Lesson, Level, Path, Unit
from aleph.models.beat import Beat
from aleph.models.brief import Brief
from aleph.models.learning_tutor import LearningRecommendation

from .conftest import create_user

QUERIES = FsPath(__file__).resolve().parents[2] / "queries"
AS_OF = datetime(2026, 10, 23, tzinfo=UTC)
EPSILON = timedelta(microseconds=1)


async def cohort(session, as_of=AS_OF):
    result = await session.execute(
        text((QUERIES / "postgres/learning_tutor_follow_through.sql").read_text()),
        {"as_of": as_of},
    )
    return {row["kind"]: dict(row) for row in result.mappings()}


async def target(session, user_id, kind, accepted_at, times):
    row = (
        Path(user_id=user_id, topic="Synthetic", level=Level.NEW_TO_IT)
        if kind == "path"
        else Beat(
            user_id=user_id, topic="Synthetic", level=Level.NEW_TO_IT, anchor_weekday=0
        )
    )
    session.add(row)
    await session.flush()
    if kind == "path":
        unit = Unit(path_id=row.id, position=0, title="Unit", summary="Synthetic")
        session.add(unit)
        await session.flush()
        for index, at in enumerate(times):
            session.add(
                Lesson(
                    path_id=row.id,
                    unit_id=unit.id,
                    position_in_path=index,
                    position_in_unit=index,
                    title="Lesson",
                    completed_at=at,
                )
            )
    else:
        for index, at in enumerate(times):
            session.add(
                Brief(
                    beat_id=row.id,
                    kind="published",
                    number=index + 1,
                    published_at=accepted_at,
                    published_on=accepted_at.date(),
                    title="Synthetic",
                    body_markdown="Synthetic",
                    read_at=at,
                )
            )
    receipt = LearningRecommendation(
        user_id=user_id,
        epoch=0,
        focus_version=0,
        kind=kind,
        status="accepted",
        accepted_at=accepted_at,
        accepted_request_hash="synthetic",
        accepted_path_id=row.id if kind == "path" else None,
        accepted_beat_id=row.id if kind == "beat" else None,
    )
    session.add(receipt)
    await session.flush()
    return row, receipt


@pytest.mark.anyio
@pytest.mark.parametrize("kind,days", [("path", 7), ("beat", 14)])
async def test_mature_cohort_boundaries_deletion_and_cleared_receipts(kind, days):
    async with db.async_session() as session:
        user = await create_user(session)
        start = AS_OF - timedelta(days=days)
        # Two successes: lower and upper bounds; repeated activity is still one.
        await target(session, user.id, kind, start, [start, start + EPSILON])
        await target(session, user.id, kind, start, [AS_OF])
        await target(session, user.id, kind, start, [start - EPSILON])
        await target(session, user.id, kind, start, [AS_OF + EPSILON])
        gone, _ = await target(session, user.id, kind, start, [start])
        await session.execute(delete(type(gone)).where(type(gone).id == gone.id))
        await target(session, user.id, kind, start + EPSILON, [AS_OF])
        await session.flush()
        row = (await cohort(session))[kind]
        assert row == dict(
            kind=kind,
            accepted=6,
            pending=1,
            mature=5,
            succeeded=2,
            no_activity=2,
            deleted_unknown=1,
            known=4,
            known_rate=0.5,
            confirmed_lower_bound=0.4,
        )
        before = (await cohort(session, AS_OF - EPSILON))[kind]
        assert before["mature"] == 0 and before["pending"] == 6
        assert before["known_rate"] is None
        after = (await cohort(session, AS_OF + EPSILON))[kind]
        assert after["mature"] == 6 and after["succeeded"] == 3
        # All receipts already have cleared payloads; no content is needed.
        assert row["accepted"] == 6


@pytest.mark.anyio
async def test_skipped_and_sources_only_are_not_published_brief_reads():
    async with db.async_session() as session:
        user = await create_user(session)
        start = AS_OF - timedelta(days=14)
        beat, _ = await target(session, user.id, "beat", start, [])
        session.add_all(
            [
                Brief(
                    beat_id=beat.id,
                    kind="skipped",
                    published_at=start,
                    published_on=start.date(),
                    skip_line="No news",
                    read_at=start,
                ),
                Brief(
                    beat_id=beat.id,
                    kind="published",
                    number=1,
                    published_at=start,
                    published_on=start.date(),
                    title="Synthetic",
                    body_markdown="Synthetic",
                    sources_seen_at=start,
                ),
            ]
        )
        await session.flush()
        assert (await cohort(session))["beat"]["succeeded"] == 0


async def records(session, rows):
    await session.execute(
        text(
            "CREATE TEMP TABLE records (span_name text, attributes jsonb, "
            "start_timestamp timestamptz) ON COMMIT DROP"
        )
    )
    for event, attributes in rows:
        assert set(attributes) <= events.EVENT_FIELDS[event]
        await session.execute(
            text("INSERT INTO records VALUES (:event, CAST(:attrs AS jsonb), :at)"),
            {"event": event, "attrs": json.dumps(attributes), "at": AS_OF},
        )


async def query(session, name):
    result = await session.execute(text((QUERIES / "logfire" / name).read_text()))
    return [dict(row) for row in result.mappings()]


@pytest.mark.anyio
async def test_replayed_observations_do_not_inflate_counts():
    accepted = (
        "learning_recommendation_accepted",
        dict(account_id="a", recommendation_id="r", target_kind="path", target_id="p"),
    )
    completed = (
        "learning_tutor_operation_completed",
        dict(
            account_id="a",
            operation_id="o",
            kind="turn",
            outcome="completed",
            duration_ms=100,
            input_tokens=10,
            output_tokens=5,
            model_admitted=True,
        ),
    )
    failed = (
        "learning_tutor_operation_completed",
        dict(
            account_id="a",
            operation_id="f",
            kind="turn",
            outcome="provider_failed",
            duration_ms=200,
            input_tokens=4,
            output_tokens=0,
            model_admitted=True,
        ),
    )
    cold = (
        "learning_tutor_operation_completed",
        dict(
            account_id="a",
            operation_id="c",
            kind="refresh",
            outcome="needs_context",
            duration_ms=0,
            input_tokens=0,
            output_tokens=0,
            model_admitted=False,
        ),
    )
    shown = (
        "learning_recommendation_interacted",
        dict(
            account_id="a",
            recommendation_id="r",
            interaction="shown",
            surface="home",
            session_id="s",
        ),
    )
    dismissed = (
        "learning_recommendation_decided",
        dict(account_id="a", recommendation_id="r", decision="dismiss"),
    )
    async with db.async_session() as session:
        await records(
            session,
            [
                accepted,
                accepted,
                completed,
                completed,
                failed,
                cold,
                shown,
                shown,
                dismissed,
                dismissed,
            ],
        )
        assert len(await query(session, "learning_tutor_acceptances.sql")) == 1
        operations = await query(session, "learning_tutor_operations.sql")
        turn = next(row for row in operations if row["kind"] == "turn")
        assert turn["operations"] == 2 and turn["succeeded"] == 1
        assert turn["failed"] == 1 and turn["model_admissions"] == 2
        assert turn["total_tokens"] == 19 and turn["tokens_per_success"] == 19
        assert turn["tokens_per_observed_acceptance"] == 19
        interactions = await query(session, "learning_tutor_interactions.sql")
        assert {row["action"]: row["observations"] for row in interactions} == {
            "shown": 1,
            "dismiss": 1,
        }


@pytest.mark.anyio
async def test_missing_event_reconciliation_uses_receipts_not_observations():
    async with db.async_session() as session:
        user = await create_user(session)
        start = AS_OF - timedelta(days=14)
        path, observed = await target(session, user.id, "path", start, [start])
        _, missing = await target(session, user.id, "path", start, [])
        event = (
            "learning_recommendation_accepted",
            dict(
                account_id=str(user.id),
                recommendation_id=str(observed.id),
                target_kind="path",
                target_id=str(path.id),
            ),
        )
        await records(session, [event, event])
        durable = (
            (
                await session.execute(
                    text(
                        (
                            QUERIES / "postgres/learning_tutor_acceptances.sql"
                        ).read_text()
                    ),
                    {"as_of": AS_OF},
                )
            )
            .mappings()
            .all()
        )
        seen = await query(session, "learning_tutor_acceptances.sql")
        durable_ids = {str(row["recommendation_id"]) for row in durable}
        seen_ids = {row["recommendation_id"] for row in seen}
        assert durable_ids - seen_ids == {str(missing.id)}
        assert seen_ids - durable_ids == set()
        assert (await cohort(session))["path"]["mature"] == 2
        assert (await cohort(session))["path"]["succeeded"] == 1
        assert all(
            set(row)
            == {
                "account_id",
                "recommendation_id",
                "target_kind",
                "target_id",
                "accepted_at",
            }
            for row in durable
        )


@pytest.mark.anyio
async def test_unrelated_work_and_empty_database_do_not_create_success():
    async with db.async_session() as session:
        assert await cohort(session) == {}
        user = await create_user(session)
        start = AS_OF - timedelta(days=7)
        await target(session, user.id, "path", start, [])
        # A second path's completion must not count for the first receipt.
        await target(session, user.id, "path", start, [start])
        row = (await cohort(session))["path"]
        assert row["mature"] == 2 and row["succeeded"] == 1


@pytest.mark.anyio
async def test_zero_success_and_acceptance_denominators_are_null():
    async with db.async_session() as session:
        await records(
            session,
            [
                (
                    "learning_tutor_operation_completed",
                    dict(
                        account_id="a",
                        operation_id="failed",
                        kind="turn",
                        outcome="interrupted",
                        duration_ms=120000,
                        input_tokens=0,
                        output_tokens=0,
                        model_admitted=True,
                    ),
                )
            ],
        )
        (row,) = await query(session, "learning_tutor_operations.sql")
        assert row["failed"] == 1 and row["model_admissions"] == 1
        assert row["tokens_per_success"] is None
        assert row["tokens_per_observed_acceptance"] is None


@pytest.mark.anyio
@pytest.mark.parametrize("kind,days", [("path", 7), ("beat", 14)])
async def test_elapsed_window_is_independent_of_reporting_session_dst(kind, days):
    async with db.async_session() as session:
        await session.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        user = await create_user(session)
        start = datetime(2026, 10, 30, 12, tzinfo=UTC)
        deadline = start + timedelta(days=days)
        await target(session, user.id, kind, start, [deadline])
        row = (await cohort(session, deadline))[kind]
        assert row["mature"] == 1 and row["succeeded"] == 1
