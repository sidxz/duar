"""Thin helpers that enforce the log envelope so call sites can't drift.

Loggers are fetched per call (no module-level cache) so structlog.testing
.capture_logs works reliably in tests.
"""

import structlog

VALID_OUTCOMES = frozenset({"success", "failure", "denied", "error", "anomaly"})


def log_security(event, *, outcome, reason=None, level=None, **fields) -> None:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    payload = {"category": "security", "outcome": outcome, **fields}
    if reason is not None:
        payload["reason"] = reason
    method = level or ("warning" if outcome in {"failure", "denied"} else "info")
    getattr(structlog.get_logger(), method)(event, **payload)


def log_audit(event="audit.activity", *, action=None, **fields) -> None:
    payload = {"category": "audit", **fields}
    if action is not None:
        payload["action"] = action
    structlog.get_logger().info(event, **payload)


def log_access(event="http.access", *, level="info", **fields) -> None:
    getattr(structlog.get_logger(), level)(event, category="access", **fields)
