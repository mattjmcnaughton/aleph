"""Authentication API DTOs (AL-021, TDD §7).

The session payload the SPA root ``beforeLoad`` consumes on every load, signed
in or out (the frontend contract in ``web/frontend/src/lib/api.ts``).
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from aleph.dtos.settings import SettingsDTO


class UserDTO(BaseModel):
    # ``model_allowlist`` starts with the ``model_`` prefix pydantic protects by
    # default; the frontend contract fixes the wire name, so opt out of the
    # protected namespace rather than rename the field.
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    username: str
    display_name: str
    # ``None`` when the IdP reported ``email_verified: false`` (TDD §7); the
    # unverified email is dropped before it is stored so it can never mint admin.
    email: str | None = None
    # Derived from the email domain at request time (see ``aleph.authz``), never
    # stored; admin-only UI (the model picker) keys off this.
    is_admin: bool = False
    # Admin model-picker options: bare OpenRouter model-id strings from the
    # ``MODEL_ALLOWLIST`` (TDD §14/D14). Populated for admins, ``[]`` for
    # everyone else.
    model_allowlist: list[str] = []
    # The learner's resolved feature flags (AL-203): every flag in the code
    # registry mapped to its effective value for *this* user. The resolution
    # order is stated once, in ``aleph.services.feature_flags``' module
    # docstring. Delivered on the session probe rather than a route of its own so
    # gating a surface costs no extra request — the SPA already fetches this
    # payload on every load. Keys outside the registry never appear, so a stale
    # row cannot invent a flag.
    feature_flags: dict[str, bool] = {}
    # The learner's effective Settings (CONTEXT.md: Settings), delivered on
    # the session probe for the same reason the flag map is: the lesson
    # view honours Auto-draft on open with no second request. ``PATCH
    # /settings`` is the one write path; the SPA folds its response back
    # into this cached payload.
    settings: SettingsDTO = SettingsDTO()


class SessionDTO(BaseModel):
    authenticated: bool
    provider: str
    user: UserDTO | None = None
