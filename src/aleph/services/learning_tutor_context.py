"""Deterministic metadata-only context; no generation-triggering reads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aleph.agents.learning_tutor import LearningTutorDeps
from aleph.domains.learning_recommendations import normalize_topic
from aleph.dtos.learning_tutor import EvidenceReference
from aleph.repositories.learning_tutor import LearningTutorRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.dtos.learning_tutor import OperationRequest
    from aleph.models.learning_tutor import LearningRecommendation as Card
    from aleph.models.learning_tutor import LearningTutorState


def serialize(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class LearningContext:
    deps: LearningTutorDeps
    evidence: dict[str, EvidenceReference]
    omitted: dict[str, int]
    needs_context: bool


async def assemble_context(
    session: AsyncSession,
    user_id: UUID,
    state: LearningTutorState,
    request: OperationRequest,
    *,
    analyst_enabled: bool,
    active: list[Card],
    selected: Card | None,
) -> LearningContext:
    repo = LearningTutorRepository(session)
    now = await repo.now()
    evidence: dict[str, EvidenceReference] = {}
    targets: dict[str, EvidenceReference] = {}
    omitted = dict(turns=0, paths=0, beats=0, briefs=0, recommendations=0)
    data: dict = dict(
        kind=request.kind,
        focus=state.focus,
        focus_version=state.focus_version,
        revision=state.revision,
        analyst_enabled=analyst_enabled,
        available_slots=3 if request.kind == "refresh" else 3 - len(active),
        content=request.content,
        selected=selected_payload(selected),
        turns=[],
        paths=[],
        beats=[],
        briefs=[],
        recommendations=[],
        omitted=omitted,
    )
    if state.focus:
        evidence["focus"] = EvidenceReference(kind="focus", observed_at=now)
    if request.content:
        evidence["message"] = EvidenceReference(kind="message", observed_at=now)
    if selected:
        selected_keys = []
        for index, raw in enumerate(selected.evidence or []):
            key = f"selected:{index}"
            evidence[key] = EvidenceReference.model_validate(raw)
            selected_keys.append(key)
        data["selected"]["evidence_keys"] = selected_keys
    data["evidence_keys"] = list(evidence)
    if len(serialize(data)) > 40000:
        raise ValueError("required context exceeds budget")

    def append(group: str, value: dict, ref: EvidenceReference | None = None) -> bool:
        key = f"{ref.kind}:{ref.id}" if ref else None
        if key and ref:
            value = dict(
                value,
                evidence_key=key,
                supplied_target_key=key,
                observed_at=ref.observed_at,
            )
        data[group].append(value)
        # Leave room for omitted counts to grow without exceeding the bound.
        if len(serialize(data)) > 39900:
            data[group].pop()
            omitted[group] += 1
            return False
        if key and ref:
            evidence[key] = ref
            targets[key] = ref
        return True

    turns = await repo.history(user_id, state.epoch, None)
    for index, row in enumerate(turns[:10]):
        if not append(
            "turns", dict(content=(row.input or {}).get("content"), output=row.output)
        ):
            omitted["turns"] += len(turns[:10]) - index - 1
            break
    total_turns = await repo.context_turn_count(user_id, state.epoch)
    omitted["turns"] += max(0, total_turns - 10)

    paths, total_paths = await repo.context_paths(user_id)
    omitted["paths"] += max(0, total_paths - len(paths))
    for row in paths:
        next_lesson = await repo.context_next_lesson(user_id, row.id)
        append(
            "paths",
            dict(
                topic=row.topic,
                level=row.level,
                guidance=(row.guidance or "")[:500],
                total_lessons=row.total or 0,
                completed_lessons=row.completed or 0,
                remaining_lessons=(row.total or 0) - (row.completed or 0),
                latest_completion=row.latest,
                next_lesson=dict(id=str(next_lesson.id), name=next_lesson.title[:200])
                if next_lesson
                else None,
            ),
            EvidenceReference(
                kind="path",
                id=row.id,
                completed_lessons=row.completed or 0,
                observed_at=now,
            ),
        )

    total_beats = 0
    if analyst_enabled:
        beats, total_beats = await repo.context_beats(user_id)
        omitted["beats"] += max(0, total_beats - len(beats))
        for row in beats:
            included = append(
                "beats",
                dict(
                    topic=row.topic,
                    level=row.level,
                    guidance=(row.guidance or "")[:500],
                    published_briefs=row.published or 0,
                    read_briefs=row.read or 0,
                    latest_read=row.latest,
                ),
                EvidenceReference(
                    kind="beat",
                    id=row.id,
                    published_briefs=row.published or 0,
                    read_briefs=row.read or 0,
                    observed_at=now,
                ),
            )
            if not included:
                continue
            briefs = await repo.context_briefs(user_id, row.id)
            omitted["briefs"] += max(0, (row.published or 0) - len(briefs))
            for brief in briefs:
                append(
                    "briefs",
                    dict(
                        beat_id=str(row.id),
                        title=(brief.title or "")[:200],
                        published_on=brief.published_on,
                        read_at=brief.read_at,
                    ),
                    EvidenceReference(kind="brief", id=brief.id, observed_at=now),
                )

    dismissed = await repo.recommendation_page(user_id, None)
    for card in [*active, *dismissed[:20]]:
        if card.kind == "beat" and not analyst_enabled:
            continue
        references = [
            EvidenceReference.model_validate(raw) for raw in card.evidence or []
        ]
        if (
            card.epoch != state.epoch
            or card.focus_version != state.focus_version
            or not all([await repo.source_exists(user_id, ref) for ref in references])
        ):
            omitted["recommendations"] += 1
            continue
        if append("recommendations", selected_payload(card) or {}):
            # Even prose can rely on excerpts whose sources missed the sample.
            for index, ref in enumerate(references):
                evidence[f"card:{card.id}:{index}"] = ref
    total_dismissed = await repo.dismissed_count(user_id)
    omitted["recommendations"] += max(0, total_dismissed - 20)
    # Complete exclusion is a validator input, not a truncated prompt excerpt.
    excluded = set()
    for kind in ("path", "beat"):
        excluded.update(
            (kind, normalize_topic(topic))
            for topic in await repo.target_topics(user_id, kind)
        )
    excluded.update(
        (kind, topic)
        for kind, topic in await repo.excluded_card_topics(
            user_id, except_id=selected.id if selected else None
        )
    )
    # Only an explicit replacement may reuse the selected card's Topic. Keep
    # independent target/dismissal/other-card collisions in the exclusion set.
    selected_topic = None
    if selected is not None:
        assert selected.normalized_topic is not None
        selected_topic = (selected.kind, selected.normalized_topic)
    deps = LearningTutorDeps(
        context=serialize(data),
        kind=request.kind,
        evidence_keys=tuple(evidence),
        targets=tuple((ref.kind, key) for key, ref in targets.items()),
        excluded=frozenset(excluded),
        analyst_enabled=analyst_enabled,
        available_slots=3 if request.kind == "refresh" else 3 - len(active),
        selected_id=selected.id if selected else None,
        selected_topic=selected_topic,
    )
    return LearningContext(
        deps,
        evidence,
        omitted,
        not state.focus and not total_paths and not total_beats,
    )


def selected_payload(card: Card | None) -> dict | None:
    if card is None:
        return None
    return dict(
        id=str(card.id),
        kind=card.kind,
        topic=card.topic,
        level=card.level,
        guidance=card.guidance,
        description=card.description,
        rationale=card.rationale,
        evidence=card.evidence,
        status=card.status,
    )
