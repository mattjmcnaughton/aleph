"""The demo loader must never fall back to an application/shared database."""

import pytest

from scripts.demo_fixtures import checked_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://user:secret@db.example.com/aleph_demo_test",
        "postgresql+asyncpg://user:secret@localhost/aleph",
        "postgresql+asyncpg://user:secret@localhost/aleph_demo_test?host=db.example.com",
        "sqlite:///aleph_demo_test",
        "not a URL",
    ],
)
def test_refuses_non_demo_destinations_without_echoing_credentials(url):
    with pytest.raises(ValueError) as error:
        checked_url(url)
    assert "secret" not in str(error.value)


def test_accepts_only_explicit_loopback_demo_database():
    url = checked_url(
        "postgresql+asyncpg://postgres:local-only@127.0.0.1:5432/aleph_demo_test"
    )
    assert url.database == "aleph_demo_test"
    assert url.host == "127.0.0.1"
