"""Offline, account-scoped demo fixtures. Never starts the app or model workers."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path as FilePath
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aleph.agents.lesson import (
    QuickCheck as CheckContent,
)
from aleph.agents.lesson import (
    correct_index_in_range,
    has_valid_option_count,
    options_are_distinct,
)
from aleph.models import Attempt, Beat, Brief, Lesson, Path, QuickCheck, Unit, User
from aleph.models.enums import (
    BeatResearchState,
    BriefKind,
    LessonGenerationState,
    Level,
    PathStatus,
)
from aleph.models.learning_tutor import (
    LearningRecommendation,
    LearningTutorOperation,
    LearningTutorState,
)
from aleph.repositories.feature_flags import FeatureFlagRepository

PACK_NAME = "healthtech-explorer"
PACK_FILE = (
    FilePath(__file__).resolve().parents[1] / "fixtures/demo/healthtech-explorer.json"
)
USER_ID = UUID("00dadd00-0000-4000-8000-000000000001")
IDENTITY = ("https://demo.aleph.invalid", PACK_NAME)
# One explicitly supported migration from the original isolated Tutor fixture.
LEGACY_IDENTITY = ("https://synthetic.invalid/interactive", "fictional-owner")
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Days = Annotated[int, Field(ge=0)]


class SeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LessonSeed(SeedModel):
    title: Nonempty
    read_passage: Nonempty
    completed_days_ago: Days | None
    quick_check: CheckContent


class UnitSeed(SeedModel):
    title: Nonempty
    summary: Nonempty
    lessons: list[LessonSeed] = Field(min_length=1)


class PathSeed(SeedModel):
    slug: Nonempty
    topic: Nonempty
    level: Level
    guidance: Nonempty
    teaching_style: Nonempty
    started_days_ago: Days
    units: list[UnitSeed] = Field(min_length=1)


class BriefSeed(SeedModel):
    title: Nonempty
    body_markdown: Nonempty
    days_ago: Days
    read: bool


class BeatSeed(SeedModel):
    slug: Nonempty
    topic: Nonempty
    level: Level
    guidance: Nonempty
    briefs: list[BriefSeed] = Field(min_length=1)


class Persona(SeedModel):
    display_name: Nonempty
    description: Nonempty
    focus: Nonempty


class DemoPack(SeedModel):
    version: Literal[1]
    persona: Persona
    paths: list[PathSeed] = Field(min_length=1)
    beats: list[BeatSeed]

    @model_validator(mode="after")
    def coherent_history(self) -> DemoPack:
        for group in (self.paths, self.beats):
            if len({row.slug for row in group}) != len(group):
                raise ValueError("Fixture slugs must be unique within each kind")
        for path in self.paths:
            unfinished = False
            previous_age = path.started_days_ago
            for unit in path.units:
                for lesson in unit.lessons:
                    check = lesson.quick_check
                    if not (
                        has_valid_option_count(check.options)
                        and correct_index_in_range(
                            check.correct_index, len(check.options)
                        )
                        and options_are_distinct(check.options)
                        and all(
                            part.strip()
                            for part in [check.stem, check.explanation, *check.options]
                        )
                    ):
                        raise ValueError("Invalid Quick check")
                    age = lesson.completed_days_ago
                    if age is None:
                        unfinished = True
                    elif unfinished or age > previous_age:
                        raise ValueError(
                            "Completions must form a chronological prefix "
                            "after path creation"
                        )
                    else:
                        previous_age = age
        for beat in self.beats:
            ages = [brief.days_ago for brief in beat.briefs]
            if len(set(ages)) != len(ages):
                raise ValueError("Brief dates must be distinct within a Beat")
        return self


def load_pack(path: FilePath = PACK_FILE) -> DemoPack:
    return DemoPack.model_validate_json(path.read_text())


def checked_url(value: str) -> URL:
    """Fail closed, including query parameters that could override the host."""
    try:
        url = make_url(value)
    except Exception:
        raise ValueError(
            "Supply ALEPH_DEMO_DATABASE_URL for a local demo database"
        ) from None
    if not (
        url.drivername == "postgresql+asyncpg"
        and url.host in {"localhost", "127.0.0.1", "::1"}
        and not url.query
        and (
            re.fullmatch(r"aleph_demo_[a-z0-9_]+", url.database or "")
            or url.database == "aleph_tutor_interactive"
        )
    ):
        raise ValueError(
            "Only loopback aleph_demo_* or aleph_tutor_interactive databases "
            "are allowed; URL options are forbidden"
        )
    return url


def fixture_id(key: str) -> UUID:
    return uuid5(USER_ID, key)


async def seed(
    session: AsyncSession, pack: DemoPack, anchor: date, *, reset: bool = False
) -> str:
    """Caller owns one transaction. Reset only this known fictional account."""
    await session.execute(text("SELECT pg_advisory_xact_lock(71490231)"))
    user = await session.get(User, USER_ID, with_for_update=True)
    if user and (user.issuer, user.subject) not in {IDENTITY, LEGACY_IDENTITY}:
        raise ValueError(
            "Fixture account ID belongs to another identity; refusing writes"
        )
    if user and not reset:
        return "preserved"
    epoch = 0
    if user:
        running = await session.scalar(
            select(func.count())
            .select_from(LearningTutorOperation)
            .where(
                LearningTutorOperation.user_id == USER_ID,
                LearningTutorOperation.status == "running",
            )
        )
        if running:
            raise ValueError(
                "Tutor operation is running; stop the runtime and wait before reset"
            )
        old_state = await session.get(LearningTutorState, USER_ID)
        epoch = old_state.epoch + 1 if old_state else 1
        if old_state:
            session.expunge(old_state)
        await session.execute(delete(User).where(User.id == USER_ID))
        await session.flush()
    start = datetime.combine(anchor, time.min, tzinfo=UTC)
    session.add(
        User(
            id=USER_ID,
            issuer=IDENTITY[0],
            subject=IDENTITY[1],
            username="demo-healthtech-explorer",
            display_name=pack.persona.display_name,
            email=None,
            time_zone="UTC",
            created_at=start - timedelta(days=90),
        )
    )
    await session.flush()
    for flag in ("learning_tutor", "analyst"):
        await FeatureFlagRepository(session).set_override(
            user_id=USER_ID, flag_key=flag, enabled=True
        )
    session.add(
        LearningTutorState(user_id=USER_ID, focus=pack.persona.focus, epoch=epoch)
    )
    for path in pack.paths:
        path_id = fixture_id(f"path:{path.slug}")
        created = start - timedelta(days=path.started_days_ago)
        session.add(
            Path(
                id=path_id,
                user_id=USER_ID,
                topic=path.topic,
                level=path.level,
                guidance=path.guidance,
                teaching_style=path.teaching_style,
                status=PathStatus.READY,
                model_outline="stub",
                model_lesson="stub",
                created_at=created,
            )
        )
        await session.flush()
        position = 0
        for ui, unit in enumerate(path.units):
            unit_id = fixture_id(f"unit:{path.slug}:{ui}")
            session.add(
                Unit(
                    id=unit_id,
                    path_id=path_id,
                    position=ui,
                    title=unit.title,
                    summary=unit.summary,
                    created_at=created,
                )
            )
            await session.flush()
            for li, lesson in enumerate(unit.lessons):
                lesson_id = fixture_id(f"lesson:{path.slug}:{position}")
                completed = (
                    start - timedelta(days=lesson.completed_days_ago)
                    if lesson.completed_days_ago is not None
                    else None
                )
                session.add(
                    Lesson(
                        id=lesson_id,
                        path_id=path_id,
                        unit_id=unit_id,
                        position_in_path=position,
                        position_in_unit=li,
                        title=lesson.title,
                        generation_state=LessonGenerationState.GENERATED,
                        read_passage=lesson.read_passage,
                        generated_at=created,
                        completed_at=completed,
                        created_at=created,
                    )
                )
                await session.flush()
                check_id = fixture_id(f"check:{path.slug}:{position}")
                session.add(
                    QuickCheck(
                        id=check_id,
                        lesson_id=lesson_id,
                        **lesson.quick_check.model_dump(),
                        created_at=created,
                    )
                )
                await session.flush()
                if completed:
                    # Vary correctness without changing completion progress.
                    selected = (
                        lesson.quick_check.correct_index + (position % 3 == 1)
                    ) % len(lesson.quick_check.options)
                    session.add(
                        Attempt(
                            id=fixture_id(f"attempt:{path.slug}:{position}"),
                            user_id=USER_ID,
                            quick_check_id=check_id,
                            selected_index=selected,
                            is_correct=selected == lesson.quick_check.correct_index,
                            created_at=completed,
                        )
                    )
                position += 1
    for beat in pack.beats:
        beat_id = fixture_id(f"beat:{beat.slug}")
        session.add(
            Beat(
                id=beat_id,
                user_id=USER_ID,
                topic=beat.topic,
                guidance=beat.guidance,
                level=beat.level,
                anchor_weekday=anchor.weekday(),
                research_state=BeatResearchState.IDLE,
                model_research="stub",
                model_brief="stub",
                created_at=start - timedelta(days=35),
            )
        )
        await session.flush()
        for number, brief in enumerate(
            sorted(beat.briefs, key=lambda b: -b.days_ago), start=1
        ):
            published = start - timedelta(days=brief.days_ago)
            session.add(
                Brief(
                    id=fixture_id(f"brief:{beat.slug}:{number}"),
                    beat_id=beat_id,
                    kind=BriefKind.PUBLISHED,
                    number=number,
                    title=brief.title,
                    body_markdown=brief.body_markdown,
                    published_at=published,
                    published_on=published.date(),
                    read_at=published if brief.read else None,
                    claims=[],
                    created_at=published,
                )
            )
    await session.flush()
    return "reset" if reset else "loaded"


async def status(session: AsyncSession) -> dict:
    counts = {}
    for name, model in (
        ("paths", Path),
        ("beats", Beat),
        ("operations", LearningTutorOperation),
        ("recommendations", LearningRecommendation),
    ):
        counts[name] = await session.scalar(
            select(func.count()).select_from(model).where(model.user_id == USER_ID)
        )
    counts["lessons"] = await session.scalar(
        select(func.count())
        .select_from(Lesson)
        .join(Path)
        .where(Path.user_id == USER_ID)
    )
    counts["completed_lessons"] = await session.scalar(
        select(func.count())
        .select_from(Lesson)
        .join(Path)
        .where(Path.user_id == USER_ID, Lesson.completed_at.is_not(None))
    )
    counts["briefs"] = await session.scalar(
        select(func.count())
        .select_from(Brief)
        .join(Beat)
        .where(Beat.user_id == USER_ID)
    )
    return counts


async def run(action: str, url: URL, pack: DemoPack, anchor: date) -> dict:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine).begin() as session:
            actual = await session.scalar(text("SELECT current_database()"))
            if actual != url.database:
                raise ValueError(
                    "Connected database identity differs from requested demo database"
                )
            result = (
                "inspected"
                if action == "status"
                else await seed(session, pack, anchor, reset=action == "reset")
            )
            return {
                "database": actual,
                "account": str(USER_ID),
                "result": result,
                **await status(session),
            }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "status", "load", "reset"))
    parser.add_argument(
        "--anchor-date", type=date.fromisoformat, default=datetime.now(UTC).date()
    )
    parser.add_argument("--confirm-reset", choices=(PACK_NAME,))
    args = parser.parse_args()
    if args.action == "reset" and args.confirm_reset != PACK_NAME:
        parser.error(
            "reset requires --confirm-reset healthtech-explorer "
            "and a stopped demo runtime"
        )
    if args.anchor_date > datetime.now(UTC).date():
        parser.error("anchor date cannot be in the future")
    try:
        pack = load_pack()
        if args.action == "validate":
            print(
                json.dumps(
                    {
                        "pack": PACK_NAME,
                        "version": pack.version,
                        "paths": len(pack.paths),
                        "beats": len(pack.beats),
                    }
                )
            )
        else:
            url = checked_url(os.environ.get("ALEPH_DEMO_DATABASE_URL", ""))
            print(
                json.dumps(asyncio.run(run(args.action, url, pack, args.anchor_date)))
            )
    except SQLAlchemyError:
        parser.exit(
            1,
            "Demo database operation failed; check the local connection "
            "and migrated schema. No URL is logged.\n",
        )
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
