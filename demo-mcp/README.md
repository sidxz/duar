# demo-mcp — a FastMCP server authorized by Duar

Shows **Duar MCP**: a human-driven agent calls MCP tools on our own FastMCP server, and
the server authorizes each call against Duar using the existing **dual-auth tier**
(the server's service key + the user's `duar:access` token).

- **Gate 1 (authn):** FastMCP `JWTVerifier` validates the user token against Duar's JWKS
  (verify-only — *not* an OAuth `RemoteAuthProvider`, so Duar stays out of OAuth-AS land).
- **Gate 2 (authz):** `DuarAuthMiddleware` calls `/roles/check-action` for the tool's
  action and, for `@duar_resource`-tagged tools, `/permissions/check` on the resource.
  Fail-closed: any missing identity or Duar error denies the call.

Tools:
- `list_reports` — RBAC-only (needs the `list_reports` action).
- `read_document(doc_id)` — resource-scoped (needs the `read_document` action **and**
  read access to that document's entity ACL).

## Wiring test (no live Duar)

```bash
cd demo-mcp
uv sync --extra dev
uv run pytest -q          # proves tools register + resource binding
```

## End-to-end run (live Duar)

1. **Start Duar** (from repo root): `make start` (service on :9003).
2. **Register this server as an MCP-server service app** and grab its key. In the admin
   panel create a service app named `demo-mcp` (or via the API), then set its `kind` to
   `mcp_server`. Export the key:
   ```bash
   export DUAR_URL=http://localhost:9003
   export MCP_SERVICE_NAME=demo-mcp
   export DUAR_SERVICE_KEY=sk_...        # the service app's key
   ```
3. **Start the server** (registers its tools as Duar actions on startup):
   ```bash
   cd demo-mcp && uv run python server.py
   ```
4. **Grant a user.** In the admin panel, create/assign a role in the user's workspace
   that grants the `demo-mcp` actions `list_reports` and/or `read_document`, and share a
   document resource with them for the resource-scoped tool.
5. **Drive it as the user.** Get that user's `duar:access` token (e.g. from the demo
   login flow / browser devtools) and run the host:
   ```bash
   export DUAR_ACCESS_TOKEN=eyJ...
   export DOC_ID=<a document uuid the user can read>
   uv run python host.py
   ```
   Expect `ALLOW` for granted tools/resources and `DENY` (an MCP tool error) for the
   rest — the allow/deny decision is Duar's, from live DB grants.

## How it maps to Duar

| MCP concept | Duar primitive |
| --- | --- |
| this server | a `service_app` with `kind="mcp_server"` |
| a tool (`list_reports`) | an RBAC action (bare name, namespaced by the server's `service_name`) |
| a resource-scoped tool | the action **plus** an entity-ACL check on the resource |
| the user behind the agent | their `duar:access` token (dual auth with the server's key) |
