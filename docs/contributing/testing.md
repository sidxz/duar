# Testing

## Running Tests

```bash
cd service && uv run pytest              # Service tests
cd sdk && uv run pytest                  # SDK tests
cd service && uv run pytest -x           # Stop on first failure
cd sdk && uv run pytest tests/test_permissions.py -v   # Single file, verbose
```

---

## Test Structure

### Service Tests

```
service/tests/
├── test_idp_validator.py     # IdP validation logic
└── test_authz_jwt.py         # Authorization JWT handling
```

### SDK Tests

```
sdk/tests/
├── conftest.py               # Shared fixtures (RSA keypair, JWT helpers)
├── test_types.py             # AuthenticatedUser, WorkspaceContext
├── test_middleware.py        # JWTAuthMiddleware (Starlette TestClient)
├── test_dependencies.py      # FastAPI dependency helpers
├── test_permissions.py       # PermissionClient (httpx mocked via respx)
├── test_roles.py             # RoleClient (httpx mocked via respx)
├── test_auth.py              # Auth client
├── test_authz_client.py      # Authz client
└── test_authz_middleware.py  # Authz middleware
```

---

## Conventions

- Use `pytest.mark.asyncio` on all async test functions
- Isolate tests -- each test sets up its own data, no shared mutable state
- Test both success and failure paths (verify 401/403 for unauthorized, 422 for invalid input)
- Use descriptive names: `test_viewer_cannot_delete_workspace`
- HTTP client tests use [respx](https://lundberg.github.io/respx/) to mock httpx -- no live service needed

---

## Penetration Testing

The `pentest/` directory contains a security testing suite that runs against a live instance.

```bash
make pentest-setup     # Install tools (one-time)
make pentest           # Full suite (ZAP, Nuclei, Nikto, jwt_tool + custom scripts)
make pentest-custom    # Custom scripts only (~110 tests)
```

Reports are written to `pentest/reports/`.
