"""Web Push API contracts, independent of persistence models."""

import uuid

from pydantic import BaseModel, Field, model_validator

from aleph.push_keys import subscription_keys


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(max_length=128)
    auth: str = Field(max_length=32)

    @model_validator(mode="after")
    def validate_keys(self) -> "SubscriptionKeys":
        subscription_keys(self.p256dh, self.auth)
        return self


class RegisterDeviceRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: SubscriptionKeys


class DeviceResponse(BaseModel):
    id: uuid.UUID


class PushConfigResponse(BaseModel):
    vapid_public_key: str


class NotificationPreferencesDTO(BaseModel):
    reminder_enabled: bool = False
    reminder_hour: int = Field(default=20, ge=0, le=23, strict=True)
    path_ready_enabled: bool = True
    brief_published_enabled: bool = True
