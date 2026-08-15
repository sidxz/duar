# Realm Endpoints

Realm endpoints come in two groups: the **internal** service-key surface used by member apps (`/realm/*`), and the **admin** management surface (`/admin/realms`). See [Realms](../guide/realms.md) for the concept.

In a split deployment the `/realm/*` routes live only on the unpublished internal listener (`:9010`); `/admin/realms` lives on the public listener. See [Deployment → Network Split](../deployment/index.md#network-split-public--internal-listeners).

## Internal endpoints

Service-key only (`X-Service-Key`). Used by member apps' SDKs.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/realm/whoami` | Service key | Resolve this service's effective scope + realm |
| POST | `/realm/m2m-token` | Service key | Mint a no-user realm m2m token |

---

### GET /realm/whoami

Returns the calling service's shared scope. The SDK calls this once at startup to self-discover whether it is a realm member — no app-side realm config. A standalone service returns its own name and `realm: null`.

**Auth:** Service key only.

**Response:** `200 OK`

```json
{
  "service_name": "reports",
  "effective_scope": "acme-suite",
  "realm": { "slug": "acme-suite", "name": "Acme Suite" }
}
```

A standalone service:

```json
{ "service_name": "reports", "effective_scope": "reports", "realm": null }
```

```bash
curl http://duar-internal:9010/realm/whoami \
  -H "X-Service-Key: sk_your_key"
```

---

### POST /realm/m2m-token

Mints a short-lived no-user m2m token (sender side of [Flow B](../guide/realms.md#flow-b-no-user)). The caller must be an active member of an active realm.

**Auth:** Service key only.

**Request Body:** (optional; the `target` field is reserved and off by default)

```json
{}
```

**Response:** `200 OK`

```json
{ "token": "eyJhbGciOi...", "expires_in": 300 }
```

`expires_in` is the realm's `m2m_ttl_s`. The token's `caller` and `svc` are **server-stamped from the authenticated key** — a client cannot assert them.

**Errors:** `403` — caller is standalone, or its realm is inactive.

```bash
curl -X POST http://duar-internal:9010/realm/m2m-token \
  -H "X-Service-Key: sk_your_key" -H "Content-Type: application/json" -d '{}'
```

#### m2m token claims

```jsonc
{
  "iss": "<base_url>",
  "aud": "sentinel:m2m",     // separate from user audiences — never accepted as a user
  "type": "m2m",
  "svc": "acme-suite",        // realm slug = effective_scope
  "caller": "app-a",          // server-stamped: which member minted it (audit)
  "actions": ["*"],           // full trust in v1
  "aud_target": null,         // reserved; when set, receiver checks it == its service_name
  "jti": "...", "iat": 0, "exp": 0
}
```

The receiver verifies the signature, `aud`, `type`, and `svc == effective_scope`. There are no `sub`/`email`/user claims.

## Admin endpoints

Admin cookie + `X-Requested-With: XMLHttpRequest` (CSRF). All mutations are audit-logged.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/admin/realms` | — | `200` list of realms |
| POST | `/admin/realms` | `RealmCreateRequest` | `201` realm |
| GET | `/admin/realms/{realm_id}` | — | `200` realm |
| PATCH | `/admin/realms/{realm_id}` | `RealmUpdateRequest` | `200` realm |
| DELETE | `/admin/realms/{realm_id}` | — | `204` |
| GET | `/admin/realms/{realm_id}/members` | — | `200` list of members |
| POST | `/admin/realms/{realm_id}/members/{service_app_id}` | — | `201` member |
| DELETE | `/admin/realms/{realm_id}/members/{service_app_id}` | — | `204` |

**`RealmCreateRequest`** — `slug` must start with a letter and match `^[a-z][a-z0-9-]*[a-z0-9]$`; it is immutable after create.

```json
{ "name": "Acme Suite", "slug": "acme-suite", "m2m_ttl_s": 300 }
```

**`RealmUpdateRequest`** — all fields optional; **no `slug`** (immutable). `m2m_ttl_s` range 30–3600.

```json
{ "name": "Acme Suite v2", "m2m_ttl_s": 600, "is_active": false }
```

**Realm response shape:**

```json
{
  "id": "a1b2c3d4-...",
  "slug": "acme-suite",
  "name": "Acme Suite",
  "m2m_ttl_s": 300,
  "is_active": true,
  "created_at": "2026-06-26T10:00:00Z"
}
```

**Member response shape** — `has_grants` flags a member that already had permission/RBAC rows under its own `service_name` (a join-with-existing-grants warning; see [migration](../guide/realms.md#migration-and-limitations)):

```json
{ "id": "f1e2...", "name": "Reports", "service_name": "reports", "has_grants": false }
```

**Errors:** `409` on add when the service app already belongs to a realm (one-realm-max). Delete is guarded by a type-to-confirm step in the admin UI.

**Audit events:** `realm_created`, `realm_updated`, `realm_deleted`, `realm_member_added`, `realm_member_removed`.
