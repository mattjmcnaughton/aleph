"""Closed provider allowlist; never normalize ambiguous URLs into acceptance."""

import re
from urllib.parse import urlsplit

PROVIDER_HOSTS = (
    "push.apple.com",
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)


def is_allowed_endpoint(url: str) -> bool:
    """Accept HTTPS provider hosts only, without credentials or explicit ports.

    Provider DNS and TLS are trusted. Redirects must separately be disabled by
    the sender. Reject whitespace/backslashes before parsers can disagree.
    """
    if not url or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme == "https"
            and parsed.netloc.lower() == host
            and not parsed.fragment
            and bool(parsed.path)
            and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                for label in host.split(".")
            )
            and any(
                host == allowed or host.endswith("." + allowed)
                for allowed in PROVIDER_HOSTS
            )
        )
    except ValueError:
        return False
