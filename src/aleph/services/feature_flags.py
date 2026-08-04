"""Feature-flag registry and per-user resolution (AL-203, epic #82).

Flags are **defined in code**: add a :class:`FeatureFlag` member and a default in
:data:`FLAG_DEFAULTS` to introduce one. Their global default can then be flipped
without a code deploy via ``settings.feature_flag_defaults``
(``FEATURE_FLAG_DEFAULTS``), and per learner via ``user_feature_overrides`` rows
managed through the admin API.

**This module docstring is the single authoritative statement of the resolution
order** (``docs/api.md`` restates it for API readers and must match); every other
site — the DTO comment, the router, the frontend client — points here rather than
respelling it.

Resolution order — highest wins::

    per-user override > settings default > admin default > code default

Read as four steps, each only consulted when the one above it is silent:

1. **per-user override** — a ``user_feature_overrides`` row for this user and
   flag. Always wins, for learners and admins alike.
2. **settings default** — an entry for this flag in
   ``settings.feature_flag_defaults`` (``FEATURE_FLAG_DEFAULTS``). Because it
   outranks the admin baseline, an explicit ``tutor:off`` silences *everyone*,
   admins included: the operator knob is a real kill switch, and an admin who
   wants to keep dogfooding takes a per-user override.
3. **admin default** — :data:`ADMIN_DEFAULT_FLAGS` forced on for the admin class,
   applied only to flags the settings map says nothing about.
4. **code default** — :data:`FLAG_DEFAULTS`.

Keys absent from the registry — stale settings entries or database rows for
deleted flags — are ignored, so removing a flag from code needs no data
migration and no cleanup pass.

This is what let Phase 2 ship dark (epic #82, owner amendment 1): ``tutor``
defaulted **off** globally but sat in :data:`ADMIN_DEFAULT_FLAGS`, so every
ticket merged and deployed with zero learner exposure while admins dogfooded the
tutor in production. ``shaping`` (Phase 2B, epic #114 adopted convention 1) was
registered the same way — a separate flag, so either could ship dark or be killed
without disturbing the other.

``streaks`` (Phase 5, D7) was registered the same way again, and rode the same
playbook from dark to launched.

**All three are now launched and default on** (AL-270, AL-370, and the streaks
flip). Their entry in :data:`FLAG_DEFAULTS` is the whole of that: a clone with no
``FEATURE_FLAG_DEFAULTS`` set — a laptop, a CI run, a fresh deploy — resolves them
on and shows the product a learner actually sees. Nothing about the machinery
changed, and the dark posture above is still exactly how the next phase ships:
register the flag ``False``, add it to :data:`ADMIN_DEFAULT_FLAGS`, flip it here at
launch.

A launched flag is still a **kill switch**: ``FEATURE_FLAG_DEFAULTS=tutor:off``
outranks this module's defaults with no code deploy, and reaches admins too (step
2 above), which is what makes it usable mid-incident.

Ported from habagou's service of the same name; adapted to aleph's
``authz.is_admin(user, settings)`` signature, which takes the config explicitly,
and to this ticket's precedence — habagou lets the admin baseline outrank the
settings map, aleph does not (step 2 above).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (  # noqa: TC002 - FastAPI resolves annotations.
    AsyncSession,
)

from aleph.authz import is_admin
from aleph.config import settings as global_settings
from aleph.dtos.feature_flags import FeatureFlagDTO
from aleph.models import User  # noqa: TC001 - FastAPI resolves annotations.
from aleph.repositories import FeatureFlagRepository, UserRepository

if TYPE_CHECKING:
    import uuid

    from aleph.config import Settings


class FeatureFlag(StrEnum):
    """Canonical feature flags.

    A ``StrEnum`` member *is* its wire key, so the registry, the settings
    parser, the database rows and the JSON the frontend reads all speak one
    spelling with no mapping table.
    """

    # Phase 2's one flag: the in-lesson tutor (the rail, its API, its stream).
    TUTOR = "tutor"
    # Phase 2B's one flag: shaping (the shaping rail, its API, its stream, and
    # the apply/undo endpoints). Independent of ``TUTOR`` on purpose — the
    # in-lesson tutor is already launched, and shaping must be able to ship dark
    # and be killed on its own without disturbing it.
    SHAPING = "shaping"
    # Phase 5's one flag (streaks, pulled forward — CONTEXT.md phase-boundary
    # note): ``GET /progress/summary`` and everything under it. Its own key,
    # independent of ``TUTOR``/``SHAPING``, for the same reason those two are
    # independent of each other — a kill switch that only kills the surface it
    # names.
    STREAKS = "streaks"
    # Phase 3's one flag (TDD D10): drafting, the daily queue, review and the
    # due pill — every flashcards route, gated router-level. Off -> ``404`` on
    # every route, and the streak union (TDD §5.5) silently loses its second
    # signal. Its own key, independent of the three above, for the same reason
    # they are independent of each other.
    FLASHCARDS = "flashcards"


# Code defaults per flag. Every FeatureFlag member gets an entry here; a flag
# missing from this dict does not exist as far as resolution is concerned.
FLAG_DEFAULTS: dict[FeatureFlag, bool] = {
    # On: Phase 2 is launched (AL-270). Both phases spent their build-out at
    # ``False`` here, which is what let every ticket merge and deploy dark; now
    # that they are live for all learners, **on is the honest default** — and
    # the one that makes a fresh clone, a local ``just dev`` and an integration
    # run show the same product a learner sees, with no environment to set.
    FeatureFlag.TUTOR: True,
    # On: Phase 2B is launched (AL-370), for the same two reasons.
    FeatureFlag.SHAPING: True,
    # On: Phase 5's streaks slice is launched. It spent its whole build-out at
    # ``False`` — shipping dark (D7) is what let every ticket merge and deploy
    # with zero learner exposure while admins dogfooded it — and this flip is
    # the launch itself, exactly the move AL-270/AL-370 made for the two above.
    FeatureFlag.STREAKS: True,
    # Off: Phase 3 (flashcards) has not launched. Starts ``False`` here, the
    # same dark posture ``tutor``/``shaping``/``streaks`` each spent their own
    # build-out at (TDD D10) — every flashcards ticket merges and deploys with
    # zero learner exposure while admins dogfood drafting and review, and this
    # flag flips to ``True`` only at launch, the AL-270/AL-370/streaks playbook
    # repeated a fourth time.
    FeatureFlag.FLASHCARDS: False,
}


# Flags that resolve to on for admins when nothing above them in the chain has
# spoken, so a feature can ship dark and be dogfooded by the admin class before a
# wider rollout. This baseline is the *weakest* say after the code default: it
# applies only to flags with no entry in ``FEATURE_FLAG_DEFAULTS``, so an explicit
# ``tutor:off`` there turns the flag off for admins too (kill switch), and a
# per-user override beats it for everyone, admins included.
#
# ``TUTOR``/``SHAPING``/``STREAKS`` are **currently redundant**: a flag whose
# code default is already ``True`` is on for admins by that default alone, and
# after a ``:off`` kill the settings map outranks this baseline anyway, so
# membership changes no answer either way. They stay listed rather than dropped
# because this is the seam the *next* dark phase uses, and re-deriving which
# flags belong here is exactly the kind of thing that gets forgotten at the
# moment a flag flips back off. ``FLASHCARDS`` is the live case right now: its
# code default is ``False`` (TDD D10), so this membership is what lets admins
# dogfood drafting and review while every learner still sees a ``404``.
ADMIN_DEFAULT_FLAGS: frozenset[FeatureFlag] = frozenset(
    {
        FeatureFlag.TUTOR,
        FeatureFlag.SHAPING,
        FeatureFlag.STREAKS,
        FeatureFlag.FLASHCARDS,
    }
)


def known_flag_keys() -> frozenset[str]:
    """The registered flag keys (StrEnum members are their string keys)."""
    return frozenset(str(flag) for flag in FLAG_DEFAULTS)


def _admin_default_keys() -> frozenset[str]:
    """String keys of the flags that default on for admins."""
    return frozenset(str(flag) for flag in ADMIN_DEFAULT_FLAGS)


def effective_defaults(config: Settings) -> dict[str, bool]:
    """Code defaults with ``config.feature_flag_defaults`` applied on top.

    Unregistered settings keys are dropped here rather than passed through — a
    map is only ever built from the code registry, so a stale entry can never
    invent a flag.
    """
    defaults = {str(flag): enabled for flag, enabled in FLAG_DEFAULTS.items()}
    for key, enabled in config.feature_flag_default_map.items():
        if key in defaults:
            defaults[key] = enabled
    return defaults


class FeatureFlagService:
    """Resolves flags for a learner and manages their per-user overrides."""

    def __init__(
        self, session: AsyncSession, *, config: Settings = global_settings
    ) -> None:
        self.session = session
        self.config = config
        self.repository = FeatureFlagRepository(session)
        self.user_repository = UserRepository(session)

    async def resolve_for_user(self, user: User) -> dict[str, bool]:
        """The user's effective flag map, resolved by this module's order.

        Admins get :data:`ADMIN_DEFAULT_FLAGS` forced on as their baseline, but
        only for flags ``FEATURE_FLAG_DEFAULTS`` does not mention, and a per-user
        override still beats both. The map is keyed by the registry, so a
        database row for a deleted flag never leaks out.
        """
        defaults = effective_defaults(self.config)
        if not defaults:
            return {}
        if is_admin(user, self.config):
            admin_keys = _admin_default_keys()
            # An explicit settings entry outranks the admin baseline, so the
            # baseline only fills in flags the settings map is silent about.
            settings_keys = self.config.feature_flag_default_map.keys()
            defaults = {
                key: True if key in admin_keys and key not in settings_keys else enabled
                for key, enabled in defaults.items()
            }
        overrides = await self.repository.overrides_for_user(user_id=user.id)
        return {key: overrides.get(key, enabled) for key, enabled in defaults.items()}

    async def list_flags(self) -> list[FeatureFlagDTO]:
        """Every registered flag with its effective default and override count."""
        counts = await self.repository.override_counts()
        return [
            FeatureFlagDTO(
                key=key,
                enabled_default=enabled,
                override_count=counts.get(key, 0),
            )
            for key, enabled in sorted(effective_defaults(self.config).items())
        ]

    async def set_user_override(
        self, *, flag_key: str, user_id: uuid.UUID, enabled: bool
    ) -> bool:
        """Upsert a user's override; ``False`` if the user does not exist.

        The row lock holds the account in place until the commit, so a
        concurrent deletion cannot slip between the existence check and the
        insert (which would surface as a foreign-key ``500``, not a ``404``).
        """
        if await self.user_repository.lock_by_id(user_id) is None:
            return False
        await self.repository.set_override(
            user_id=user_id, flag_key=flag_key, enabled=enabled
        )
        await self.session.commit()
        return True

    async def clear_user_override(self, *, flag_key: str, user_id: uuid.UUID) -> bool:
        """Delete a user's override; ``False`` if none existed."""
        deleted = await self.repository.delete_override(
            user_id=user_id, flag_key=flag_key
        )
        await self.session.commit()
        return deleted
