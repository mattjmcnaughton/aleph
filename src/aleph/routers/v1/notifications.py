"""Authenticated Web Push registration and account preferences."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from aleph.db import get_session
from aleph.dependencies import get_current_user
from aleph.dtos.notifications import (
    DeviceResponse,
    NotificationPreferencesDTO,
    PushConfigResponse,
    RegisterDeviceRequest,
)
from aleph.errors import error_response
from aleph.models import User
from aleph.services import notifications

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]
router = APIRouter(prefix="/api/v1", tags=["notifications"])


@router.get("/push-config")
async def push_config(user: CurrentUser) -> PushConfigResponse:
    public = notifications.push_public_key()
    if public is None:
        raise HTTPException(
            404, detail={"code": "not_found", "message": "Push is not configured."}
        )
    return PushConfigResponse(vapid_public_key=public)


@router.post("/devices", response_model=DeviceResponse)
async def register_device(
    body: RegisterDeviceRequest, request: Request, user: CurrentUser, session: Session
) -> DeviceResponse | Response:
    try:
        device_id = await notifications.register_device(
            session, user.id, body, request.headers.get("user-agent")
        )
    except notifications.EndpointNotAllowedError:
        return error_response(
            request,
            status_code=422,
            code="push_endpoint_not_allowed",
            message="Push endpoint is not allowed.",
        )
    return DeviceResponse(id=device_id)


@router.delete("/devices/{device_id}", status_code=204)
async def delete_device(
    device_id: uuid.UUID, user: CurrentUser, session: Session
) -> Response:
    try:
        await notifications.delete_device(session, user.id, device_id)
    except notifications.DeviceNotFoundError as exc:
        raise HTTPException(
            404, detail={"code": "not_found", "message": "Device not found."}
        ) from exc
    return Response(status_code=204)


@router.get("/notification-preferences")
async def get_preferences(
    user: CurrentUser, session: Session
) -> NotificationPreferencesDTO:
    return await notifications.get_preferences(session, user.id)


@router.put("/notification-preferences")
async def put_preferences(
    body: NotificationPreferencesDTO, user: CurrentUser, session: Session
) -> NotificationPreferencesDTO:
    return await notifications.put_preferences(session, user.id, body)
