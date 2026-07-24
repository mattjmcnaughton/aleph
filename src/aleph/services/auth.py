"""Authentication service: OIDC identity -> local account (AL-020).

Ported near-verbatim from habagou. Provisioning is keyed on the stable
``(issuer, subject)`` pair; ``username``, ``display_name`` and ``email`` are
presentation claims refreshed on every sign-in. The service owns the unit of
work (commit) around the repository (habagou layering).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from aleph import events
from aleph.repositories import UserRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.auth import AuthIdentity
    from aleph.models import User

# Bounds the provision retry loop under a username collision (each retry picks
# the next free suffix against the now-committed row); an identity collision
# resolves on the first retry via re-fetch. A handful of attempts is far more
# than concurrent first-logins could realistically contend for.
_MAX_PROVISION_ATTEMPTS = 5


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def sign_in(self, identity: AuthIdentity) -> User:
        """Provision-or-reuse the account for an OIDC identity, refresh claims."""
        user = await self.users.get_by_identity(identity.issuer, identity.subject)
        if user is None:
            return await self._provision(identity)

        user.display_name = identity.display_name
        user.email = identity.email
        await self.session.flush()
        await self.session.commit()
        return user

    async def _provision(self, identity: AuthIdentity) -> User:
        """Insert the account, tolerating a concurrent racing first-login.

        Two callers whose ``get_by_identity`` both miss will both try to insert.
        Postgres serializes them on the unique indexes, so the loser's flush
        raises ``IntegrityError`` — a 500 without this guard. On that error we
        roll back and re-fetch by identity: if a concurrent first-login for the
        *same* identity won, we reuse its row; if instead a *different* identity
        took our synthesized username, the retry recomputes a free username
        against the now-committed row.
        """
        for _ in range(_MAX_PROVISION_ATTEMPTS):
            try:
                user = await self.users.create(
                    username=await self._available_username(identity.username),
                    display_name=identity.display_name,
                    issuer=identity.issuer,
                    subject=identity.subject,
                    email=identity.email,
                )
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
                existing = await self.users.get_by_identity(
                    identity.issuer, identity.subject
                )
                if existing is not None:
                    # A concurrent first-login for the *same* identity won and
                    # already emitted ``account_created``; reusing its row must
                    # not double-count the new account.
                    return existing
                continue
            else:
                # A genuinely new account (PRD §5.7): the record timestamp is the
                # signup time anchoring the activation cohort + 7-day window.
                events.emit_account_created(account_id=user.id)
                return user

        raise RuntimeError(
            "could not provision account after concurrent contention "
            f"({_MAX_PROVISION_ATTEMPTS} attempts)"
        )

    async def _available_username(self, preferred: str) -> str:
        """Synthesize a collision-free username (``username`` is UNIQUE)."""
        base = _normalize_username(preferred)
        candidate = base
        suffix = 2
        while await self.users.username_exists(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate


def _normalize_username(username: str) -> str:
    normalized = username.strip().lower().replace(" ", "-")
    return normalized or "user"
