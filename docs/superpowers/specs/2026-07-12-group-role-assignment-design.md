# Group → Role Assignment — Design

**Date:** 2026-07-12
**Status:** Approved (design)
**Branch:** `group-roles` (off `main`)
**Task:** Extend workspace RBAC roles so that **groups** — not just individual users — can be assigned to a role. Every member of a bound group holds the role's actions for as long as they are in the group.

## Problem

The three authorization tiers are asymmetric about groups. Entity ACLs already accept group grantees (`ResourceShare.grantee_type = 'group'`), but RBAC roles bind only to individual users (`user_roles`). Teams that map cleanly to groups ("analysts", "billing-ops") must be role-assigned user by user, and drift as people join/leave the team.

## Decisions (locked with user 2026-07-12)

1. **Bind authority: Duar admin only.** The group↔role link is created/removed exclusively through the admin panel, mirroring `user_roles` today. Workspace admins control *who is in* a bound group (existing behavior) but never *what a group can do*.
2. **Member display: direct members + groups list.** A role's expanded row shows two sections — direct user members (as today) and bound groups. No computed "effective members" view in v1.
3. **Audit gap fixed with this feature.** Group create/delete/member-add/member-remove currently emit **no** activity events; once groups grant actions, a member-add is a privilege grant. This feature adds activity events for group mutations plus `role_group_added`/`role_group_removed`.

## Non-Goals

- **No group nesting / transitive resolution.** Groups are flat; stays that way.
- **No workspace-owner self-service binding.** Rejected in favor of decision 1.
- **No effective-members query or "via group X" badges** (decision 2). Revisit if admins get confused by multi-path grants in practice.
- **No SDK changes.** Python `RoleClient` / JS `roles.ts` only call register/check/user-actions, whose contracts are unchanged.
- **No JWT changes.** RBAC group resolution is DB-side by `user_id`; the JWT `groups` claim remains ACL-only.
- **No token revocation on bind/unbind.** Consistent with direct assignment today; staleness bounded by authz-token TTL.

## Current state (facts the design builds on)

- `user_roles` binds users to workspace-scoped roles; assignment guards workspace membership (`role_service.py` `assign_user_role`).
- `check_action` / `get_user_actions` join `user_roles` only, resolved live from the DB per check.
- Authz tokens bake resolved `actions` at mint (`authz_routes.py`); staleness = authz TTL.
- Groups are workspace-scoped, flat; `group_service.add_member` guards workspace membership; group mutation requires workspace admin+ (`group_routes.py`).
- `workspace_service.remove_member` purges `group_memberships` + `user_roles` + user shares in one transaction (dormant-privilege guard).
- `group_service.delete_group` purges group ACL shares and revokes member tokens; `remove_member` (group) revokes the member's tokens.

## Design

### 1. Schema — new table `group_roles`

Mirrors `user_roles` (`service/src/models/role.py`):

```python
class GroupRole(Base):
    __tablename__ = "group_roles"
    __table_args__ = (
        UniqueConstraint("group_id", "role_id", name="uq_group_role"),
        Index("ix_group_roles_group_id", "group_id"),
        Index("ix_group_roles_role_id", "role_id"),
    )
    id: UUID pk
    group_id: FK groups.id ondelete=CASCADE, nullable=False
    role_id: FK roles.id ondelete=CASCADE, nullable=False
    assigned_by: FK users.id ondelete=SET NULL, nullable=True
    assigned_at: server_default=now()
```

`Role.group_roles` relationship with `cascade="all, delete-orphan"`; `Group.group_roles` likewise. One additive Alembic migration, no backfill, auto-applied on startup.

**Rejected alternative:** polymorphic `role_assignments(assignee_type, assignee_id)` à la `ResourceShare`. Polymorphic IDs cannot carry real FKs — the codebase already pays that tax (`delete_group` must manually purge shares "or they orphan permanently"). Real FKs make every lifecycle case below free.

### 2. Check path — union of two grant paths

`check_action` and `get_user_actions` in `role_service.py` become a UNION:

```
path A (existing): Role ⋈ UserRole                         WHERE user_id = :u
path B (new):      Role ⋈ GroupRole ⋈ GroupMembership      WHERE user_id = :u
```

Both filtered by `Role.workspace_id`, `ServiceAction.service_name` (+ `action` for check). UNION dedups role names / actions. All joins are indexed (`ix_group_memberships_user_id`, new `ix_group_roles_group_id`). The authz-token mint path (`authz_routes.py` → `get_user_actions`) picks this up with zero changes.

### 3. Service layer — `role_service.py` additions

- `assign_group_role(db, group_id, role_id, assigned_by)` — 404 if role or group missing; **400 if `group.workspace_id != role.workspace_id`** (the only guard needed: group members are workspace members by construction); 409 on duplicate via `uq_group_role`.
- `remove_group_role(db, group_id, role_id)` — 404 if binding missing.
- `list_role_groups(db, role_id)` — group id/name/description + member_count + assigned_at/by, ordered by `assigned_at`.
- `list_workspace_roles` gains a `group_count` scalar subquery beside `member_count`.

### 4. Admin API — `admin_routes.py`, mirroring the member endpoints

- `GET /admin/roles/{role_id}/groups` → `list[RoleGroupResponse]`
- `POST /admin/roles/{role_id}/groups/{group_id}` → 201, activity `role_group_added` (target_type `group`, detail `{role_id}`)
- `DELETE /admin/roles/{role_id}/groups/{group_id}` → 204, activity `role_group_removed`
- `RoleResponse` gains `group_count: int = 0` (backward-compatible).
- New schema `RoleGroupResponse(group_id, name, description, member_count, assigned_at, assigned_by)`.

### 5. Audit events for group mutations (gap fix)

`group_routes.py` handlers gain `activity_service.log_activity` calls (actor = the workspace admin's user id):

- `group_created` / `group_deleted` (target_type `group`)
- `group_member_added` / `group_member_removed` (target_type `user`, detail `{group_id}`)

Logging lives in the route handlers (same placement as `admin_routes.py` role-member handlers): log + commit after the service-layer mutation succeeds.

### 6. Admin UI — `WorkspaceDetail.tsx` RolesTab

- Role row header shows `N members · M groups`.
- Expanded row gains a **Groups** section beside Members: bound-group list (name + member count + remove button) and an add-picker fed by the existing `workspace-groups` query.
- New client functions `getRoleGroups` / `assignRoleGroup` / `removeRoleGroup`; invalidate `["role-groups", roleId]` + `["workspace-roles", workspaceId]`.

### 7. Docs

- `docs/guide/roles.md` — concept paragraph (groups as assignees, flat, admin-bound) + endpoint list.
- `docs/api/roles.md` — the three new admin endpoints + `group_count` field.
- `docs/PLAN.md` three-tier description: "roles assignable to users and groups".

## Invariants — all preserved with zero new purge code

| Event | What happens to group-derived grants |
|---|---|
| User leaves workspace | `remove_member` already purges `group_memberships` → path B dies with it. No dormant-privilege-on-reinvite via groups, ever. |
| Group deleted | `group_roles` rows CASCADE; `delete_group` already revokes member tokens. |
| Role deleted | CASCADE. |
| User deleted | `group_memberships` CASCADE. |
| Workspace deleted | roles + groups CASCADE. |
| User added to group | Workspace-membership guard already enforced in `group_service.add_member`. |

## Accepted consequences (documented, intentional)

1. **Privilege delegation.** After an admin binds group→role, workspace admins/owners control who holds those actions by editing group membership. Bounded: they choose *who's on the team*, never *what the team can do*. Mirrors existing group ACL-share behavior. Documented in `docs/guide/roles.md`.
2. **Freshness asymmetry (favorable).** Removing a user from a *group* revokes their tokens (existing ACL-claim hygiene) → group-derived actions drop at next authz mint. Removing a direct role assignment still leaves authz tokens valid until TTL (unchanged today).
3. **Multi-path grants.** A user granted a role both directly and via a group keeps the role until *all* paths are removed. UI shows both sections so the second path is discoverable.

## Testing

- **`test_role_routes_authz.py` extensions:** action allowed via group path; allowed via both paths returns deduped role names; removing direct assignment while group path remains → still allowed; removing group membership → denied; cross-workspace group↔role bind → 400; duplicate bind → 409; authz token `actions` include group-derived actions.
- **`test_remove_member_cleanup.py` extension:** workspace removal kills group-derived role access (membership purge → path B gone).
- **Admin route tests:** the three new endpoints incl. activity events.
- **Group audit tests:** member add/remove emits activity rows.

## Out of scope / future

- Effective-members view ("via group X" badges) — revisit on admin feedback.
- Workspace-owner self-service binding.
- Group nesting.
- SDK convenience wrappers for the new admin endpoints (admin-panel-only surface).
