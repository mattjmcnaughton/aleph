"""Authentication API DTOs (AL-021, TDD §7).

The session payload the SPA root ``beforeLoad`` consumes on every load, signed
in or out (the frontend contract in ``web/frontend/src/lib/api.ts``).
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


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


class SessionDTO(BaseModel):
    authenticated: bool
    provider: str
    user: UserDTO | None = None
