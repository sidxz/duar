# Admin Picker Dialogs — Design

**Date:** 2026-07-12
**Status:** Approved
**Scope:** Admin SPA only (`admin/`). No backend changes.

## Problem

`WorkspaceDetail.tsx` uses native `<select>` dropdowns for every "add X to Y"
interaction. Worst case is the role action picker: a flat dropdown of every
action across every service, rendered as `service:service:action` strings —
hundreds of indistinguishable rows. The Members tab says "+ Invite Member"
although the backend (`invite_member`) only ever attaches *existing* users
(users come from IdPs; there is no invitation flow), so the label lies and an
admin gets no way to browse/search who exists.

The same weak pattern appears in five places:

1. Members tab — add user to workspace (modal with free-text email)
2. Role → Actions — flat dropdown of all service actions
3. Role → Members — dropdown of workspace members
4. Role → Groups — dropdown of workspace groups
5. Group → Members — dropdown of workspace members

## Decisions (validated with user)

- **One shared component** covering all five call sites.
- **Interaction model:** button opens a search dialog with a multi-select
  checkbox list; grouped by service for actions; single "Add N" commit.
- **Members flow:** rename to "Add Users"; one role select applied to the
  whole batch (default `viewer`); per-user roles remain editable inline in
  the member list afterward.
- **No new dependencies** — built from existing shadcn pieces (Dialog,
  Input, Checkbox, Button, Badge). No cmdk, no popover.
- **No backend changes** — `POST /admin/workspaces/{id}/members/invite`
  keeps its path; `addRoleActions` already accepts a batch of IDs.

## Component: `AddItemsDialog`

New file: `admin/src/components/AddItemsDialog.tsx`.

```ts
type PickerItem = {
  id: string;
  label: string;          // primary text (e.g. action name, user name)
  sublabel?: string;      // muted secondary (description, email)
  group?: string;         // renders group headers (service_name for actions)
  disabled?: boolean;     // shown grayed, not selectable
  disabledReason?: string; // e.g. "already a member"
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  items: PickerItem[];
  onSearch?: (q: string) => void; // present = server-search mode; absent = client filter
  isLoading?: boolean;            // server mode: show skeleton rows
  onAdd: (ids: string[]) => Promise<void>;
  addLabel: (n: number) => string; // e.g. n => `Add ${n} action${n===1?"":"s"}`
  footer?: React.ReactNode;       // slot rendered left of Cancel/Add (role select)
};
```

Behavior:

- Search input at top. Client mode filters `label`/`sublabel`/`group`
  case-insensitively. Server mode calls `onSearch` debounced 250 ms and the
  parent re-renders `items`.
- Scrollable list (`max-h-[50vh] overflow-y-auto`). Items with `group`
  render under a sticky group header showing the group name and a
  **"select all"** checkbox that toggles the group's *visible, enabled*
  rows. Ungrouped items render flat.
- Row = checkbox + label + muted sublabel. Disabled rows are grayed with
  `disabledReason` right-aligned; clicking does nothing.
- Footer: "N selected" count, optional `footer` slot, Cancel, and the Add
  button (`addLabel(n)`, disabled when `n === 0` or while `onAdd` pends).
- Selection and search state **reset every time the dialog opens** (guards
  the stale-picker-state class of bug fixed in 563e53f).
- Empty states: "No matches" (search) / "Nothing left to add" (empty source
  list). A fully-disabled list is not an empty state: it renders every row
  grayed with its `disabledReason`, which tells the admin *why* there is
  nothing to pick.

## Call sites

| Call site | Button | Items | Mode | Commit |
|---|---|---|---|---|
| Members tab | **+ Add Users** | `getUsers(1, 20, q)` results; existing members `disabled` ("already a member") | server | N × `inviteMember(email, role)` |
| Role → Actions | + Add Actions | `getServiceActions()`, `group = service_name`, `label = action`, `sublabel = description`; assigned `disabled` ("already assigned") | client | **one** `addRoleActions(roleId, ids)` |
| Role → Members | + Add Members | workspace members; in-role `disabled` | client | N × `addRoleMember` |
| Role → Groups | + Add Groups | workspace groups, `sublabel = description` (`Group` has no `member_count`); assigned `disabled` | client | N × `addRoleGroup` |
| Group → Members | + Add Members | workspace members; in-group `disabled` | client | N × `addGroupMember` |

Notes:

- Each inline `select + Add` row is deleted along with its now-dead state
  (`selectedActionId`, `addMemberEmail`, `selectedGroupId`) and replaced by
  the button + dialog.
- Members dialog `footer` = role `<select>` (viewer/editor/admin/owner —
  same options as today; backend still gates owner grants).
- Users search uses React Query keyed on the debounced query with
  `placeholderData: keepPreviousData`; first page (20) only — search
  narrows instead of paginating. Already-member detection compares against
  the already-loaded `getWorkspaceMembers` list.
- Action rows drop the `service:service:action` rendering everywhere the
  picker shows them; the *assigned* actions list under a role keeps its
  current flat rendering (out of scope).

## Errors

Multi-call batches run via `Promise.allSettled`. One toast summarizes:
all-success → `toast.success("Added 3 users")`; partial → `toast.error`
listing failures as `label — backend message` (e.g. org-restriction 403,
"already a member" races). Relevant queries invalidated once regardless of
outcome. Single-call batch (role actions) keeps plain success/error toasts.

## Wording

All "invite" language in the admin UI becomes "add" (button, dialog title
"Add users to {workspace}", toasts). API client function name `inviteMember`
and the backend route are unchanged.

## Verification

No admin test harness exists; none added. Gate = `tsc` build plus manual
pass: exercise all five pickers against the dev service (`make start` +
`make admin`), including search, group select-all, disabled rows, partial
failure toast, both themes.

## Out of scope

- Backend/API changes, endpoint renames
- Pagination inside the picker beyond first 20 user results
- Keyboard navigation / cmdk combobox
- Regrouping the assigned-actions list display
