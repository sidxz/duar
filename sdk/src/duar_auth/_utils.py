"""Internal utilities for the Duar SDK."""

import asyncio
import logging
from urllib.parse import urlparse

import httpx

_logger = logging.getLogger("duar_auth")


class LoopLocalClient:
    """An ``httpx.AsyncClient`` bound to the event loop that is running now.

    A pooled connection belongs to the loop that opened it, so a client reused on a
    later loop (``asyncio.run(...)`` at startup, then a server's own loop) fails its
    first request with "Event loop is closed". ``get()`` hands out a fresh client
    whenever the running loop has changed; the old one is dropped, since its loop is
    gone and it cannot be closed from here. ``aclose()`` likewise only closes a client
    owned by the running loop.
    """

    def __init__(self, **httpx_kwargs):
        self._kwargs = httpx_kwargs
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def get(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop is not loop:
            self._client = httpx.AsyncClient(**self._kwargs)
            self._loop = loop
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and not client.is_closed and self._loop is asyncio.get_running_loop():
            await client.aclose()


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
