"""Authorization predicates over the resolved user (AL-021, TDD §7/D14).

Admin status is derived from the user's email domain rather than stored: the
email is refreshed from the identity provider on every sign-in
(:meth:`aleph.services.auth.AuthService.sign_in`), and an unverified email is
dropped before it is stored (:func:`aleph.auth.fetch_identity`), so
classification self-heals and needs no migration or management UI. The trust
anchor is the OIDC provider's verified ``email`` claim (TDD §7).

Adapted from habagou's ``authz`` module; aleph has no guest accounts, so the
only gate is the email domain. The admin domains are passed in (rather than read
from a module-level singleton) so callers can classify against any config and
the predicate stays trivially testable — AL-042 (admin rate-limit exemption) and
AL-052 (picker enforcement) consume this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aleph.config import Settings
    from aleph.models import User


def is_admin(user: User, settings: Settings) -> bool:
    """Whether ``user`` belongs to the admin class.

    True iff the user has an email whose domain — the part after the final
    ``@``, compared case-insensitively and exactly (no subdomain or suffix
    matching) — is one of ``settings.admin_email_domain_set``. A missing email
    (the provider dropped an unverified one) or an address with no ``@`` is
    never an admin.
    """
    if not user.email or "@" not in user.email:
        return False
    domain = user.email.rsplit("@", 1)[1].lower()
    return domain in settings.admin_email_domain_set
