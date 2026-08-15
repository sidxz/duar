import json
import logging

import pytest
import structlog

from src.config import Settings, settings
from src.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging() mutates process-wide logging (root handlers, structlog
    config, uvicorn/sqlalchemy loggers). Snapshot and restore so these tests can't
    leak state into the rest of the suite (order-dependent coupling)."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_structlog = structlog.get_config()
    uvicorn_access = logging.getLogger("uvicorn.access")
    saved_uvicorn_handlers = uvicorn_access.handlers[:]
    saved_uvicorn_propagate = uvicorn_access.propagate
    saved_sqla_level = logging.getLogger("sqlalchemy.engine").level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        structlog.configure(**saved_structlog)
        uvicorn_access.handlers[:] = saved_uvicorn_handlers
        uvicorn_access.propagate = saved_uvicorn_propagate
        logging.getLogger("sqlalchemy.engine").setLevel(saved_sqla_level)


def test_logging_defaults():
    s = Settings()
    assert s.log_level == "INFO"
    assert s.log_format == "json"
    assert s.log_pii_redaction is True


def test_environment_property(monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    assert settings.environment == "dev"
    monkeypatch.setattr(settings, "debug", False)
    assert settings.environment == "prod"


def test_configure_emits_json_envelope(capsys, monkeypatch):
    monkeypatch.setattr(settings, "log_format", "json")
    monkeypatch.setattr(settings, "log_level", "INFO")
    configure_logging()
    structlog.get_logger().info("test.event", foo="bar")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "test.event"
    assert rec["level"] == "info"
    assert rec["service"] == "duar"
    assert rec["version"]
    assert rec["ts"]
    assert rec["foo"] == "bar"


def test_migrations_do_not_clobber_app_logging(monkeypatch):
    """Startup migrations must not take the app's logging config down with them.

    Regression: ``migrations/env.py`` calls ``fileConfig(alembic.ini)``, which
    REPLACES root handlers and resets the root level to alembic.ini's ``WARN``.
    ``main.py`` runs migrations in-process right after ``configure_logging()``, so
    every boot silently lost JSON rendering and dropped all info-level events —
    i.e. every 2xx access log — for the life of the process. main.py opts out via
    ``config.attributes["configure_logger"]``; this asserts env.py honors it.

    Offline mode (``sql=True``) loads env.py and emits SQL without a database.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    monkeypatch.setattr(settings, "log_level", "INFO")
    configure_logging()
    handlers = logging.getLogger().handlers[:]

    service_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(service_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(service_root / "migrations"))
    cfg.attributes["configure_logger"] = False  # what main.py._migrate sets
    command.upgrade(cfg, "head", sql=True)

    assert logging.getLogger().handlers == handlers, (
        "alembic replaced the app's log handlers — structured JSON logging is gone "
        "for the rest of the process"
    )
    assert logging.getLogger().level == logging.INFO, (
        "alembic reset the root log level — info events (all 2xx access logs) are "
        "being dropped"
    )


def test_configure_redacts_email_end_to_end(capsys, monkeypatch):
    monkeypatch.setattr(settings, "log_format", "json")
    monkeypatch.setattr(settings, "log_pii_redaction", True)
    configure_logging()
    structlog.get_logger().warning("authz.token.denied", email="a@acme.com")
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "email" not in rec
    assert rec["email_domain"] == "acme.com"
