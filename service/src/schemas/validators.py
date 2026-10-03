"""Shared Pydantic types and validators for input sanitization."""

from typing import Annotated

import nh3
from pydantic import AfterValidator


def strip_html(value: str) -> str:
    """Remove HTML tags from a string using nh3 (Rust-based sanitizer)."""
    if "<" not in value:
        return value.strip()
    return nh3.clean(value, tags=set()).strip()


def strip_html_optional(value: str | None) -> str | None:
    if value is None:
        return None
    return strip_html(value)


SafeStr = Annotated[str, AfterValidator(strip_html)]
SafeStrOptional = Annotated[str | None, AfterValidator(strip_html_optional)]


def strip_html_required(value: str) -> str:
    """Like ``strip_html`` but rejects values that are empty after sanitization.

    A plain ``min_length`` runs BEFORE the strip, so ``"  "`` or ``"<b></b>"``
    would pass it and then collapse to ``""``. Validate AFTER stripping instead so
    a name can never be blanked to the empty string.
    """
    cleaned = strip_html(value)
    if not cleaned:
        raise ValueError("must not be empty")
    return cleaned


def strip_html_required_optional(value: str | None) -> str | None:
    if value is None:
        return None
    return strip_html_required(value)


NonEmptySafeStr = Annotated[str, AfterValidator(strip_html_required)]
NonEmptySafeStrOptional = Annotated[
    str | None, AfterValidator(strip_html_required_optional)
]

# Single source of truth for slugs (org + workspace): lowercase letters, digits,
# and hyphens, no leading/trailing hyphen, minimum two characters.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*[a-z0-9]$"
# Realm slugs are letter-start (they stand in for ``service_name``, which is
# letter-start), unlike the org/workspace SLUG_PATTERN above which allows a digit start.
REALM_SLUG_PATTERN = r"^[a-z][a-z0-9-]*[a-z0-9]$"


def sanitize_url(value: str | None) -> str | None:
    """Allow only http(s) URLs. Blocks javascript:, data:, and other schemes."""
    if value is None:
        return None
    if not value.startswith(("https://", "http://")):
        return None
    return value


SafeUrl = Annotated[str | None, AfterValidator(sanitize_url)]


def validate_redirect_uri(uri: str) -> str:
    """Strictly validate a redirect URI.

    Requires http(s) scheme, non-empty host, no userinfo/fragment, and round-trips
    through urlparse cleanly. Path is allowed; query string is not (OAuth appends
    its own). Rejects wildcards and common shapes used for origin confusion
    (``https://good@evil.com/...``, ``http://``, bare hosts).
    """
    from urllib.parse import urlparse, urlunparse

    if not isinstance(uri, str) or not uri:
        raise ValueError("redirect URI must be a non-empty string")
    parsed = urlparse(uri.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Invalid redirect URI scheme: {uri!r}")
    if "@" in parsed.netloc:
        raise ValueError(f"Redirect URI must not contain userinfo: {uri!r}")
    if not parsed.hostname:
        raise ValueError(f"Redirect URI must have a host: {uri!r}")
    if parsed.fragment:
        raise ValueError(f"Redirect URI must not contain a fragment: {uri!r}")
    if parsed.query:
        raise ValueError(f"Redirect URI must not contain a query string: {uri!r}")
    # Reject wildcards / placeholders
    if "*" in uri or uri.lower() in {"null", "http://", "https://"}:
        raise ValueError(f"Invalid redirect URI: {uri!r}")
    # Normalize — reject values that don't round-trip
    roundtripped = urlunparse(parsed)
    if roundtripped != uri.strip():
        raise ValueError(f"Malformed redirect URI: {uri!r}")
    return uri.strip()


def validate_origin_string(origin: str) -> str:
    """Strictly validate an Origin (scheme://host[:port]).

    Rejects paths, query strings, fragments, wildcards, ``null``, bare hostnames.
    """
    from urllib.parse import urlparse, urlunparse

    if not isinstance(origin, str) or not origin:
        raise ValueError("origin must be a non-empty string")
    parsed = urlparse(origin.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Invalid origin scheme: {origin!r}")
    if "@" in parsed.netloc:
        raise ValueError(f"Origin must not contain userinfo: {origin!r}")
    if not parsed.hostname:
        raise ValueError(f"Origin must have a host: {origin!r}")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError(f"Origin must not contain path/query/fragment: {origin!r}")
    if "*" in origin or origin.lower() == "null":
        raise ValueError(f"Invalid origin: {origin!r}")
    # Require exact round-trip with no path
    canonical = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    if canonical != origin.strip():
        raise ValueError(f"Malformed origin: {origin!r}")
    return origin.strip()
