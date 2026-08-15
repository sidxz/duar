# JWT Signing-Key Rotation — Design

**Date:** 2026-05-29
**Status:** Approved (design)
**Closes:** ASVS MED-6 (JWT key rotation)
**Task:** #1 — Implement graceful RS256 signing-key rotation

## Problem

Duar signs every token (access / admin / refresh / authz) with a **single
static RSA private key** loaded from a file (`service/src/auth/jwt.py`). Issued
tokens carry **no `kid` header**, and `decode_token` verifies against **one**
public key. Consequences:

- The signing key **cannot be rotated** without invalidating every live token
  and forcing all clients to re-fetch keys — i.e. an outage.
- If the key leaks, it is **unrecoverable** without that outage: you cannot
  stand up a new key alongside the old one.
- The SDK makes it worse: `JWTAuthMiddleware._get_public_key()` grabs
  `keys[0]` from JWKS, ignores `kid`, and **caches that one key forever** (never
  refetches). `AuthzMiddleware` pins a single PEM. So even if the server
  published multiple keys, SDK-protected services would not pick up a new one
  without a process restart.

This concentrates trust in a key that can't be rotated — the central risk called
out in the security review for proxy mode.

## Goals

- Sign with a **current** key; verify against a set of **current + previously
  retired** keys, selected by `kid`.
- Support **graceful rotation**: a new key can sign while tokens from the old key
  still verify, until they expire — no outage, no forced mass re-login during a
  planned rotation.
- Make the **SDK** `kid`-aware and able to pick up new keys without a restart.
- Keep a clean seam so **KMS/HSM** signing can replace file-based keys later
  without touching token/JWKS/verify logic.

## Non-Goals (deferred behind the provider seam)

- Automatic/scheduled rotation.
- Admin-API-triggered rotation.
- KMS/HSM-backed signing.
- Backward compatibility for pre-upgrade tokens (all clients are in dev — see
  Transition).

## Decisions

| Decision | Choice |
|----------|--------|
| Key storage | PEM files + a thin key-provider seam (KMS later swaps the provider) |
| Rotation trigger | Manual, with a documented runbook (no auto/admin trigger) |
| `kid` derivation | RFC-7638 thumbprint (logic already in `jwks.py`) — deterministic from the key |
| Transition / legacy tokens | **Strict `kid`** — no legacy fallback. Pre-upgrade tokens (no `kid`) are rejected; dev clients re-login once at first deploy |

## Design

### 1. Config (`service/src/config.py`)
Keep the existing pair as the **current** signer (no change for existing deploys):
- `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH` — current key.

Add:
- `JWT_PREVIOUS_PUBLIC_KEY_PATHS` — optional, comma-separated list of retired
  **public** keys still inside their verify window (empty by default).

### 2. Key provider (`service/src/auth/key_provider.py`, new)
The single seam KMS would later replace:
- `signing_key() -> (private_pem: str, kid: str)` — current private key + its
  thumbprint `kid`.
- `verification_keys() -> dict[str, PublicKey]` — `{kid: public_key}` for the
  current key plus every key in `JWT_PREVIOUS_PUBLIC_KEY_PATHS`.

`kid` is computed with the existing RFC-7638 thumbprint routine (extracted from
`jwks.py` so provider and JWKS share one implementation).

### 3. Signing (`service/src/auth/jwt.py`)
Every `create_*_token` (access / admin / refresh / authz) passes
`headers={"kid": <current kid>}` to `jwt.encode`. **No claim changes.**

### 4. Verify (`decode_token`)
- Read the unverified header `kid`.
- Look it up in `verification_keys()`. **Hit → verify with that key.**
- **Miss or absent `kid` → reject** (strict; no try-all).
- `RS256`-only, audience, and issuer enforcement unchanged.

### 5. JWKS (`service/src/auth/jwks.py`)
`build_jwks()` publishes **all** verification keys (current + previous), each
with its `kid`, `use: sig`, `alg: RS256`. (Loop the existing single-key builder.)
Endpoint stays `/.well-known/jwks.json`.

### 6. SDK (`sdk/src/duar_auth/`) — required for rotation to work
**`JWTAuthMiddleware`** and **`AuthzMiddleware`**:
- Replace the single-key cache with a `{kid: key}` **keyset** fetched from JWKS.
- On each request, read the token's `kid` and select that key.
- **On unknown `kid`, refetch JWKS once** (rotation pickup without restart), then
  retry; still unknown → reject.
- Tokens with no `kid` → reject (strict, matches server).
- `AuthzMiddleware` gains the JWKS-by-`kid` path it currently lacks (it pins one
  PEM today); it derives the JWKS URL from `base_url` / `duar_instance`.

### 7. Rotation runbook (`docs/` — operator doc)
**Planned rotation:**
1. Generate a new RSA keypair.
2. Move the *old* public key path into `JWT_PREVIOUS_PUBLIC_KEY_PATHS`.
3. Point `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` at the new key.
4. Reload Duar. New tokens sign with the new `kid`; old tokens still verify.
5. Keep the old public key for **≥ the longest token lifetime** (refresh = 7 days),
   then remove it from `JWT_PREVIOUS_PUBLIC_KEY_PATHS`.

**Emergency rotation (key compromise):** do the above immediately, and
*additionally* revoke outstanding tokens (force re-login / flush refresh
families) — do **not** keep the compromised key in the verify set.

### 8. Client upgrade note (for the existing dev clients)
The two dev client apps run the old SDK, which pins `keys[0]` and never refetches.
Before the first server deploy of this change:
1. Bump both apps to the new SDK version (`kid`-aware keyset).
2. Redeploy them.
3. Because the change is strict-`kid`, their users re-authenticate once.

No need to support old-SDK clients during a transition — all clients are in dev
and will be upgraded together. (Revisit this note if any client ships to prod
before rotation is exercised.)

## Testing
- Signing stamps the current `kid` on every token type.
- `decode_token` verifies a token by `kid`.
- Token with **no `kid`** is rejected; token with **unknown `kid`** is rejected.
- **Rotation continuity:** sign with a new current key while a previous key is in
  the verify set → tokens from both verify; after the previous key is dropped,
  its tokens are rejected.
- `build_jwks()` emits multiple keys with distinct `kid`s.
- SDK: selects key by `kid`; **refetches JWKS on unknown `kid`** and then accepts
  a token signed by a newly added key without restart.

## Future work (behind the provider seam)
- KMS/HSM-backed `signing_key()` so the private key never sits on disk.
- Scheduled auto-rotation and/or admin-triggered rotation (would add key
  lifecycle + persistence; out of scope here).
