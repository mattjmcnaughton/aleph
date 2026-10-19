"""Write the API contract both clients generate from: ``clients/openapi.json``.

iOS TDD §4.1. Builds the app with :func:`aleph.app.create_app`, takes
``app.openapi()``, keeps only the paths under ``/api/v1`` (the ``/auth/*``
browser routes, the health probes and the e2e harness are not the contract) and
the components those paths reference, and writes the result with sorted keys,
two-space indent and a trailing newline, so a diff shows only real changes.

- ``just openapi`` writes the committed file.
- ``just openapi-check`` writes to a temp file and diffs it against the committed
  one; it runs in ``just gate-be``, so CI's ``gate`` job fails on drift.

Consumers: ``clients/web`` generates ``src/lib/api/schema.d.ts`` from this file
(``pnpm run openapi``), and from AL-602 the iOS app generates its Swift client.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aleph.app import create_app

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI

CONTRACT_PREFIX = "/api/v1/"
REF_PREFIX = "#/components/schemas/"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "clients" / "openapi.json"


def _refs(node: Any) -> set[str]:
    """Every component schema name referenced anywhere under ``node``."""
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(REF_PREFIX):
            found.add(ref.removeprefix(REF_PREFIX))
        for value in node.values():
            found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


def build_contract(app: FastAPI) -> dict[str, Any]:
    """The ``/api/v1`` slice of ``app``'s OpenAPI document."""
    document = app.openapi()
    paths = {
        path: item
        for path, item in document["paths"].items()
        if path.startswith(CONTRACT_PREFIX)
    }
    schemas = document.get("components", {}).get("schemas", {})
    keep: set[str] = set()
    frontier = _refs(paths)
    while frontier:
        name = frontier.pop()
        if name in keep:
            continue
        keep.add(name)
        frontier |= _refs(schemas[name]) - keep
    contract = {key: value for key, value in document.items() if key != "paths"}
    contract["paths"] = paths
    contract["components"] = {
        **document.get("components", {}),
        "schemas": {name: schemas[name] for name in keep},
    }
    return contract


def render(contract: dict[str, Any]) -> str:
    """The byte-stable text form: sorted keys, two-space indent, final newline."""
    return json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="where to write the document (default: clients/openapi.json)",
    )
    args = parser.parse_args(argv)
    args.output.write_text(render(build_contract(create_app())), encoding="utf-8")


if __name__ == "__main__":
    main()
