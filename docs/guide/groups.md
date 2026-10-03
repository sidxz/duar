# Groups

Groups are named collections of users within a workspace. Their primary purpose is batch permission grants -- share a resource with a group instead of sharing with each user individually.

```python
# Share a document with the "Engineering" group
await duar.permissions.share(
    token=user_token,
    resource_type="document",
    resource_id=doc_id,
    grantee_type="group",
    grantee_id=engineering_group_id,
    permission="edit",
)
```

All members of the group now have edit access to the document.

## How Groups Work

- Groups are workspace-scoped. A group in workspace A is separate from a group in workspace B.
- Group names must be unique within a workspace (`UNIQUE(workspace_id, name)`).
- Group IDs are embedded in the user's JWT `groups` claim at token issuance.
- During [permission resolution](permissions.md), group shares are checked using these JWT-embedded IDs -- no extra database lookup for group membership.

## Managing Groups

All group operations require `admin` or `owner` workspace role. Any member can list groups.

```
POST   /workspaces/{wid}/groups                          # Create
GET    /workspaces/{wid}/groups                          # List
PATCH  /workspaces/{wid}/groups/{gid}                    # Update
DELETE /workspaces/{wid}/groups/{gid}                    # Delete
POST   /workspaces/{wid}/groups/{gid}/members/{uid}      # Add member
DELETE /workspaces/{wid}/groups/{gid}/members/{uid}      # Remove member
```

A user can belong to multiple groups. Removing a user from a group does not remove them from the workspace.

## Important Behavior

**Group membership changes require token refresh.** Since group IDs are embedded in the JWT, adding or removing a user from a group only takes effect when their token is refreshed or re-issued.

**Deleting a group** cascades to all `group_memberships`. Resource shares granted to the deleted group will no longer match during permission checks.

## Related

- [Entity Permissions](permissions.md) -- how group shares are resolved (step 7)
- [Workspaces](workspaces.md) -- workspace-scoped isolation
