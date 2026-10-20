"""Independent wire expectations from RFC8291 Appendix A and endpoint policy."""

import json
import re
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from aleph.config import Settings
from aleph.domains.notification_copy import (
    brief_published_message,
    path_ready_message,
    reminder_message,
)
from aleph.domains.push_endpoint import is_allowed_endpoint
from aleph.models import Device
from aleph.push_keys import decode_key, encode_key, public_bytes
from aleph.services.push import (
    NullPushSender,
    PushResult,
    WebPushSender,
    encrypt_payload,
)

PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx"
    "aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)


def device(endpoint="https://push.apple.com/test"):
    return Device(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        endpoint=endpoint,
        p256dh=PUBLIC,
        auth="BTBZMqHH6r4Tts7J_aSIgg",
    )


@pytest.mark.parametrize(
    "host",
    [
        "push.apple.com",
        "web.push.apple.com",
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "wns.notify.windows.com",
    ],
)
def test_allowed(host):
    assert is_allowed_endpoint(f"https://{host}/subscription")


@pytest.mark.parametrize(
    "url",
    [
        "http://push.apple.com/x",
        "https://evilpush.apple.com/x",
        "https://fcm.googleapis.com.evil.example/x",
        "https://push.apple.com:443/x",
        "https://user@push.apple.com/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://push.apple.com./x",
        "https://push.apple.com/x#fragment",
        "https://push.apple.com\\@evil.example/x",
        " https://push.apple.com/x",
    ],
)
def test_rejected(url):
    assert not is_allowed_endpoint(url)


def test_rfc8291_appendix_a():
    # Published input and final ciphertext, not a round trip through our own code.
    private = ec.derive_private_key(
        int.from_bytes(decode_key("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw")),
        ec.SECP256R1(),
    )
    encrypted = encrypt_payload(
        b"When I grow up, I want to be a watermelon",
        decode_key(
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
        ),
        decode_key("BTBZMqHH6r4Tts7J_aSIgg"),
        private_key=private,
        salt=decode_key("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    assert encrypted == decode_key(
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,result",
    [
        (201, PushResult.DELIVERED),
        (404, PushResult.GONE),
        (410, PushResult.GONE),
        (200, PushResult.ERROR),
        (413, PushResult.ERROR),
        (429, PushResult.ERROR),
        (500, PushResult.ERROR),
        (307, PushResult.ERROR),
    ],
)
async def test_transport_response_and_vapid(status, result):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        sender = WebPushSender(
            PRIVATE, "https://aleph.example", client=client, clock=lambda: 1000
        )
        assert (
            await sender.send(device(), path_ready_message("abc", "Algebra", "ready"))
            == result
        )
    assert len(requests) == 1  # No redirect, and no retries, even for 429/500.
    request = requests[0]
    assert request.headers["TTL"] == "3600"
    assert request.headers["Urgency"] == "normal"
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", request.headers["Topic"])
    token, key = request.headers["Authorization"].removeprefix("vapid t=").split(", k=")
    header, claims, signature = token.split(".")
    assert json.loads(decode_key(header))["alg"] == "ES256"
    assert json.loads(decode_key(claims)) == {
        "aud": "https://push.apple.com",
        "sub": "https://aleph.example",
        "exp": 44200,
    }
    raw = decode_key(signature)
    # Verify JWS with an independent cryptography primitive, not our JWT encoder.
    public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), decode_key(key)
    )
    public.verify(
        encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:])),
        f"{header}.{claims}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    assert request.content[16:21] == b"\0\0\x10\0\x41"
    assert request.content[21:86] != decode_key(key)  # Ephemeral ECDH != signing key.


@pytest.mark.anyio
async def test_tokens_cached_by_audience_and_refreshed_and_randomness_fresh():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(201)

    now = [1000]
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        sender = WebPushSender(
            PRIVATE, "https://aleph.example", client=client, clock=lambda: now[0]
        )
        msg = reminder_message("2026-10-20")
        await sender.send(device(), msg)
        await sender.send(device(), msg)
        await sender.send(device("https://fcm.googleapis.com/test"), msg)
        now[0] += 11 * 3600
        await sender.send(device(), msg)
    assert requests[0].headers["Authorization"] == requests[1].headers["Authorization"]
    assert requests[0].content[:16] != requests[1].content[:16]
    assert requests[0].content[21:86] != requests[1].content[21:86]
    assert requests[0].headers["Authorization"] != requests[2].headers["Authorization"]
    assert requests[0].headers["Authorization"] != requests[3].headers["Authorization"]
    assert requests[0].headers["TTL"] == "21600"


@pytest.mark.anyio
async def test_invalid_stored_subscriptions_and_disabled_never_send():
    def unexpected(request):
        pytest.fail("Network attempted")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        sender = WebPushSender(PRIVATE, "https://aleph.example", client=client)
        for url in (
            "https://evilpush.apple.com/x",
            "https://push.apple.com:444/x",
            "http://push.apple.com/x",
            "https://user@push.apple.com/x",
            "https://127.0.0.1/x",
        ):
            assert (
                await sender.send(device(url), reminder_message("today"))
                == PushResult.ERROR
            )
        invalid = device()
        invalid.p256dh = encode_key(b"\x04" + b"\x00" * 64)
        assert await sender.send(invalid, reminder_message("today")) == PushResult.ERROR
        assert (
            await NullPushSender().send(device(), reminder_message("today"))
            == PushResult.DISABLED
        )


@pytest.mark.anyio
async def test_network_error_is_not_success():
    def unavailable(request):
        raise httpx.ConnectError("unavailable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        sender = WebPushSender(PRIVATE, "https://aleph.example", client=client)
        assert (
            await sender.send(device(), reminder_message("today")) == PushResult.ERROR
        )


def test_configuration_derives_public_key_and_rejects_mismatch():
    config = Settings(
        vapid_private_key=PRIVATE, public_base_url="https://aleph.example"
    )
    expected = encode_key(
        public_bytes(
            ec.derive_private_key(
                int.from_bytes(decode_key(PRIVATE)), ec.SECP256R1()
            ).public_key()
        )
    )
    assert config.vapid_public_key == expected
    assert PRIVATE not in repr(config)
    with pytest.raises(ValueError, match="does not match"):
        Settings(
            vapid_private_key=PRIVATE,
            vapid_public_key=PUBLIC,
            public_base_url="https://aleph.example",
        )
    with pytest.raises(ValueError, match="HTTPS origin"):
        Settings(vapid_private_key=PRIVATE, public_base_url="http://aleph.example")


def test_copy_targets_and_limits():
    assert reminder_message("today", streak=5).body == "Keep your 5-day streak going."
    assert reminder_message("today").body == "Pick up where you left off."
    message = brief_published_message("abc", "Research", 7)
    assert message.body == "Research: Brief #7 is ready."
    assert message.target == "/briefs/abc"
    assert message.ttl == 86400
    assert path_ready_message("abc", "Title", "failed").target == "/paths/abc"
    assert len(path_ready_message("abc", "x" * 10000, "ready").body) < 250


@pytest.mark.anyio
async def test_secret_endpoint_is_not_exported_to_telemetry(capfire):
    import logfire

    endpoint = "https://push.apple.com/subscription-secret-must-not-be-exported"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(201))
    ) as client:
        logfire.instrument_httpx(client)
        sender = WebPushSender(PRIVATE, "https://aleph.example", client=client)
        assert (
            await sender.send(device(endpoint), reminder_message("today"))
            == PushResult.DELIVERED
        )
    assert "subscription-secret-must-not-be-exported" not in repr(
        capfire.exporter.exported_spans_as_dict()
    )


def test_key_utility_prints_a_matching_fresh_pair(capsys):
    from aleph.push_keys import load_private_key
    from scripts.vapid_keys import main

    main()
    pair = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    private = load_private_key(pair["VAPID_PRIVATE_KEY"])
    assert pair["VAPID_PUBLIC_KEY"] == encode_key(public_bytes(private.public_key()))
    main()
    assert pair["VAPID_PRIVATE_KEY"] not in capsys.readouterr().out


@pytest.mark.anyio
async def test_httpx_info_logs_do_not_expose_push_capabilities(caplog):
    import logging

    endpoint = "https://push.apple.com/private-capability-marker"
    with caplog.at_level(logging.INFO, logger="httpx"):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(201))
        ) as client:
            sender = WebPushSender(PRIVATE, "https://aleph.example", client=client)
            await sender.send(device(endpoint), reminder_message("today"))
            await client.get("https://example.org/ordinary-request")
    assert "private-capability-marker" not in caplog.text
    assert "ordinary-request" in caplog.text


def test_key_validation_contracts_do_not_depend_on_services():
    import ast
    import inspect

    import aleph.config
    import aleph.dtos.notifications

    for module in (aleph.config, aleph.dtos.notifications):
        imports = [
            node.module
            for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        assert not any(name.startswith("aleph.services") for name in imports)
