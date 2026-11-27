"""API key DTOs (``/api/v1/api-keys``)."""

from __future__ import annotations

import datetime  # noqa: TC003 - pydantic resolves annotations at runtime.
import uuid  # noqa: TC003 - pydantic resolves annotations at runtime.
from typing import Annotated

from pydantic import BaseModel, StringConstraints

KeyNameStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


class CreateApiKeyRequest(BaseModel):
    name: KeyNameStr


class ApiKeyDTO(BaseModel):
    """A key as listed: never its secret or digest."""

    id: uuid.UUID
    name: str
    created_at: datetime.datetime
    revoked_at: datetime.datetime | None


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyDTO]


class CreatedApiKeyResponse(BaseModel):
    """``key`` is the secret, in this response only."""

    id: uuid.UUID
    name: str
    key: str
