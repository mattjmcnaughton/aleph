"""The Swift generator keeps admin tools out of the iOS client (iOS TDD §4.2, D1).

``clients/ios/Packages/API/openapi-generator-config.yaml`` tells
swift-openapi-generator which operations to generate. Its ``filter`` is
include-only, so the config lists every learner tag; the admin surface
(feature-flag management under ``/api/v1/admin/``) is the parity exception and is
left out. These tests pin that list against ``clients/openapi.json`` so a new tag
can neither be dropped from the iOS client silently nor let admin tools in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "clients" / "openapi.json"
CONFIG = ROOT / "clients" / "ios" / "Packages" / "API" / "openapi-generator-config.yaml"

# Tags whose operations are admin tools: web-only by CLAUDE.md's "Two Clients" rule.
ADMIN_TAGS = {"feature-flags"}
ADMIN_PREFIX = "/api/v1/admin/"


def _operations(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (path, op) for path, ops in contract["paths"].items() for op in ops.values()
    ]


def _filter_list(config: str, key: str) -> set[str]:
    """The ``- item`` entries under ``filter: <key>:`` in the (simple) YAML config."""
    block = re.search(
        rf"^filter:\n(?:  .*\n)*?  {key}:\n((?:    - .*\n)+)", config, re.MULTILINE
    )
    assert block, f"filter.{key} not found in {CONFIG.name}"
    return {line.strip()[2:].strip() for line in block.group(1).splitlines()}


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return json.loads(CONTRACT.read_text())


@pytest.fixture(scope="module")
def config() -> str:
    return CONFIG.read_text()


def test_config_includes_every_learner_tag_and_no_admin_tag(
    contract: dict[str, Any], config: str
) -> None:
    contract_tags = {tag for _, op in _operations(contract) for tag in op["tags"]}
    assert _filter_list(config, "tags") == contract_tags - ADMIN_TAGS


def test_admin_routes_carry_only_admin_tags(contract: dict[str, Any]) -> None:
    admin_ops = [
        op for path, op in _operations(contract) if path.startswith(ADMIN_PREFIX)
    ]
    assert admin_ops, "expected the admin surface in the contract"
    for op in admin_ops:
        assert set(op["tags"]) <= ADMIN_TAGS, op["operationId"]


def test_learner_routes_carry_no_admin_tag(contract: dict[str, Any]) -> None:
    for path, op in _operations(contract):
        if not path.startswith(ADMIN_PREFIX):
            assert not set(op["tags"]) & ADMIN_TAGS, op["operationId"]


def test_config_generates_a_package_scoped_client(config: str) -> None:
    assert re.search(r"^generate:\n  - types\n  - client\n", config, re.MULTILINE)
    assert re.search(r"^accessModifier: package$", config, re.MULTILINE)
