from src.logging_redaction import redact_mapping, redact_processor


def _r(d):
    return redact_processor(None, "info", dict(d))


def test_email_becomes_domain():
    out = _r({"email": "alice@acme.com"})
    assert "email" not in out
    assert out["email_domain"] == "acme.com"


def test_suffixed_email_key_becomes_domain():
    out = _r({"actor_email": "bob@corp.io"})
    assert "actor_email" not in out
    assert out["actor_email_domain"] == "corp.io"


def test_secrets_redacted():
    out = _r({"access_token": "x", "service_key": "k", "password": "p", "name": "Bob"})
    assert out["access_token"] == "[redacted]"
    assert out["service_key"] == "[redacted]"
    assert out["password"] == "[redacted]"
    assert out["name"] == "[redacted]"


def test_non_sensitive_keys_preserved():
    out = _r(
        {
            "service_name": "docu",
            "workspace_id": "ws1",
            "actor": "u1",
            "caller_service": "auth-svc",
        }
    )
    assert out == {
        "service_name": "docu",
        "workspace_id": "ws1",
        "actor": "u1",
        "caller_service": "auth-svc",
    }


def test_nested_and_list_redaction():
    out = _r({"detail": {"email": "x@y.com", "token": "t"}, "items": [{"jwt": "j"}]})
    assert out["detail"]["email_domain"] == "y.com"
    assert out["detail"]["token"] == "[redacted]"
    assert out["items"][0]["jwt"] == "[redacted]"


def test_redact_mapping_is_pure():
    src = {"email": "a@b.com"}
    redact_mapping(src)
    assert src == {"email": "a@b.com"}  # input untouched


def test_email_key_with_non_parseable_value_yields_none_domain():
    out = _r({"email": None})
    assert "email" not in out
    assert out["email_domain"] is None
    out2 = _r({"email": "not-an-email"})
    assert "email" not in out2
    assert out2["email_domain"] is None  # raw value never leaked


def test_email_key_is_case_insensitive():
    out = _r({"Email": "bob@x.com"})
    assert "Email" not in out
    assert out["Email_domain"] == "x.com"
