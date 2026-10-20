"""Web Push key encoding and validation, independent of transport and settings."""

import base64
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def encode_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_key(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("Invalid base64url key")
    raw = base64.b64decode(
        value.rstrip("=") + "=" * (-len(value.rstrip("=")) % 4),
        altchars=b"-_",
        validate=True,
    )
    if encode_key(raw) != value.rstrip("="):
        raise ValueError("Noncanonical base64url key")
    return raw


def subscription_keys(p256dh: str, auth: str) -> tuple[bytes, bytes]:
    public, secret = decode_key(p256dh), decode_key(auth)
    if len(public) != 65 or public[0] != 4 or len(secret) != 16:
        raise ValueError(
            "Expected uncompressed P-256 public key and 16-byte auth secret"
        )
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    return public, secret


def public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def load_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    if value.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(value.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError("VAPID requires a P-256 private key")
        return key
    raw = decode_key(value)
    if len(raw) != 32:
        raise ValueError("VAPID requires a 32-byte P-256 scalar")
    return ec.derive_private_key(int.from_bytes(raw), ec.SECP256R1())
