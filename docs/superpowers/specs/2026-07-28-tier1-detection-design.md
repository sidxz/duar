# Tier-1 Security Signals — Design

> Approved 2026-07-28. Implements the Tier 1 rules from `docs/ai-security-roadmap.md`:
> impossible travel, new-country/new-device flags, credential-stuffing detection,
> plus the start-endpoint audit gap.

## Decisions (from brainstorm)

- **Detect-only.** Signals are activity events; no enforcement (no auto-revoke, no
  blocking) until real-world false-positive rates are known. The step-up lever for a
  later config flip already exists (`revoke_token_family` blacklists access jtis
  immediately; SDK falls into the `silentLogin`/`needs_reauth` path).
- **Country-only geo data.** Keep the bundled geoip2fast dataset. Impossible travel
  uses a static country-centroid table + haversine. No ASN/city datasets; Redis key
  shapes leave room to add per-ASN keying later.
- **Include the start-site audit gap** (rejects at `GET /auth/login/{provider}` and
  `GET /auth/admin/login/{provider}` currently write no ActivityLog row).
- **Dedicated Dashboard signals card** in the admin UI, on top of the one-line
  Activity-dropdown/chart-group additions.

## Principle (carried from roadmap)

Detection never sits in the authz decision path. Every signal evaluation is
fail-open: an exception logs a warning and the auth flow proceeds untouched — the
same contract `swap_refresh_context` established (telemetry failure must never
revoke or block anything).

## Architecture

New files:

- `service/src/services/signal_service.py` — all four rules as async functions;
  Redis state via the existing `token_service.get_redis()`.
- `service/src/services/country_centroids.py` — static `{ISO2: (lat, lon)}` dict
  (~250 entries) + `haversine_km()` (stdlib `math` only).

Reused, not rebuilt:

- Country lookup: geoip2fast singleton in `insights_service` (`_lookup_country`).
- Device family: `insights_service.parse_user_agent` (browser/OS regex tables).
- Event emission: `activity_service.log_activity` + `log_security` stream.
- Client IP: `middleware/rate_limit.get_client_ip` (trusted-proxy aware).

Call sites (all existing hot points, all fail-open wrapped):

| Hot point | Location | Rules run |
|---|---|---|
| User login success | `auth_routes.py` (~:323, next to the `user_login` row) | travel, new country, new device |
| Admin login success | `auth_routes.py` (~:784) | travel, new country, new device |
| Refresh context changed | `auth_service.rotate_refresh_token` (~:405, inside the existing non-None branch) | travel |
| Login failure | `auth_routes._log_login_failure` | stuffing counters |

## Rules

### 1. Impossible travel — `login_impossible_travel` (severity: high)

- State: `geo:last:{user_id}` → JSON `{country, ts}`, TTL 90 days. Updated on every
  successful login and every refresh-context change (per-user, spans devices).
- Fire when: previous state exists, country differs, and
  `haversine_km(centroid_prev, centroid_new) / Δt_hours > signal_impossible_travel_kmh`.
- Skip silently: private/unresolvable IP (geoip code starts with `-`), no prior
  state, same country.
- Ping-pong damping: `geo:flag:{user_id}:{a}:{b}` (country pair, sorted), SETNX,
  TTL 6h — each pair signals at most once per window. Covers the VPN-laptop +
  phone alternating-countries false-positive mode.
- Known ceiling: country centroids miss intra-country jumps (US coast-to-coast)
  and overstate distance for neighboring-country borders. Acceptable for
  detect-only; city-level data is the upgrade path.

### 2. New country — `login_new_country` (severity: medium)

- State: `seen:cty:{user_id}` — Redis SET of ISO2 codes, TTL 365d refreshed on touch.
- Empty set (SCARD 0) → seed silently (first login ever is not a signal).
- Else `SADD` returning 1 → signal. Atomic first-seen; no read-then-check race.

### 3. New device — `login_new_device` (severity: low)

- Same mechanics on `seen:dev:{user_id}`; member = `"{browser_family}|{os_family}"`
  from `parse_user_agent`. Deliberately family-level: raw-UA hashing would fire on
  every browser version bump. (Roadmap said UA+ASN hash; this is the stable
  approximation without ASN data.)

### 4. Credential stuffing — `credential_stuffing_suspected` (severity: high)

- In `_log_login_failure`, only for callback (credential-shaped) failures:
  - `INCR fail:ip:{ip}` — `EXPIRE` to window on first increment.
  - `SADD fail:em:{ip} <email>` when an email is present — same TTL.
- Fire when failures ≥ `signal_stuffing_failures` AND distinct emails ≥
  `signal_stuffing_distinct_emails` within the window. Distinct-email spread is
  what separates stuffing from one user fat-fingering a password.
- Once per window per IP: `fail:flag:{ip}` SETNX marker, window TTL.
- Event has `target_type="system"`, no actor (attacker is unauthenticated);
  detail carries ip, counts, and the window.

## Events

All signals are ordinary `log_activity` rows plus a `log_security("auth.signal.<name>")`
stream emit. Consistent detail envelope (the Tier-2 risk-scorer seam):

```json
{"signal": "impossible_travel", "severity": "high", "ip": "…", "user_agent": "…",
 "prev_country": "US", "country": "RU", "km": 7510, "minutes": 42, "kmh": 10728}
```

Rule-specific fields: travel → `prev_country/country/km/minutes/kmh`; new country →
`country`; new device → `device`, plus `country` for context; stuffing →
`failures/distinct_emails/window_minutes`.

User-scoped signals: `target_type="user"`, `target_id=actor_id=user.id`.

## Start-endpoint audit gap

Route the currently-invisible rejects through `_log_login_failure`:

- `GET /auth/login/{provider}`: unconfigured provider, non-S256 PKCE method,
  `redirect_uri_not_allowed` (reasons: `provider_not_configured`,
  `pkce_method_rejected`, `redirect_uri_not_allowed`).
- `GET /auth/admin/login/{provider}`: unconfigured provider (flow=`admin`).

`_log_login_failure` gains `count_for_stuffing: bool = True`; start-site calls pass
`False` — these are config/probe-shaped, not credential attempts, and must not
poison the stuffing counters. Existing stream-only emits stay as they are.

## Config (`Settings`, env-mapped)

| Flag | Default |
|---|---|
| `signals_enabled` | `True` |
| `signal_impossible_travel_kmh` | `900` |
| `signal_stuffing_window_minutes` | `15` |
| `signal_stuffing_failures` | `10` |
| `signal_stuffing_distinct_emails` | `5` |

`signals_enabled=False` short-circuits every entry point.

## Admin UI

- `SecuritySignalsCard` on the Dashboard: four stat tiles (one per signal action)
  with counts over the selected range, sourced from the existing
  `GET /admin/activity/summary` response filtered client-side — **zero new
  endpoints**. Severity styling per dataviz tokens (status-red only for
  high-severity counts > 0). Each tile links to the Activity page filtered to
  that action.
- One-line adds: four entries in the `Activity.tsx` action dropdown; extend the
  "Auth anomalies" regex in `charts.tsx`.

## Testing

`service/tests/test_signals.py`, following existing patterns
(`test_auth_event_logging.py`):

- Haversine sanity (known city pair within tolerance).
- Travel threshold boundary (just under / just over `kmh`).
- First-seen seeding vs. signal for country and device sets.
- Ping-pong damping: second A↔B flip within 6h emits nothing.
- Stuffing AND-condition: 10 failures × 1 email → no signal; 10 × 5 → signal;
  once-per-window marker; window expiry resets.
- Fail-open: Redis client raising → no exception propagates, no signal row.
- `signals_enabled=False` → no Redis calls, no rows.
- Start-site rejects: ActivityLog row written with correct reason/flow; stuffing
  counters NOT incremented.

## Out of scope (explicit)

- Enforcement (auto-revoke, IP blocking) — later config flip, needs FP data first.
- ASN/city datasets, notification email (no email subsystem exists), per-ASN
  counters, risk scoring (Tier 2).
