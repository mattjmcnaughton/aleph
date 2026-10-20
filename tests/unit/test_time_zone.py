"""The optional time-zone header is a bounded IANA name, never an error."""

from zoneinfo import ZoneInfo, reset_tzpath

import pytest

from aleph.services.time_zone import validated_time_zone


@pytest.mark.parametrize(
    "zone", ["Europe/London", "America/New_York", "UTC", "US/Eastern", "Etc/UTC"]
)
def test_valid_zone(zone: str) -> None:
    assert validated_time_zone(zone) == zone


@pytest.mark.parametrize(
    "zone", [None, "", "Mars/Olympus", "../UTC", "a" * 65, "localtime", "posixrules"]
)
def test_invalid_zone(zone: str | None) -> None:
    assert validated_time_zone(zone) is None


def test_runtime_tzdata_without_system_files() -> None:
    reset_tzpath(())
    try:
        assert ZoneInfo.no_cache("America/New_York").key == "America/New_York"
    finally:
        reset_tzpath()
