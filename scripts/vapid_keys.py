"""Generate a fresh VAPID pair locally; never uploads or provisions secrets."""

from cryptography.hazmat.primitives.asymmetric import ec

from aleph.push_keys import encode_key, public_bytes


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    print(
        "VAPID_PRIVATE_KEY="
        + encode_key(key.private_numbers().private_value.to_bytes(32))
    )
    print("VAPID_PUBLIC_KEY=" + encode_key(public_bytes(key.public_key())))


if __name__ == "__main__":
    main()
