"""Feature-flag admin API DTOs (AL-203).

Separate from the ORM model as always: the wire shape describes *flags* (a
code-defined registry entry plus its effective default and override count),
while the table stores only per-user exceptions.
"""

from uuid import UUID

from pydantic import BaseModel


class FeatureFlagDTO(BaseModel):
    """One code-defined flag: its effective default and per-user override count.

    ``enabled_default`` is the **global** default (code default with
    ``FEATURE_FLAG_DEFAULTS`` applied) — deliberately not the admin baseline,
    which is a resolution-time property of the reader, not of the flag.
    """

    key: str
    enabled_default: bool
    override_count: int


class FeatureFlagListDTO(BaseModel):
    flags: list[FeatureFlagDTO]


class FeatureFlagOverrideSetDTO(BaseModel):
    enabled: bool


class FeatureFlagOverrideDTO(BaseModel):
    flag_key: str
    user_id: UUID
    enabled: bool
