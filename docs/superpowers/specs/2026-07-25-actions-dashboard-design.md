# RBAC Actions Dashboard — Design

**Date:** 2026-07-25
**Status:** Approved (brainstormed with user)

## Goal

Admin-facing dashboard over the RBAC action-usage data that started accumulating
2026-07-25 (`action_usage` rollup + `action_denied` activity events, commits
`daaf981..5604192`). Two lenses at equal parity: usage analytics (what's hot)
and role mining (what's granted but never used). Covers the Tier-3 role-mining
item in `docs/ai-security-roadmap.md`.

## Scope decisions (user-confirmed)

- **Both lenses, full parity** — usage analytics and security/role-mining.
- **Both placements** — global "Actions" nav page (all workspaces, with
  workspace filter) plus an "Actions" tab in WorkspaceDetail, sharing one
  component.
- **All four views** — top actions + per-service, per-user usage,
  denied-vs-allowed trend, dormant grants + unused roles.
- **Selectable time range** — 7/30/90 days.
- **Approach: one aggregate endpoint** (Insights precedent), not per-view
  endpoints. Split the role-mining queries into their own endpoint only if
  they measurably get slow.

## Backend

New `service/src/services/actions_insights_service.py`, single entry point.
Route in `admin_routes.py` next to `GET /admin/activity/insights`:

```
GET /admin/actions/insights?days=30&workspace_id=<uuid, optional>
```

Admin-cookie guarded (`require_admin`). `days` restricted to {7, 30, 90},
default 30. No new tables, no migrations.

Response payload:

| Field | Content |
|---|---|
| `top_actions` | sum(count) grouped by `(service_name, action)` over window, desc, top 20 |
| `by_service` | sum(count) grouped by `service_name` |
| `top_users` | sum(count) grouped by user, joined to `users` for email/display name, top 20 |
| `trend` | per-day series: allowed = sum from `action_usage`; denied = count of `activity_logs` rows with `action='action_denied'` grouped by `created_at::date` |
| `dormant_grants` | granted `(user, service, action, via-role)` pairs with zero `action_usage` in window; capped at 50 rows + `total` count |
| `unused_roles` | roles where no assignee exercised any of the role's actions in window; zero-assignee roles qualify, flagged `no_assignees` |
| `data_since` | `min(day)` from `action_usage` (nullable) |

**Dormant grants query:** enumerate granted pairs via both grant paths —
direct (`user_roles` ⋈ `role_actions` ⋈ `service_actions`) and via group
(`group_roles` ⋈ `group_memberships` ⋈ …) — same join shape as
`_granted_stmt` in `role_service.py`, then anti-join against `action_usage`
rows in the window (matching workspace/user/service/action). Workspace filter
applies to both sides.

**Honest labeling:** data only exists from 2026-07-25. `data_since` lets the
frontend show "data since {date}" whenever that is later than the window
start — no hardcoded rollout date.

## Frontend

`admin/src/pages/ActionsInsights.tsx` exports:

- `ActionsInsightsView({ workspaceId? })` — the shared dashboard body:
  7/30/90-day picker, then Cards: `BarList` (from `components/charts.tsx`)
  for top actions / by service / top users; a dual-series allowed/denied
  daily chart in the style of `ActivityMixChart`; two tables for dormant
  grants and unused roles. Per-section empty states (relevant now — data is
  young).
- Global page — `ActionsInsightsView` plus a workspace filter dropdown
  (default: all workspaces). New "Actions" item in `Layout.tsx` nav, route
  in `App.tsx`.

WorkspaceDetail gets an "Actions" tab rendering `ActionsInsightsView` with
`workspaceId` pinned (no workspace dropdown).

Plumbing: response types in `types/api.ts`, fetch fn in `api/client.ts`, one
React Query hook. Styling: shadcn/ui Cards, semantic theme tokens (never
`zinc-*`), IBM Plex — match Insights.tsx.

## Error handling

- Endpoint behind existing admin auth + default admin rate limits.
- Empty `action_usage` → all sections return empty lists, `data_since` null;
  frontend shows per-section empty states, not an error.
- Invalid `days` → 422 (validated to the allowed set).

## Testing

Service tests (pytest, existing patterns):

- Seed `action_usage` rows + roles/grants; assert each section's numbers.
- Workspace filter: rows outside the filtered workspace excluded everywhere.
- Dormant grants: grant with usage excluded; grant without usage included;
  group-path grant without usage included.
- Unused roles: role with active usage excluded; role whose assignees never
  used its actions included; zero-assignee role included with `no_assignees`.
- Trend: denied series counts only `action_denied` activity rows.

Admin SPA has no test harness — manual browser pass after build.

## Out of scope

- Retention/pruning of `action_usage` (revisit when data ages).
- Splitting role-mining queries into a separate endpoint (only if slow).
- Alerting/exports on dormant grants (roadmap Tier-3 follow-ups).
