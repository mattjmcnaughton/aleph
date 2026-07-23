"""Derived-admin classification (AL-021, TDD §7/D14).

Admin status is derived from the email domain at request time, never stored: the
email is refreshed from the identity provider on every sign-in, so the
classification self-heals and needs no migration or management UI. Pure logic —
no DB, no I/O; a plain ``User`` instance is enough to exercise it.
"""

from __future__ import annotations

from aleph.authz import is_admin
from aleph.config import Settings
from aleph.models import User

_SETTINGS = Settings(admin_email_domains="mattjmcnaughton.com")


def _user(email: str | None) -> User:
    return User(
        issuer="https://issuer.example.test",
        subject="subject-1",
        username="dev",
        display_name="Dev User",
        email=email,
    )


def test_admin_when_email_domain_matches() -> None:
    assert is_admin(_user("admin@mattjmcnaughton.com"), _SETTINGS) is True


def test_match_is_case_insensitive() -> None:
    # Providers may echo the domain (or local part) in any case; classification
    # keys on the domain compared case-insensitively.
    assert is_admin(_user("Admin@MattJMcNaughton.COM"), _SETTINGS) is True


def test_not_admin_when_domain_differs() -> None:
    assert is_admin(_user("dev@example.com"), _SETTINGS) is False


def test_not_admin_when_email_is_none() -> None:
    # The provider dropped an unverified email (see aleph.auth): with no email
    # there is no domain to trust, so the user is never an admin.
    assert is_admin(_user(None), _SETTINGS) is False


def test_not_admin_when_email_has_no_domain() -> None:
    assert is_admin(_user("not-an-email"), _SETTINGS) is False


def test_no_subdomain_or_suffix_matching() -> None:
    # Exact domain equality only: a look-alike subdomain is not the admin domain.
    assert is_admin(_user("evil@evil.mattjmcnaughton.com"), _SETTINGS) is False


def test_not_admin_with_trailing_dot_domain() -> None:
    # "user@mattjmcnaughton.com." (trailing dot) compares unequal: fail-closed.
    assert is_admin(_user("user@mattjmcnaughton.com."), _SETTINGS) is False


def test_honours_a_multi_domain_allowlist() -> None:
    settings = Settings(admin_email_domains="mattjmcnaughton.com, aleph.test")
    assert is_admin(_user("a@aleph.test"), settings) is True
    assert is_admin(_user("b@mattjmcnaughton.com"), settings) is True
    assert is_admin(_user("c@other.test"), settings) is False
