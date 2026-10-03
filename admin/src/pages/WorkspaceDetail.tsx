import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Copy } from "lucide-react";
import {
  addGroupMember,
  addRoleActions,
  addRoleGroup,
  addRoleMember,
  createGroup,
  createRole,
  deleteGroup,
  deleteRole,
  deleteWorkspace,
  getGroupMembers,
  getOrganizations,
  getRoleActions,
  getRoleGroups,
  getRoleMembers,
  getServiceActions,
  getUsers,
  getWorkspace,
  getWorkspaceAllowedOrgs,
  getWorkspaceGroups,
  getWorkspaceMembers,
  getWorkspaceRoles,
  inviteMember,
  removeMember,
  removeGroupMember,
  removeRoleAction,
  removeRoleGroup,
  removeRoleMember,
  setWorkspaceAllowedOrgs,
  updateGroup,
  updateMemberRole,
  updateRole,
  updateWorkspace,
} from "../api/client";
import { ConfirmModal } from "../components/ConfirmModal";
import { Modal } from "../components/Modal";
import { AddItemsDialog, batchAdd, type PickerItem } from "../components/AddItemsDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ActionsInsightsView } from "./ActionsInsights";

const TABS = ["Members", "Groups", "Roles", "Access", "Usage"] as const;
type Tab = (typeof TABS)[number];

const selectClass =
  "rounded border border-border bg-background px-2 py-1 text-xs text-foreground";

export function WorkspaceDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("Members");
  const [showEdit, setShowEdit] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deleteSlug, setDeleteSlug] = useState("");
  const [editForm, setEditForm] = useState({ name: "", description: "" });

  const { data: workspace, isLoading, isError, error } = useQuery({
    queryKey: ["workspace", id],
    queryFn: () => getWorkspace(id!),
    enabled: !!id,
  });

  const update = useMutation({
    mutationFn: () =>
      updateWorkspace(id!, {
        name: editForm.name || undefined,
        description: editForm.description,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", id] });
      setShowEdit(false);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteWorkspace(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      navigate("/workspaces");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const openEdit = () => {
    if (workspace) {
      setEditForm({ name: workspace.name, description: workspace.description ?? "" });
    }
    setShowEdit(true);
  };

  if (isLoading) {
    return <div className="animate-pulse h-64 bg-muted/50 rounded-lg" />;
  }

  if (isError || !workspace) {
    return (
      <div className="space-y-3">
        <Link
          to="/workspaces"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Workspaces
        </Link>
        <div className="border border-border rounded-lg p-6 text-center">
          <p className="text-sm text-foreground">
            {(error as Error)?.message ?? "Workspace not found."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/workspaces" className="hover:text-foreground">Workspaces</Link>
        <span>/</span>
        <span className="text-foreground">{workspace.name}</span>
      </div>

      {/* Header */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">{workspace.name}</h2>
            <div className="text-sm text-muted-foreground mt-0.5 flex items-center gap-1.5">
              <span className="font-mono">{workspace.slug}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => {
                  navigator.clipboard.writeText(workspace.slug);
                  toast.success("Copied");
                }}
                aria-label="Copy slug"
              >
                <Copy className="w-3.5 h-3.5" />
              </Button>
            </div>
            {workspace.description && (
              <div className="text-sm text-muted-foreground mt-2">{workspace.description}</div>
            )}
            <div className="text-xs text-muted-foreground mt-2">
              Created {new Date(workspace.created_at).toLocaleDateString()}
              {" · "}{workspace.member_count} members · {workspace.group_count} groups
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={openEdit}>
              Edit
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => { setShowDelete(true); setDeleteSlug(""); }}
            >
              Delete
            </Button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
              tab === t
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "Members" && <MembersTab workspaceId={id!} />}
      {tab === "Groups" && <GroupsTab workspaceId={id!} />}
      {tab === "Roles" && <RolesTab workspaceId={id!} />}
      {tab === "Access" && <AccessTab workspaceId={id!} />}
      {tab === "Usage" && <ActionsInsightsView workspaceId={id!} />}

      {/* Edit modal */}
      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Workspace">
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="ws-edit-name">Name</Label>
            <Input
              id="ws-edit-name"
              value={editForm.name}
              onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ws-edit-description">Description</Label>
            <Input
              id="ws-edit-description"
              value={editForm.description}
              onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowEdit(false)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => update.mutate()}
              disabled={update.isPending}
            >
              Save
            </Button>
          </div>
        </div>
      </Modal>

      {/* Delete confirmation */}
      <ConfirmModal
        open={showDelete}
        onClose={() => setShowDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete Workspace"
        message={`This will permanently delete "${workspace.name}" and all its memberships and groups.`}
        confirmLabel="Delete Workspace"
        danger
        isPending={remove.isPending}
        confirmInput={workspace.slug}
        confirmInputValue={deleteSlug}
        onConfirmInputChange={setDeleteSlug}
      />
    </div>
  );
}

function MembersTab({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [userQuery, setUserQuery] = useState("");
  const [batchRole, setBatchRole] = useState("viewer");

  const { data: userPage } = useQuery({
    queryKey: ["user-picker", userQuery],
    queryFn: () => getUsers(1, 20, userQuery || undefined),
    enabled: showAdd,
    placeholderData: keepPreviousData,
  });

  const { data: members = [], isLoading } = useQuery({
    queryKey: ["workspace-members", workspaceId],
    queryFn: () => getWorkspaceMembers(workspaceId),
  });

  const changeRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      updateMemberRole(workspaceId, userId, role),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["workspace-members", workspaceId] }),
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: (userId: string) => removeMember(workspaceId, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["workspace-members", workspaceId] }),
    onError: (e) => toast.error((e as Error).message),
  });

  if (isLoading) return <div className="h-32 bg-muted/50 rounded-lg animate-pulse" />;

  const memberIds = new Set(members.map((m) => m.user_id));
  const foundUsers = userPage?.items ?? [];
  const userItems: PickerItem[] = foundUsers.map((u) => ({
    id: u.id,
    label: u.name,
    sublabel: u.email,
    disabled: memberIds.has(u.id),
    disabledReason: "already a member",
  }));

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => setShowAdd(true)}>
          + Add Users
        </Button>
      </div>

      <div className="rounded-lg border border-border divide-y divide-border">
        {members.map((m) => (
          <div key={m.user_id} className="flex items-center gap-3 px-4 py-3">
            <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-xs font-medium text-muted-foreground shrink-0">
              {m.name.charAt(0).toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <Link to={`/users/${m.user_id}`} className="text-sm font-medium hover:underline">{m.name}</Link>
              <div className="text-xs text-muted-foreground">{m.email}</div>
            </div>
            <select
              value={m.role}
              onChange={(e) => changeRole.mutate({ userId: m.user_id, role: e.target.value })}
              className={selectClass}
            >
              {["owner", "admin", "editor", "viewer"].map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => remove.mutate(m.user_id)}
              className="text-destructive hover:text-destructive"
            >
              Remove
            </Button>
          </div>
        ))}
        {members.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">No members</div>
        )}
      </div>

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
    </div>
  );
}

function GroupsTab({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editingGroup, setEditingGroup] = useState<string | null>(null);
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", description: "" });
  const [showAddMembers, setShowAddMembers] = useState(false);

  const { data: groups = [], isLoading } = useQuery({
    queryKey: ["workspace-groups", workspaceId],
    queryFn: () => getWorkspaceGroups(workspaceId),
  });

  const { data: members = [] } = useQuery({
    queryKey: ["workspace-members", workspaceId],
    queryFn: () => getWorkspaceMembers(workspaceId),
  });

  const { data: groupMembers = [] } = useQuery({
    queryKey: ["group-members", expandedGroup],
    queryFn: () => getGroupMembers(expandedGroup!),
    enabled: !!expandedGroup,
  });

  const create = useMutation({
    mutationFn: () => createGroup(workspaceId, { name: form.name, description: form.description || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-groups", workspaceId] });
      setShowCreate(false);
      setForm({ name: "", description: "" });
    },
  });

  const edit = useMutation({
    mutationFn: () => updateGroup(editingGroup!, { name: form.name, description: form.description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-groups", workspaceId] });
      setEditingGroup(null);
      setForm({ name: "", description: "" });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const del = useMutation({
    mutationFn: (gid: string) => deleteGroup(gid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-groups", workspaceId] });
      setExpandedGroup(null);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeMemberMut = useMutation({
    mutationFn: (userId: string) => removeGroupMember(expandedGroup!, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["group-members", expandedGroup] }),
    onError: (e) => toast.error((e as Error).message),
  });

  if (isLoading) return <div className="h-32 bg-muted/50 rounded-lg animate-pulse" />;

  const groupMemberIds = new Set(groupMembers.map((gm) => gm.user_id));
  const groupMemberItems: PickerItem[] = members.map((m) => ({
    id: m.user_id,
    label: m.name,
    sublabel: m.email,
    disabled: groupMemberIds.has(m.user_id),
    disabledReason: "already a member",
  }));

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button
          variant="outline"
          size="sm"
          onClick={() => { setShowCreate(true); setForm({ name: "", description: "" }); }}
        >
          + Create Group
        </Button>
      </div>

      <div className="rounded-lg border border-border divide-y divide-border">
        {groups.map((g) => (
          <div key={g.id}>
            <div
              className="flex items-center justify-between px-4 py-3 hover:bg-muted/50 cursor-pointer transition-colors"
              onClick={() => {
                setExpandedGroup(expandedGroup === g.id ? null : g.id);
                setShowAddMembers(false);
              }}
            >
              <div>
                <div className="text-sm font-medium">{g.name}</div>
                {g.description && <div className="text-xs text-muted-foreground mt-0.5">{g.description}</div>}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingGroup(g.id);
                    setForm({ name: g.name, description: g.description ?? "" });
                  }}
                >
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => { e.stopPropagation(); del.mutate(g.id); }}
                  className="text-destructive hover:text-destructive"
                >
                  Delete
                </Button>
                <span className="text-xs text-muted-foreground">{expandedGroup === g.id ? "▲" : "▼"}</span>
              </div>
            </div>

            {/* Expanded group members */}
            {expandedGroup === g.id && (
              <div className="px-4 pb-3 space-y-2 bg-muted/50">
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
                <div className="divide-y divide-border">
                  {groupMembers.map((gm) => (
                    <div key={gm.user_id} className="flex items-center justify-between py-2">
                      <div className="text-sm">
                        <span className="text-foreground">{gm.name}</span>
                        <span className="text-muted-foreground ml-2 text-xs">{gm.email}</span>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeMemberMut.mutate(gm.user_id)}
                        className="text-destructive hover:text-destructive"
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                  {groupMembers.length === 0 && (
                    <div className="py-2 text-xs text-muted-foreground">No members in this group</div>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        {groups.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">No groups</div>
        )}
      </div>

      {/* Create group modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Create Group">
        <div className="space-y-3">
          <Input
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="Group name"
          />
          <Input
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Description (optional)"
          />
          {create.isError && (
            <div className="text-xs text-destructive">{(create.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => create.mutate()}
              disabled={!form.name || create.isPending}
            >
              Create
            </Button>
          </div>
        </div>
      </Modal>

      {/* Edit group modal */}
      <Modal open={!!editingGroup} onClose={() => setEditingGroup(null)} title="Edit Group">
        <div className="space-y-3">
          <Input
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
          <Input
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Description"
          />
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setEditingGroup(null)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => edit.mutate()}
              disabled={edit.isPending}
            >
              Save
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function RolesTab({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editingRole, setEditingRole] = useState<string | null>(null);
  const [expandedRole, setExpandedRole] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", description: "" });
  const [showAddActions, setShowAddActions] = useState(false);
  const [showAddMembers, setShowAddMembers] = useState(false);
  const [showAddGroups, setShowAddGroups] = useState(false);

  const { data: roles = [], isLoading } = useQuery({
    queryKey: ["workspace-roles", workspaceId],
    queryFn: () => getWorkspaceRoles(workspaceId),
  });

  const { data: members = [] } = useQuery({
    queryKey: ["workspace-members", workspaceId],
    queryFn: () => getWorkspaceMembers(workspaceId),
  });

  const { data: allActions = [] } = useQuery({
    queryKey: ["service-actions"],
    queryFn: () => getServiceActions(),
  });

  const { data: roleActions = [] } = useQuery({
    queryKey: ["role-actions", expandedRole],
    queryFn: () => getRoleActions(expandedRole!),
    enabled: !!expandedRole,
  });

  const { data: roleMembers = [] } = useQuery({
    queryKey: ["role-members", expandedRole],
    queryFn: () => getRoleMembers(expandedRole!),
    enabled: !!expandedRole,
  });

  const { data: groups = [] } = useQuery({
    queryKey: ["workspace-groups", workspaceId],
    queryFn: () => getWorkspaceGroups(workspaceId),
  });

  const { data: roleGroups = [] } = useQuery({
    queryKey: ["role-groups", expandedRole],
    queryFn: () => getRoleGroups(expandedRole!),
    enabled: !!expandedRole,
  });

  const create = useMutation({
    mutationFn: () => createRole(workspaceId, { name: form.name, description: form.description || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
      setShowCreate(false);
      setForm({ name: "", description: "" });
    },
  });

  const edit = useMutation({
    mutationFn: () => updateRole(editingRole!, { name: form.name, description: form.description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
      setEditingRole(null);
      setForm({ name: "", description: "" });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const del = useMutation({
    mutationFn: (rid: string) => deleteRole(rid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
      setExpandedRole(null);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeAction = useMutation({
    mutationFn: (actionId: string) => removeRoleAction(expandedRole!, actionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["role-actions", expandedRole] });
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeMemberMut = useMutation({
    mutationFn: (userId: string) => removeRoleMember(expandedRole!, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["role-members", expandedRole] });
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeGroupMut = useMutation({
    mutationFn: (groupId: string) => removeRoleGroup(expandedRole!, groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["role-groups", expandedRole] });
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  if (isLoading) return <div className="h-32 bg-muted/50 rounded-lg animate-pulse" />;

  const assignedActionIds = new Set(roleActions.map((a) => a.id));
  const actionItems: PickerItem[] = allActions.map((a) => ({
    id: a.id,
    label: a.action,
    sublabel: a.description ?? undefined,
    group: a.service_name,
    disabled: assignedActionIds.has(a.id),
    disabledReason: "already assigned",
  }));
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

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button
          variant="outline"
          size="sm"
          onClick={() => { setShowCreate(true); setForm({ name: "", description: "" }); }}
        >
          + Create Role
        </Button>
      </div>

      <div className="rounded-lg border border-border divide-y divide-border">
        {roles.map((r) => (
          <div key={r.id}>
            <div
              className="flex items-center justify-between px-4 py-3 hover:bg-muted/50 cursor-pointer transition-colors"
              onClick={() => {
                setExpandedRole(expandedRole === r.id ? null : r.id);
                setShowAddActions(false);
                setShowAddMembers(false);
                setShowAddGroups(false);
              }}
            >
              <div>
                <div className="text-sm font-medium font-mono">{r.name}</div>
                {r.description && <div className="text-xs text-muted-foreground mt-0.5">{r.description}</div>}
                <div className="text-xs text-muted-foreground mt-0.5">
                  {r.action_count} actions · {r.member_count} members · {r.group_count} groups
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingRole(r.id);
                    setForm({ name: r.name, description: r.description ?? "" });
                  }}
                >
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => { e.stopPropagation(); del.mutate(r.id); }}
                  className="text-destructive hover:text-destructive"
                >
                  Delete
                </Button>
                <span className="text-xs text-muted-foreground">{expandedRole === r.id ? "▲" : "▼"}</span>
              </div>
            </div>

            {/* Expanded role detail */}
            {expandedRole === r.id && (
              <div className="px-4 pb-3 space-y-4 bg-muted/50">
                {/* Actions section */}
                <div>
                  <div className="text-xs font-medium text-muted-foreground pt-2 pb-1">Actions</div>
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
                      toast.success(`Added ${sel.length} action${sel.length === 1 ? "" : "s"}`);
                      queryClient.invalidateQueries({ queryKey: ["role-actions", expandedRole] });
                      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
                    }}
                  />
                  <div className="divide-y divide-border mt-1">
                    {roleActions.map((a) => (
                      <div key={a.id} className="flex items-center justify-between py-2">
                        <div className="text-sm">
                          <span className="text-foreground font-mono">{a.service_name}:{a.action}</span>
                          {a.description && <span className="text-muted-foreground ml-2 text-xs">{a.description}</span>}
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => removeAction.mutate(a.id)}
                          className="text-destructive hover:text-destructive"
                        >
                          Remove
                        </Button>
                      </div>
                    ))}
                    {roleActions.length === 0 && (
                      <div className="py-2 text-xs text-muted-foreground">No actions assigned</div>
                    )}
                  </div>
                </div>

                {/* Members section */}
                <div>
                  <div className="text-xs font-medium text-muted-foreground pb-1">Members</div>
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
                  <div className="divide-y divide-border mt-1">
                    {roleMembers.map((rm) => (
                      <div key={rm.user_id} className="flex items-center justify-between py-2">
                        <div className="text-sm">
                          <span className="text-foreground">{rm.name}</span>
                          <span className="text-muted-foreground ml-2 text-xs">{rm.email}</span>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => removeMemberMut.mutate(rm.user_id)}
                          className="text-destructive hover:text-destructive"
                        >
                          Remove
                        </Button>
                      </div>
                    ))}
                    {roleMembers.length === 0 && (
                      <div className="py-2 text-xs text-muted-foreground">No members in this role</div>
                    )}
                  </div>
                </div>

                {/* Groups section */}
                <div>
                  <div className="text-xs font-medium text-muted-foreground pb-1">Groups</div>
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
                  <div className="divide-y divide-border mt-1">
                    {roleGroups.map((rg) => (
                      <div key={rg.group_id} className="flex items-center justify-between py-2">
                        <div className="text-sm">
                          <span className="text-foreground">{rg.name}</span>
                          <span className="text-muted-foreground ml-2 text-xs">
                            {rg.member_count} member{rg.member_count === 1 ? "" : "s"}
                          </span>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => removeGroupMut.mutate(rg.group_id)}
                          className="text-destructive hover:text-destructive"
                        >
                          Remove
                        </Button>
                      </div>
                    ))}
                    {roleGroups.length === 0 && (
                      <div className="py-2 text-xs text-muted-foreground">No groups assigned</div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
        {roles.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">No roles</div>
        )}
      </div>

      {/* Create role modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Create Role">
        <div className="space-y-3">
          <Input
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="Role name"
          />
          <Input
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Description (optional)"
          />
          {create.isError && (
            <div className="text-xs text-destructive">{(create.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => create.mutate()}
              disabled={!form.name || create.isPending}
            >
              Create
            </Button>
          </div>
        </div>
      </Modal>

      {/* Edit role modal */}
      <Modal open={!!editingRole} onClose={() => setEditingRole(null)} title="Edit Role">
        <div className="space-y-3">
          <Input
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
          <Input
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Description"
          />
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setEditingRole(null)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => edit.mutate()}
              disabled={edit.isPending}
            >
              Save
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// Loader: waits for the current allow-list, then mounts the stateful inner
// component so its useState initializers seed from real data exactly once
// (no useEffect-sync or null-sentinel gymnastics).
function AccessTab({ workspaceId }: { workspaceId: string }) {
  const { data: allowed, isLoading } = useQuery({
    queryKey: ["workspace-allowed-orgs", workspaceId],
    queryFn: () => getWorkspaceAllowedOrgs(workspaceId),
  });
  if (isLoading || !allowed) {
    return <div className="h-32 bg-muted/50 rounded-lg animate-pulse" />;
  }
  // Seed the editor once from the fetched allow-list, keyed on workspaceId (not the
  // data). Keying on the data would remount the inner component on any background
  // refetch — or another admin's concurrent save — silently discarding the admin's
  // in-progress selections. Their edits now stand until they Save (last-write-wins).
  return (
    <AccessTabInner
      key={workspaceId}
      workspaceId={workspaceId}
      initialAllowed={allowed.organization_ids}
    />
  );
}

function AccessTabInner({
  workspaceId,
  initialAllowed,
}: {
  workspaceId: string;
  initialAllowed: string[];
}) {
  const queryClient = useQueryClient();
  const { data: orgs = [] } = useQuery({
    queryKey: ["organizations"],
    queryFn: getOrganizations,
  });

  const [restrict, setRestrict] = useState(initialAllowed.length > 0);
  const [selected, setSelected] = useState<Set<string>>(
    new Set(initialAllowed),
  );

  const toggleOrg = (orgId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(orgId)) next.delete(orgId);
      else next.add(orgId);
      return next;
    });
  };

  const save = useMutation({
    mutationFn: () =>
      setWorkspaceAllowedOrgs(
        workspaceId,
        restrict ? Array.from(selected) : [],
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace-allowed-orgs", workspaceId],
      });
      toast.success("Access updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  return (
    <div className="space-y-4 max-w-xl">
      <div>
        <h3 className="text-sm font-semibold text-foreground">
          Organization access
        </h3>
        <p className="text-xs text-muted-foreground">
          Restrict which organizations' users may be added to this workspace.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={restrict}
          onChange={(e) => setRestrict(e.target.checked)}
        />
        <span className="font-medium">Restrict to specific organizations</span>
      </label>
      {!restrict && (
        <p className="text-xs text-muted-foreground -mt-2">
          Open: members from any organization may be added.
        </p>
      )}

      {restrict && (
        <>
          <ul className="space-y-1 border border-border rounded-md p-2">
            {orgs.map((o) => (
              <li key={o.id}>
                <label className="flex items-center gap-2 text-sm px-2 py-1">
                  <input
                    type="checkbox"
                    checked={selected.has(o.id)}
                    onChange={() => toggleOrg(o.id)}
                  />
                  {o.is_public && <span>🌐</span>}
                  {o.name}
                </label>
              </li>
            ))}
          </ul>
          {selected.size === 0 && (
            <p className="text-xs text-amber-700 dark:text-amber-400 -mt-2">
              Select at least one organization, or turn off the restriction.
              Saving with none selected leaves the workspace open to all.
            </p>
          )}
        </>
      )}

      <Button
        size="sm"
        onClick={() => save.mutate()}
        disabled={save.isPending || (restrict && selected.size === 0)}
      >
        {save.isPending ? "Saving..." : "Save"}
      </Button>
    </div>
  );
}
