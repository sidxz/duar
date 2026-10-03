import pytest
from structlog.testing import capture_logs

from src.logging_events import log_audit, log_security


def test_log_security_shape_and_level():
    with capture_logs() as logs:
        log_security(
            "authz.token.denied", outcome="denied", reason="not_member", actor="u1"
        )
    e = logs[0]
    assert e["event"] == "authz.token.denied"
    assert e["category"] == "security"
    assert e["outcome"] == "denied"
    assert e["reason"] == "not_member"
    assert e["log_level"] == "warning"  # denied/failure -> warning


def test_log_security_success_is_info():
    with capture_logs() as logs:
        log_security("auth.login.succeeded", outcome="success", actor="u1")
    assert logs[0]["log_level"] == "info"


def test_log_security_rejects_bad_outcome():
    with pytest.raises(ValueError):
        log_security("x.y", outcome="bogus")


def test_log_audit_shape():
    with capture_logs() as logs:
        log_audit(action="workspace_created", target_type="workspace", actor="u1")
    e = logs[0]
    assert e["event"] == "audit.activity"
    assert e["category"] == "audit"
    assert e["action"] == "workspace_created"
