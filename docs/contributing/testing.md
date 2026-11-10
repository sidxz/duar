# Testing

## Running Tests

```bash
cd service && uv run pytest              # Service tests
cd sdk && uv run pytest                  # SDK tests
cd service && uv run pytest -x           # Stop on first failure
cd sdk && uv run pytest tests/test_permissions.py -v   # Single file, verbose
```

### System tests

```bash
uv sync --all-packages --extra dev   # once
make system-test                     # needs Docker, Node and pnpm
```

`make system-test` boots `docker-compose.prod.yml` in Docker from an empty database and drives it from outside, the way a browser, a backend and the SDKs would. A small overlay (`docker-compose.system-test.yml`) adds what a test run needs around the production file: locally built images, [Dex](https://dexidp.io/) as a throwaway OIDC provider to sign in against, and a TLS reverse proxy in front. Keys, certificates and passwords are generated for the run; nothing touches `./keys`, `.env.prod` or the dev containers.

The run covers:

- **Deployment**: the images build, migrations apply from zero, every container reports healthy, the service starts with `DEBUG=false`.
- **Service flows over HTTPS**: admin and user sign-in through Dex (authorization code + PKCE), refresh rotation and reuse detection, logout, deactivation, the admin API through the admin panel's own proxy, rate limiting at a production threshold, and 4xx for bad input.
- **Python SDK**: a FastAPI app protected by `Duar` in AuthZ mode and in proxy mode, covering workspace roles, custom roles and per-resource sharing, plus realm machine tokens.
- **JS SDKs**: packed with `npm pack`, installed with pnpm, then used for sign-in, token verification, the permission and role clients, machine tokens and the AuthZ-mode client through the `@duar-auth/nextjs/proxy` helper.
- **Service log**: the run fails if the service logged a 5xx response or an unexpected error.

It takes a few minutes, most of it image builds. To iterate, keep the stack up and re-run only what you are working on:

```bash
KEEP=1 system-tests/run.sh                     # leaves the stack running
SYSTEM_TEST_DIR=$PWD/system-tests/.work SSL_CERT_FILE=$PWD/system-tests/.work/tls/ca.crt \
  uv run pytest system-tests -k refresh        # re-run a few tests against it
system-tests/run.sh down                       # tear it down
```

The service's rate limits are live, so a second full run within a minute of the first is throttled; wait a minute or start from a fresh stack. Logs of the last run are in `system-tests/.work/stack.log`.

---

## Test Structure

### Service Tests

```
service/tests/
├── test_idp_validator.py     # IdP validation logic
└── test_authz_jwt.py         # Authorization JWT handling
```

`tests/route_table.txt` lists every route with the credential it requires and the rate limit declared on it. `tests/test_route_table.py` fails when the app and the file disagree, so a new endpoint or a changed auth dependency has to be acknowledged:

```bash
cd service && UPDATE_ROUTE_TABLE=1 uv run pytest tests/test_route_table.py
```

A route with no auth dependency also has to be added to the `NO_AUTH_DEPENDENCY` list in that test.

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
