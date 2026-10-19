"""Re-mint the iOS UI tests' pre-signed tokens (iOS TDD D22, §16.3).

    uv run python scripts/mint_e2e_tokens.py               # re-sign, committed key
    uv run python scripts/mint_e2e_tokens.py --rotate-key  # new key, then re-sign

Writes ``clients/ios/AlephUITests/Fixtures/tokens.json``: a learner, an admin and
an already-expired learner token, signed RS256 by the fake issuer's committed,
**test-only** key (``tests/fixtures/e2e_oidc/signing_key.jwk``). Every claim is
fixed, so re-running without ``--rotate-key`` reproduces the file exactly;
``tests/unit/test_e2e_fake_issuer.py`` fails if the two drift apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from joserfc.jwk import RSAKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.e2e_fake_issuer import (  # noqa: E402 - needs the repo root on sys.path.
    SIGNING_KEY_ID,
    SIGNING_KEY_PATH,
    TOKENS_PATH,
    load_signing_key,
    mint_tokens,
    render_tokens,
)


def rotate_key(path: Path = SIGNING_KEY_PATH) -> None:
    key = RSAKey.generate_key(2048, parameters={"kid": SIGNING_KEY_ID}, private=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(key.as_dict(private=True), indent=2, sort_keys=True) + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--rotate-key",
        action="store_true",
        help="generate a new test-only signing key before minting",
    )
    args = parser.parse_args(argv)
    if args.rotate_key:
        rotate_key()
    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(render_tokens(mint_tokens(load_signing_key())))
    print(f"wrote {TOKENS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
