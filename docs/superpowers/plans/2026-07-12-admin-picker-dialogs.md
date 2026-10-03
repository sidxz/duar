# Admin Picker Dialogs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all five native-`<select>` "add X" pickers in the admin WorkspaceDetail page with one shared searchable multi-select dialog, and rename the members flow from "Invite" to "Add Users".

**Architecture:** One new component `AddItemsDialog` (search input + grouped checkbox list + batch "Add N" commit) plus an exported `batchAdd` helper for multi-call commits. Five call sites in `admin/src/pages/WorkspaceDetail.tsx` swap their inline `select + Add` rows for a button that opens the dialog. No backend or API-client changes.

**Tech Stack:** React 19 + TypeScript, shadcn/ui primitives already in repo (Dialog, Checkbox, Input, Button), @tanstack/react-query v5, sonner toasts.

**Spec:** `docs/superpowers/specs/2026-07-12-admin-picker-dialogs-design.md`

## Global Constraints

- **No new npm dependencies** (no cmdk, no popover).
- **No backend changes**; API client functions keep their names (`inviteMember` stays `inviteMember`).
- All user-facing "invite" wording becomes "add" (button `+ Add Users`, dialog title `Add users to {name}`, toasts say "Added …").
- Semantic theme tokens only (`bg-muted`, `text-muted-foreground`, `border-border`, …) — never `zinc-*` or raw colors; must look right in light AND dark themes.
- Verification gate per task: `cd admin && npx tsc -b` passes (admin has no test harness; do not add one).
- Tasks 2–5 edit the same file (`WorkspaceDetail.tsx`) — execute them **in order**; anchors are code snippets, not line numbers.
- Working branch: `admin-picker-dialogs` (already created; spec committed).

### Known spec deviation

The spec says role-group picker rows show `"N members"` as sublabel. The `Group` type (`admin/src/types/api.ts`) has no `member_count`; use `description` as the sublabel instead. (The assigned-groups *list* keeps showing member counts — those rows come from `RoleGroup`, which has it.)

---

### Task 1: `AddItemsDialog` component + `batchAdd` helper

**Files:**
- Create: `admin/src/components/AddItemsDialog.tsx`

**Interfaces:**
- Consumes: shadcn primitives `@/components/ui/{dialog,checkbox,button,input}`, `sonner` toast.
- Produces (used verbatim by Tasks 2–5):
  ```ts
  export type PickerItem = {
    id: string;
    label: string;
    sublabel?: string;
    group?: string;
    disabled?: boolean;
    disabledReason?: string;
  };
  export function AddItemsDialog(props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    items: PickerItem[];
    onSearch?: (q: string) => void;   // present = server-search mode
    isLoading?: boolean;
    onAdd: (items: PickerItem[]) => Promise<void>; // throw → dialog stays open
    addLabel: (n: number) => string;
    footer?: ReactNode;
  }): JSX.Element;
  export function batchAdd(
    selected: PickerItem[],
    call: (item: PickerItem) => Promise<unknown>,
    noun: string,
  ): Promise<void>; // never throws; toasts summary itself
  ```

- [ ] **Step 1: Write the component**

Create `admin/src/components/AddItemsDialog.tsx` with exactly:

```tsx
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

export type PickerItem = {
  id: string;
  label: string;
  sublabel?: string;
  group?: string;
  disabled?: boolean;
  disabledReason?: string;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  items: PickerItem[];
  /** Present = server-search mode: debounced query is pushed up, parent re-renders items. */
  onSearch?: (q: string) => void;
  isLoading?: boolean;
  /**
   * Receives the full selected items — in server-search mode selections can
   * span multiple search pages, so callers must not re-derive them from the
   * currently rendered list. Throw to keep the dialog open (caller surfaces
   * its own error toast).
   */
  onAdd: (items: PickerItem[]) => Promise<void>;
  addLabel: (n: number) => string;
  /** Rendered left of Cancel/Add — e.g. the batch role select. */
  footer?: ReactNode;
};

export function AddItemsDialog({
  open,
  onOpenChange,
  title,
  items,
  onSearch,
  isLoading,
  onAdd,
  addLabel,
  footer,
}: Props) {
  const [query, setQuery] = useState("");
  // Map keeps the full PickerItem per selected id: in server mode the list
  // under the checkboxes changes with every search, so ids alone couldn't be
  // resolved back to items at add-time.
  const [selected, setSelected] = useState<Map<string, PickerItem>>(new Map());
  const [pending, setPending] = useState(false);

  // Fresh state every time the dialog opens (guards stale-picker bugs, cf. 563e53f).
  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(new Map());
    }
  }, [open]);

  // Server mode: debounce query up to the parent.
  useEffect(() => {
    if (!onSearch) return;
    const t = setTimeout(() => onSearch(query), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const visible = useMemo(() => {
    if (onSearch) return items; // server already filtered
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((i) =>
      [i.label, i.sublabel, i.group].some((s) => s?.toLowerCase().includes(q)),
    );
  }, [items, query, onSearch]);

  const groups = useMemo(() => {
    const m = new Map<string, PickerItem[]>();
    for (const i of visible) {
      const g = i.group ?? "";
      if (!m.has(g)) m.set(g, []);
      m.get(g)!.push(i);
    }
    return [...m.entries()];
  }, [visible]);

  const toggle = (item: PickerItem) =>
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(item.id)) next.delete(item.id);
      else next.set(item.id, item);
      return next;
    });

  const toggleGroup = (groupItems: PickerItem[]) => {
    const enabled = groupItems.filter((i) => !i.disabled);
    const allIn = enabled.length > 0 && enabled.every((i) => selected.has(i.id));
    setSelected((prev) => {
      const next = new Map(prev);
      for (const i of enabled) {
        if (allIn) next.delete(i.id);
        else next.set(i.id, i);
      }
      return next;
    });
  };

  const handleAdd = async () => {
    setPending(true);
    try {
      await onAdd([...selected.values()]);
      onOpenChange(false);
    } catch {
      // caller toasted; keep dialog open
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <Input
          autoFocus
          placeholder="Search…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="max-h-[50vh] overflow-y-auto rounded-md border border-border">
          {isLoading &&
            [0, 1, 2].map((n) => (
              <div key={n} className="px-3 py-2.5">
                <div className="h-4 w-2/3 rounded bg-muted animate-pulse" />
              </div>
            ))}
          {!isLoading && visible.length === 0 && (
            <div className="px-3 py-8 text-center text-sm text-muted-foreground">
              {items.length === 0 && !query.trim()
                ? "Nothing left to add"
                : "No matches"}
            </div>
          )}
          {!isLoading &&
            groups.map(([group, groupItems]) => {
              const enabled = groupItems.filter((i) => !i.disabled);
              const allIn =
                enabled.length > 0 && enabled.every((i) => selected.has(i.id));
              return (
                <div key={group || "__flat__"}>
                  {group && (
                    <div className="sticky top-0 z-10 flex items-center justify-between bg-muted px-3 py-1.5">
                      <span className="font-mono text-xs font-medium text-muted-foreground">
                        {group}
                      </span>
                      <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                        select all
                        <Checkbox
                          checked={allIn}
                          disabled={enabled.length === 0}
                          onCheckedChange={() => toggleGroup(groupItems)}
                        />
                      </label>
                    </div>
                  )}
                  {groupItems.map((item) => (
                    <label
                      key={item.id}
                      className={`flex items-center gap-2.5 px-3 py-2 ${
                        item.disabled
                          ? "opacity-50"
                          : "cursor-pointer hover:bg-muted/50"
                      }`}
                    >
                      <Checkbox
                        checked={selected.has(item.id)}
                        disabled={item.disabled}
                        onCheckedChange={() => toggle(item)}
                      />
                      <span className="min-w-0 flex-1 text-sm">
                        <span className="text-foreground">{item.label}</span>
                        {item.sublabel && (
                          <span className="ml-2 text-xs text-muted-foreground">
                            {item.sublabel}
                          </span>
                        )}
                      </span>
                      {item.disabled && item.disabledReason && (
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {item.disabledReason}
                        </span>
                      )}
                    </label>
                  ))}
                </div>
              );
            })}
        </div>
        <DialogFooter className="items-center gap-2 sm:justify-between">
          <span className="text-xs text-muted-foreground">
            {selected.size} selected
          </span>
          <div className="flex items-center gap-2">
            {footer}
            <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={selected.size === 0 || pending}
              onClick={handleAdd}
            >
              {addLabel(selected.size)}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Run one call per item, then toast a single summary. Never throws. */
// eslint-disable-next-line react-refresh/only-export-components
export async function batchAdd(
  selected: PickerItem[],
  call: (item: PickerItem) => Promise<unknown>,
  noun: string,
): Promise<void> {
  const results = await Promise.allSettled(selected.map((i) => call(i)));
  const failures = results
    .map((result, idx) => ({ result, item: selected[idx] }))
    .filter(
      (x): x is { result: PromiseRejectedResult; item: PickerItem } =>
        x.result.status === "rejected",
    );
  const added = selected.length - failures.length;
  if (failures.length === 0) {
    toast.success(`Added ${added} ${noun}${added === 1 ? "" : "s"}`);
  } else {
    toast.error(
      `Added ${added}, ${failures.length} failed: ${failures
        .map(
          (f) =>
            `${f.item.label} — ${
              f.result.reason instanceof Error
                ? f.result.reason.message
                : "error"
            }`,
        )
        .join("; ")}`,
    );
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `cd admin && npx tsc -b`
Expected: exit 0, no output. (Component is not imported anywhere yet — that's fine, it must still compile.)

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/AddItemsDialog.tsx
git commit -m "feat(admin): shared AddItemsDialog multi-select picker + batchAdd helper"
```

---

### Task 2: Members tab → "+ Add Users" dialog

**Files:**
- Modify: `admin/src/pages/WorkspaceDetail.tsx` (function `MembersTab`)

**Interfaces:**
- Consumes: `AddItemsDialog`, `PickerItem`, `batchAdd` from `../components/AddItemsDialog`; existing `getUsers`, `inviteMember`, `getWorkspaceMembers` from `../api/client`; `keepPreviousData` from `@tanstack/react-query`.
- Produces: nothing new for later tasks (call-site pattern only).

- [ ] **Step 1: Update imports**

In `WorkspaceDetail.tsx`, change the react-query import line to include `keepPreviousData`:

```tsx
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
```

Add `getUsers` to the existing `../api/client` import list (alphabetical position, after `getServiceActions`). Add below the other component imports:

```tsx
import { AddItemsDialog, batchAdd, type PickerItem } from "../components/AddItemsDialog";
```

- [ ] **Step 2: Replace MembersTab state, invite mutation, and modal**

Inside `function MembersTab`, **delete**:
- the state lines for `showInvite`, `inviteEmail`, `inviteRole`
- the whole `const invite = useMutation({ ... });` block
- the whole `<Modal open={showInvite} ...>…</Modal>` JSX block at the bottom of the returned tree

**Add** in their place (state at top of the function, after `const queryClient…`):

```tsx
const [showAdd, setShowAdd] = useState(false);
const [userQuery, setUserQuery] = useState("");
const [batchRole, setBatchRole] = useState("viewer");

const { data: userPage } = useQuery({
  queryKey: ["user-picker", userQuery],
  queryFn: () => getUsers(1, 20, userQuery || undefined),
  enabled: showAdd,
  placeholderData: keepPreviousData,
});
```

Below the existing `if (isLoading) return …` line, build the items:

```tsx
const memberIds = new Set(members.map((m) => m.user_id));
const foundUsers = userPage?.items ?? [];
const userItems: PickerItem[] = foundUsers.map((u) => ({
  id: u.id,
  label: u.name,
  sublabel: u.email,
  disabled: memberIds.has(u.id),
  disabledReason: "already a member",
}));
```

- [ ] **Step 3: Swap the button and mount the dialog**

Replace the `+ Invite Member` button JSX with:

```tsx
<Button variant="outline" size="sm" onClick={() => setShowAdd(true)}>
  + Add Users
</Button>
```

Where the old `<Modal>` block was, render instead:

```tsx
<AddItemsDialog
  open={showAdd}
  onOpenChange={setShowAdd}
  title="Add users to this workspace"
  items={userItems}
  onSearch={setUserQuery}
  isLoading={showAdd && !userPage}
  addLabel={(n) => `Add ${n} user${n === 1 ? "" : "s"}`}
  footer={
    <select
      value={batchRole}
      onChange={(e) => setBatchRole(e.target.value)}
      className={selectClass}
      aria-label="Role for added users"
    >
      {["viewer", "editor", "admin", "owner"].map((r) => (
        <option key={r} value={r}>{r}</option>
      ))}
    </select>
  }
  onAdd={async (sel) => {
    // sublabel carries the email for user items; selections may span searches,
    // so `sel` (not the current page) is the source of truth.
    await batchAdd(
      sel,
      (i) => inviteMember(workspaceId, i.sublabel!, batchRole),
      "user",
    );
    queryClient.invalidateQueries({ queryKey: ["workspace-members", workspaceId] });
  }}
/>
```

- [ ] **Step 4: Typecheck**

Run: `cd admin && npx tsc -b`
Expected: exit 0. If `Modal` is now unused in this file, that's fine — other components (`GroupsTab`, `RolesTab`) still use it; do NOT remove the `Modal` import.

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/WorkspaceDetail.tsx
git commit -m "feat(admin): Members tab adds existing users via searchable multi-select dialog"
```

---

### Task 3: Role → Actions picker (grouped by service, single batch call)

**Files:**
- Modify: `admin/src/pages/WorkspaceDetail.tsx` (function `RolesTab`, Actions section)

**Interfaces:**
- Consumes: `AddItemsDialog`, `PickerItem` (Task 1); existing `addRoleActions(roleId, serviceActionIds: string[])` — already batched.
- Produces: nothing new.

- [ ] **Step 1: Replace action-picker state and mutation**

In `function RolesTab`, **delete**:
- state line `const [selectedActionId, setSelectedActionId] = useState("");`
- the whole `const addAction = useMutation({ ... });` block
- the derived list:
  ```tsx
  const availableActions = allActions.filter(
    (a) => !roleActions.some((ra) => ra.id === a.id),
  );
  ```

**Add** state (top of `RolesTab`, with the other `useState` lines):

```tsx
const [showAddActions, setShowAddActions] = useState(false);
```

(Task 4 adds `showAddMembers`/`showAddGroups` when it wires those dialogs — declaring them earlier trips `noUnusedLocals`.)

**Add** derived items where `availableActions` was:

```tsx
const assignedActionIds = new Set(roleActions.map((a) => a.id));
const actionItems: PickerItem[] = allActions.map((a) => ({
  id: a.id,
  label: a.action,
  sublabel: a.description ?? undefined,
  group: a.service_name,
  disabled: assignedActionIds.has(a.id),
  disabledReason: "already assigned",
}));
```

In the role-row `onClick` that expands/collapses (`setExpandedRole(...)`), delete only the `setSelectedActionId("");` line and add this one (leave `setAddMemberEmail("")` and `setSelectedGroupId("")` untouched — Task 4 removes them with their state):

```tsx
setShowAddActions(false);
```

- [ ] **Step 2: Replace the Actions section picker row**

In the expanded-role JSX, replace this block (the `select` + `Add` button inside the Actions section):

```tsx
<div className="flex items-center gap-2">
  <select
    value={selectedActionId}
    ...
  </select>
  <Button ... >Add</Button>
</div>
```

with a header row + dialog:

```tsx
<div className="flex items-center justify-between">
  <div className="text-xs font-medium text-muted-foreground">
    {roleActions.length} assigned
  </div>
  <Button variant="outline" size="sm" onClick={() => setShowAddActions(true)}>
    + Add Actions
  </Button>
</div>
<AddItemsDialog
  open={showAddActions}
  onOpenChange={setShowAddActions}
  title={`Add actions to ${r.name}`}
  items={actionItems}
  addLabel={(n) => `Add ${n} action${n === 1 ? "" : "s"}`}
  onAdd={async (sel) => {
    try {
      await addRoleActions(expandedRole!, sel.map((i) => i.id));
    } catch (e) {
      toast.error((e as Error).message);
      throw e;
    }
    queryClient.invalidateQueries({ queryKey: ["role-actions", expandedRole] });
    queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
  }}
/>
```

Note: the section already renders a `Actions` label div above this; keep it. The existing assigned-actions list below stays unchanged.

- [ ] **Step 3: Typecheck**

Run: `cd admin && npx tsc -b`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add admin/src/pages/WorkspaceDetail.tsx
git commit -m "feat(admin): role action picker becomes service-grouped multi-select dialog"
```

---

### Task 4: Role → Members and Role → Groups pickers

**Files:**
- Modify: `admin/src/pages/WorkspaceDetail.tsx` (function `RolesTab`, Members + Groups sections)

**Interfaces:**
- Consumes: `AddItemsDialog`, `PickerItem`, `batchAdd` (Task 1); existing `addRoleMember`, `addRoleGroup` client functions.
- Produces: nothing new.

- [ ] **Step 1: Delete single-pick state and mutations**

In `function RolesTab`, **delete**:
- state lines for `addMemberEmail` and `selectedGroupId`
- the whole `const addMember = useMutation({ ... });` and `const addGroup = useMutation({ ... });` blocks
- the line `const selectedMember = members.find((m) => m.email === addMemberEmail);`
- in the expand/collapse `onClick`, the `setAddMemberEmail("")` / `setSelectedGroupId("")` lines

**Add** state (next to `showAddActions` from Task 3):

```tsx
const [showAddMembers, setShowAddMembers] = useState(false);
const [showAddGroups, setShowAddGroups] = useState(false);
```

and in the same expand/collapse `onClick`, next to `setShowAddActions(false);`:

```tsx
setShowAddMembers(false);
setShowAddGroups(false);
```

**Add** derived items next to `actionItems` (from Task 3):

```tsx
const roleMemberIds = new Set(roleMembers.map((rm) => rm.user_id));
const roleMemberItems: PickerItem[] = members.map((m) => ({
  id: m.user_id,
  label: m.name,
  sublabel: m.email,
  disabled: roleMemberIds.has(m.user_id),
  disabledReason: "already assigned",
}));
const roleGroupIds = new Set(roleGroups.map((rg) => rg.group_id));
const roleGroupItems: PickerItem[] = groups.map((g) => ({
  id: g.id,
  label: g.name,
  sublabel: g.description ?? undefined,
  disabled: roleGroupIds.has(g.id),
  disabledReason: "already assigned",
}));
```

- [ ] **Step 2: Replace the Members section picker row**

Replace the `select` + `Add` row in the role Members section with:

```tsx
<div className="flex items-center justify-between">
  <div className="text-xs font-medium text-muted-foreground">
    {roleMembers.length} assigned
  </div>
  <Button variant="outline" size="sm" onClick={() => setShowAddMembers(true)}>
    + Add Members
  </Button>
</div>
<AddItemsDialog
  open={showAddMembers}
  onOpenChange={setShowAddMembers}
  title={`Add members to ${r.name}`}
  items={roleMemberItems}
  addLabel={(n) => `Add ${n} member${n === 1 ? "" : "s"}`}
  onAdd={async (sel) => {
    await batchAdd(sel, (i) => addRoleMember(expandedRole!, i.id), "member");
    queryClient.invalidateQueries({ queryKey: ["role-members", expandedRole] });
    queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
  }}
/>
```

- [ ] **Step 3: Replace the Groups section picker row**

Replace the `select` + `Add` row in the role Groups section with:

```tsx
<div className="flex items-center justify-between">
  <div className="text-xs font-medium text-muted-foreground">
    {roleGroups.length} assigned
  </div>
  <Button variant="outline" size="sm" onClick={() => setShowAddGroups(true)}>
    + Add Groups
  </Button>
</div>
<AddItemsDialog
  open={showAddGroups}
  onOpenChange={setShowAddGroups}
  title={`Add groups to ${r.name}`}
  items={roleGroupItems}
  addLabel={(n) => `Add ${n} group${n === 1 ? "" : "s"}`}
  onAdd={async (sel) => {
    await batchAdd(sel, (i) => addRoleGroup(expandedRole!, i.id), "group");
    queryClient.invalidateQueries({ queryKey: ["role-groups", expandedRole] });
    queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
  }}
/>
```

- [ ] **Step 4: Typecheck**

Run: `cd admin && npx tsc -b`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/WorkspaceDetail.tsx
git commit -m "feat(admin): role member and group pickers become multi-select dialogs"
```

---

### Task 5: Group → Members picker

**Files:**
- Modify: `admin/src/pages/WorkspaceDetail.tsx` (function `GroupsTab`)

**Interfaces:**
- Consumes: `AddItemsDialog`, `PickerItem`, `batchAdd` (Task 1); existing `addGroupMember` client function.
- Produces: nothing new.

- [ ] **Step 1: Delete single-pick state and mutation**

In `function GroupsTab`, **delete**:
- state line `const [addMemberEmail, setAddMemberEmail] = useState("");`
- the whole `const addMember = useMutation({ ... });` block
- the line `const selectedMember = members.find((m) => m.email === addMemberEmail);`

**Add** state at the top of `GroupsTab`:

```tsx
const [showAddMembers, setShowAddMembers] = useState(false);
```

In the group-row expand/collapse `onClick` (`setExpandedGroup(...)`), add reset:

```tsx
setShowAddMembers(false);
```

**Add** derived items after the `if (isLoading) return …` line:

```tsx
const groupMemberIds = new Set(groupMembers.map((gm) => gm.user_id));
const groupMemberItems: PickerItem[] = members.map((m) => ({
  id: m.user_id,
  label: m.name,
  sublabel: m.email,
  disabled: groupMemberIds.has(m.user_id),
  disabledReason: "already a member",
}));
```

- [ ] **Step 2: Replace the picker row in the expanded group**

Replace the `select` + `Add` row inside the expanded-group block with:

```tsx
<div className="flex items-center justify-between pt-2">
  <div className="text-xs font-medium text-muted-foreground">
    {groupMembers.length} member{groupMembers.length === 1 ? "" : "s"}
  </div>
  <Button variant="outline" size="sm" onClick={() => setShowAddMembers(true)}>
    + Add Members
  </Button>
</div>
<AddItemsDialog
  open={showAddMembers}
  onOpenChange={setShowAddMembers}
  title={`Add members to ${g.name}`}
  items={groupMemberItems}
  addLabel={(n) => `Add ${n} member${n === 1 ? "" : "s"}`}
  onAdd={async (sel) => {
    await batchAdd(sel, (i) => addGroupMember(expandedGroup!, i.id), "member");
    queryClient.invalidateQueries({ queryKey: ["group-members", expandedGroup] });
  }}
/>
```

- [ ] **Step 3: Typecheck**

Run: `cd admin && npx tsc -b`
Expected: exit 0. Also run `cd admin && npm run lint` once here (covers all edits so far); fix any unused-import complaints it raises.

- [ ] **Step 4: Commit**

```bash
git add admin/src/pages/WorkspaceDetail.tsx
git commit -m "feat(admin): group member picker becomes multi-select dialog"
```

---

### Task 6: Manual verification pass

**Files:** none (verification only; fix-ups allowed anywhere touched above)

- [ ] **Step 1: Build gate**

Run: `cd admin && npm run build`
Expected: `tsc -b` and `vite build` both succeed.

- [ ] **Step 2: Run service + admin**

Run (two shells): `make start` and `make admin` (service :9003, admin :9004; `make seed` first if the dev DB is empty). Log into the admin panel, open a workspace with members/groups/roles.

- [ ] **Step 3: Exercise all five pickers**

Checklist:
1. Members tab: "+ Add Users" opens dialog; typing filters via server search; an existing member appears grayed with "already a member"; select 2 users + role `editor` → "Add 2 users" → both appear in list with editor role; toast "Added 2 users".
2. Members tab partial failure: try adding a user blocked by an org restriction (or re-add raced member) → dialog closes, error toast lists `name — reason`, the addable one still got added.
3. Role → Actions: dialog groups by service with sticky headers; "select all" on one service selects only that service's enabled rows; search narrows across groups; "Add N actions" lands them in one request; already-assigned rows grayed.
4. Role → Members and Role → Groups: multi-add works, counts on the role row update, switching expanded roles never leaks selection state (dialog reopens empty).
5. Group → Members: multi-add works; when every workspace member is already in the group, all rows render grayed with "already a member" ("Nothing left to add" appears only for an empty source list).
6. Toggle dark mode: dialog, sticky group headers, disabled rows, and skeletons all use theme tokens correctly in both themes.

- [ ] **Step 4: Fix anything found, re-verify, commit fixes**

```bash
git add -A admin/src
git commit -m "fix(admin): picker dialog polish from manual verification"
```

(Skip the commit if nothing needed fixing.)
