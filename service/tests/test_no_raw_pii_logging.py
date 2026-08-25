import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_authz_routes_log_no_raw_email():
    text = (SRC / "api" / "authz_routes.py").read_text()
    # The two known leaks (email_conflict ~:259, inactive_user ~:267) must be gone.
    assert "email=idp_claims" not in text
    assert "email=user.email" not in text


def test_onboard_routes_never_log_the_code():
    """The bearer code must never reach a log_security/log_activity call."""
    src = (SRC / "api" / "onboard_routes.py").read_text()
    assert "code_hash" not in src
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in ("log_security", "log_activity"):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        assert "code" not in names, ast.unparse(node)
