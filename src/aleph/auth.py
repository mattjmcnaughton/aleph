"""Configurable OIDC provider registration and identity extraction.

Ported near-verbatim from habagou (TDD §7/D2). Keycloak is the deterministic
local/CI provider; Auth0 is the intended hosted provider. The application is
coupled to neither: it discovers a standards-compliant OIDC provider from
``OIDC_ISSUER`` (or the optional ``OIDC_METADATA_URL`` override).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from authlib.integrations.starlette_client import OAuth

if TYPE_CHECKING:
    from aleph.config import Settings

oauth = OAuth()


@dataclass(frozen=True)
class AuthIdentity:
    """The stable identity shape mapped out of provider OIDC claims.

    ``(issuer, subject)`` is the sole identity key; ``username``,
    ``display_name`` and ``email`` are presentation data refreshed at login.
    """

    issuer: str
    subject: str
    username: str
    display_name: str
    email: str | None = None


def register_provider(settings: Settings) -> None:
    """Register the configured OIDC provider through discovery metadata."""
    metadata_url = settings.oidc_metadata_url or (
        f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
    )
    oauth.register(
        name=settings.oidc_provider,
        server_metadata_url=metadata_url,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": settings.oidc_scopes},
    )


def fetch_identity(token: dict[str, Any]) -> AuthIdentity:
    """Map standard OIDC claims to the app's stable identity shape.

    A thin wrapper over ``_oidc_identity`` kept for habagou parity: it is the
    seam the callback patches in tests and the place a provider-specific claim
    quirk (e.g. an Auth0 namespace) would be handled without touching callers.
    """
    return _oidc_identity(token)


def _oidc_identity(token: dict[str, Any]) -> AuthIdentity:
    claims = token.get("userinfo") or token.get("id_token") or {}
    issuer = str(claims.get("iss") or "")
    subject = str(claims.get("sub") or "")
    username = str(claims.get("preferred_username") or claims.get("email") or subject)
    display_name = str(claims.get("name") or username)
    email = claims.get("email")
    # An email the provider itself marks unverified must never become identity
    # data: derived-admin classification (AL-021) keys off the email domain, so
    # trusting an unverified claim would let a self-signup mint an admin
    # address. An absent flag keeps the email (matching providers that omit it).
    if claims.get("email_verified") is False:
        email = None

    if not issuer or not subject or not username:
        raise ValueError("OIDC token is missing required identity claims")

    return AuthIdentity(
        issuer=issuer,
        subject=subject,
        username=username,
        display_name=display_name,
        email=str(email) if email else None,
    )
