"""Defense-in-depth redaction of PII and secrets from log event dicts.

The convention is "log email_domain, never email"; this processor enforces it
even when a developer forgets, and scrubs a denylist of secret-bearing keys.
"""

_REDACTED = "[redacted]"

# Exact (case-insensitive) key matches. Email is handled separately (see _is_email).
_DENY_EXACT = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "cookie",
        "set-cookie",
        "service_key",
        "api_key",
        "client_secret",
        "secret",
        "jwt",
        "name",
        "full_name",
    }
)


def _is_email_key(key: str) -> bool:
    return key == "email" or key.endswith("_email")


def _domain(value: object) -> str | None:
    if isinstance(value, str) and "@" in value:
        return value.rsplit("@", 1)[-1]
    return None


def redact_mapping(data: dict) -> dict:
    """Return a redacted shallow-rebuilt copy of ``data`` (recurses into dict/list)."""
    out: dict = {}
    for key, value in data.items():
        kl = key.lower() if isinstance(key, str) else key
        if isinstance(kl, str) and _is_email_key(kl):
            out[f"{key}_domain"] = _domain(value)
            continue  # raw email value never emitted; domain (or None) kept
        if isinstance(kl, str) and kl in _DENY_EXACT:
            out[key] = _REDACTED
            continue
        out[key] = _redact_value(value)
    return out


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def redact_processor(logger, method_name, event_dict: dict) -> dict:
    """structlog processor: redact PII/secrets. Never raises into the log path."""
    try:
        return redact_mapping(event_dict)
    except Exception:
        return event_dict
