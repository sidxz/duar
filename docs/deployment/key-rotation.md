# JWT Signing-Key Rotation

Duar signs all tokens (access / admin / refresh / authz) with an RSA private
key. Every issued token carries a `kid` (RFC-7638 thumbprint) header, and
`/.well-known/jwks.json` publishes the **current key plus any retired keys** so
verifiers can pick the right one during a rotation.

This makes rotation **graceful**: a new key can start signing while tokens from
the previous key keep verifying until they expire — no outage.

## Configuration

| Variable | Meaning |
|----------|---------|
| `JWT_PRIVATE_KEY_PATH` | Current signing key (default `keys/private.pem`). |
| `JWT_PUBLIC_KEY_PATH` | Current public key (default `keys/public.pem`). |
| `JWT_PREVIOUS_PUBLIC_KEY_PATHS` | Comma-separated retired **public** keys still inside their verification window. Empty by default. |

The current key's `kid` is derived from its public key, so it is stable across
restarts and identical between the signer and JWKS.

## Planned rotation (zero downtime)

1. **Generate a new keypair:**
   ```bash
   openssl genrsa -out keys/private-new.pem 2048
   openssl rsa -in keys/private-new.pem -pubout -out keys/public-new.pem
   ```
2. **Retire the current public key** — add its path to `JWT_PREVIOUS_PUBLIC_KEY_PATHS`:
   ```bash
   JWT_PREVIOUS_PUBLIC_KEY_PATHS=keys/public.pem
   ```
3. **Promote the new key:**
   ```bash
   JWT_PRIVATE_KEY_PATH=keys/private-new.pem
   JWT_PUBLIC_KEY_PATH=keys/public-new.pem
   ```
4. **Reload Duar.** New tokens sign with the new `kid`; tokens still bearing
   the old `kid` continue to verify because the old public key is in
   `JWT_PREVIOUS_PUBLIC_KEY_PATHS` and is published in JWKS.
5. **Wait out the overlap.** Keep the old public key for at least
   `REFRESH_TOKEN_EXPIRE_DAYS` (default 7 days) — the longest-lived token. After
   that, **remove** it from `JWT_PREVIOUS_PUBLIC_KEY_PATHS` and reload. Old
   tokens are then rejected (their `kid` is no longer in the verify set).

> **Client pickup:** SDK-protected services fetch JWKS and select the verifying
> key by `kid`, refetching JWKS automatically when they see an unknown `kid`. No
> client restart is required for a planned rotation.

## Emergency rotation (key compromise)

If the private key may be compromised, do **not** keep it in the verify set:

1. Generate and promote a new key (steps 1, 3 above).
2. **Do not** add the compromised public key to `JWT_PREVIOUS_PUBLIC_KEY_PATHS` —
   any token signed by the leaked key must stop verifying immediately.
3. Reload. All tokens signed by the old key are now rejected.
4. Force re-authentication: flush refresh-token families / blacklist outstanding
   `jti`s as needed (see `docs/security.md` → Token Lifecycle).

## Client upgrade note (for existing dev clients)

The two current dev client apps must run an SDK version that resolves the
verifying key by `kid` (keyset + refetch-on-unknown-`kid`). Older SDK builds
pinned a single key and would break on rotation.

Before exercising rotation:
1. Bump both client apps to the current SDK and redeploy.
2. Because verification is strict-`kid`, any tokens those clients still hold from
   before the server upgrade are rejected once — users re-authenticate.

There is no need to support old-SDK clients during a transition while all clients
are in dev and upgraded together. Revisit this note if any client ships to
production before rotation is exercised.

## Verifying

- `curl https://<base_url>/.well-known/jwks.json` lists one key normally, and two
  during the overlap window — each with a distinct `kid`.
- A freshly minted token's header `kid` (decode the JWT header) matches a key in
  JWKS.
