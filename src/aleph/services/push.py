"""RFC8291 encryption and RFC8292 VAPID transport; never retries or redirects.

https://www.rfc-editor.org/rfc/rfc8291#section-3.4
https://www.rfc-editor.org/rfc/rfc8292#section-2
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

import httpx
import logfire
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from joserfc import jwt
from joserfc.jwk import ECKey

from aleph.domains.push_endpoint import is_allowed_endpoint
from aleph.push_keys import (
    encode_key,
    load_private_key,
    public_bytes,
    subscription_keys,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from aleph.domains.notification_copy import PushMessage
    from aleph.models import Device


class _PushURLFilter(logging.Filter):
    """Redact HTTPX request URLs before any stdlib handler can export them.

    Permanent policy, not a logger-level toggle across concurrent awaits.
    HTTPX supplies the URL as its second format argument.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.msg == 'HTTP Request: %s %s "%s %d %s"'
            and isinstance(record.args, tuple)
            and len(record.args) == 5
            and is_allowed_endpoint(str(record.args[1]))
        ):
            record.args = (record.args[0], "[REDACTED push endpoint]", *record.args[2:])
        return True


logging.getLogger("httpx").addFilter(_PushURLFilter())


def encrypt_payload(
    payload: bytes,
    public: bytes,
    auth: bytes,
    *,
    private_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """Single aes128gcm record, rs4096 (RFC8291 §§3.4,4 / RFC8188 §2).

    Explicit randomness inputs permit the independent RFC Appendix A vector.
    Production always generates a fresh ephemeral key and salt for each send.
    """
    if len(payload) > 3993:
        raise ValueError("Push payload exceeds one Web Push record")
    private_key = private_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt if salt is not None else os.urandom(16)
    if len(salt) != 16 or len(auth) != 16:
        raise ValueError("Invalid salt or authentication secret")
    sender_public = public_bytes(private_key.public_key())
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    shared = private_key.exchange(ec.ECDH(), receiver)
    ikm = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=auth,
        info=b"WebPush: info\0" + public + sender_public,
    ).derive(shared)
    cek = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt,
        info=b"Content-Encoding: aes128gcm\0",
    ).derive(ikm)
    nonce = HKDF(
        algorithm=hashes.SHA256(),
        length=12,
        salt=salt,
        info=b"Content-Encoding: nonce\0",
    ).derive(ikm)
    return (
        salt
        + (4096).to_bytes(4)
        + bytes([65])
        + sender_public
        + AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    )


class PushResult(StrEnum):
    DELIVERED = "delivered"  # Accepted by push service, not proof of phone delivery.
    GONE = "gone"
    ERROR = "error"
    DISABLED = "disabled"


class PushSender(Protocol):
    async def send(self, device: Device, message: PushMessage) -> PushResult: ...


class NullPushSender:
    async def send(self, device: Device, message: PushMessage) -> PushResult:
        return PushResult.DISABLED


@dataclass(frozen=True)
class RecordedPush:
    device_id: str
    account_id: str
    payload: dict


class InMemoryPushSender:
    def __init__(
        self,
        base_url: str = "https://aleph.example",
        result: PushResult = PushResult.DELIVERED,
    ) -> None:
        self.base_url = base_url
        self.result = result
        self.pushes: list[RecordedPush] = []

    async def send(self, device: Device, message: PushMessage) -> PushResult:
        self.pushes.append(
            RecordedPush(
                str(device.id), str(device.user_id), message.payload(self.base_url)
            )
        )
        return self.result


class WebPushSender:
    def __init__(
        self,
        private_key: str,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._private = load_private_key(private_key)
        self.public_key = encode_key(public_bytes(self._private.public_key()))
        self._jwt_key = ECKey.import_key(
            self._private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self._base_url = base_url
        self._client = client
        self._clock = clock
        self._tokens: dict[str, tuple[int, str]] = {}

    def _token(self, audience: str) -> str:
        now = int(self._clock())
        cached = self._tokens.get(audience)
        if cached is None or cached[0] - now <= 3600:
            expiry = now + 12 * 3600
            token = jwt.encode(
                {"alg": "ES256", "typ": "JWT"},
                {"aud": audience, "sub": self._base_url, "exp": expiry},
                self._jwt_key,
            )
            self._tokens[audience] = expiry, token
            return token
        return cached[1]

    async def send(self, device: Device, message: PushMessage) -> PushResult:
        # Revalidate persisted data before any crypto or network activity.
        if not is_allowed_endpoint(device.endpoint):
            return PushResult.ERROR
        try:
            import json

            public, auth = subscription_keys(device.p256dh, device.auth)
            payload = json.dumps(
                message.payload(self._base_url),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            encrypted = encrypt_payload(payload, public, auth)
            audience = "https://" + str(urlsplit(device.endpoint).hostname)
            headers = {
                "Authorization": (
                    f"vapid t={self._token(audience)}, k={self.public_key}"
                ),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": str(message.ttl),
                "Urgency": "normal",
                "Topic": encode_key(hashlib.sha256(message.tag.encode()).digest()[:24]),
            }
            # Subscription URLs are capabilities, not safe HTTP trace metadata.
            with logfire.suppress_instrumentation():
                if self._client is not None:
                    response = await self._client.post(
                        device.endpoint,
                        content=encrypted,
                        headers=headers,
                        follow_redirects=False,
                        timeout=10,
                    )
                else:
                    async with httpx.AsyncClient(trust_env=False) as client:
                        response = await client.post(
                            device.endpoint,
                            content=encrypted,
                            headers=headers,
                            follow_redirects=False,
                            timeout=10,
                        )
        except (ValueError, httpx.HTTPError):
            # Do not log exceptions: HTTP errors contain secret endpoint URLs.
            return PushResult.ERROR
        if response.status_code == 201:
            return PushResult.DELIVERED
        if response.status_code in (404, 410):
            return PushResult.GONE
        return PushResult.ERROR
