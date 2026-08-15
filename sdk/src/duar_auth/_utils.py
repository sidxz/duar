"""Internal utilities for the Duar SDK."""

import logging
from urllib.parse import urlparse

_logger = logging.getLogger("duar_auth")

_SAFE_HOSTS = {"localhost", "127.0.0.1", "::1"}


def warn_if_insecure(url: str, context: str = "") -> None:
    """Log a warning if the URL uses plain HTTP on a non-localhost host."""
    parsed = urlparse(url)
    if parsed.scheme == "http" and parsed.hostname not in _SAFE_HOSTS:
        label = f" ({context})" if context else ""
        _logger.warning(
            "Duar SDK%s is connecting over plain HTTP to %s. "
            "Use HTTPS in production to protect tokens and credentials.",
            label,
            parsed.hostname,
        )
