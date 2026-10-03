import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { addUserToWorkspace, getActivityInsights, getAllWorkspaces, getUserDetail, revokeUserTokens, updateUser } from "../api/client";
import { RoleBadge, StatusBadge } from "../components/Badge";
import { BarList } from "../components/charts";
import { WorldMap } from "../components/WorldMap";
import { ConfirmModal } from "../components/ConfirmModal";
import { Modal } from "../components/Modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function UserDetail() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState("");
  const [showConfirmToggle, setShowConfirmToggle] = useState(false);
  const [showConfirmAdmin, setShowConfirmAdmin] = useState(false);
  const [showConfirmRevoke, setShowConfirmRevoke] = useState(false);
  const [showAddWorkspace, setShowAddWorkspace] = useState(false);
  const [addWsId, setAddWsId] = useState("");
  const [addWsRole, setAddWsRole] = useState("viewer");

  const { data: user, isLoading } = useQuery({
    queryKey: ["user", id],
    queryFn: () => getUserDetail(id!),
    enabled: !!id,
  });

  const { data: allWorkspaces = [] } = useQuery({
    queryKey: ["all-workspaces"],
    queryFn: getAllWorkspaces,
    enabled: showAddWorkspace,
  });

  const { data: insights } = useQuery({
    queryKey: ["user-insights", id],
    queryFn: () => getActivityInsights(30, id),
    enabled: !!id,
  });

  const updateName = useMutation({
    mutationFn: () => updateUser(id!, { name: nameValue }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["user", id] });
      setEditingName(false);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const toggleActive = useMutation({
    mutationFn: () => updateUser(id!, { is_active: !user?.is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["user", id] });
      setShowConfirmToggle(false);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const toggleAdmin = useMutation({
    mutationFn: () => updateUser(id!, { is_admin: !user?.is_admin }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["user", id] });
      setShowConfirmAdmin(false);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const revokeTokens = useMutation({
    mutationFn: () => revokeUserTokens(id!),
    onSuccess: () => {
      setShowConfirmRevoke(false);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const addToWorkspace = useMutation({
    mutationFn: () => addUserToWorkspace(id!, addWsId, addWsRole),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["user", id] });
      setShowAddWorkspace(false);
      setAddWsId("");
      setAddWsRole("viewer");
    },
  });

  if (isLoading) return <div className="animate-pulse h-64 bg-muted/50 rounded-lg" />;
  if (!user) return <div className="text-muted-foreground">User not found</div>;

  const availableWorkspaces = allWorkspaces.filter(
    (ws) => !user.memberships.some((m) => m.workspace_id === ws.id)
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/users" className="hover:text-foreground">Users</Link>
        <span>/</span>
        <span className="text-foreground">{user.name}</span>
      </div>

      {/* Profile card */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex items-start gap-4">
          <div className="w-14 h-14 rounded-full bg-muted flex items-center justify-center text-xl font-semibold text-muted-foreground shrink-0">
            {user.name.charAt(0).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            {editingName ? (
              <div className="flex items-center gap-2">
                <Input
                  value={nameValue}
                  onChange={(e) => setNameValue(e.target.value)}
                  className="h-8 w-auto text-sm"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") updateName.mutate();
                    if (e.key === "Escape") setEditingName(false);
                  }}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => updateName.mutate()}
                  disabled={updateName.isPending || !nameValue}
                  className="text-emerald-700 dark:text-emerald-400 hover:text-emerald-700 dark:hover:text-emerald-300"
                >
                  Save
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setEditingName(false)}>
                  Cancel
                </Button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold text-foreground">{user.name}</h2>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => { setNameValue(user.name); setEditingName(true); }}
                  className="text-muted-foreground hover:text-foreground"
                  title="Edit name"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                </Button>
              </div>
            )}
            <div className="text-sm text-muted-foreground mt-0.5">{user.email}</div>
            <div className="flex items-center gap-3 mt-2">
              <StatusBadge active={user.is_active} />
              {user.is_admin && (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-500/10 text-purple-700 dark:text-purple-400 ring-1 ring-purple-500/20">
                  Admin
                </span>
              )}
              <span className="text-xs text-muted-foreground">
                Joined {new Date(user.created_at).toLocaleDateString()}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowConfirmAdmin(true)}
              className={
                user.is_admin
                  ? ""
                  : "text-purple-700 dark:text-purple-400 hover:text-purple-700 dark:hover:text-purple-300"
              }
            >
              {user.is_admin ? "Demote Admin" : "Promote Admin"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowConfirmRevoke(true)}
              className="text-amber-700 dark:text-amber-400 hover:text-amber-700 dark:hover:text-amber-300"
            >
              Revoke Tokens
            </Button>
            {user.is_active ? (
              <Button variant="destructive" size="sm" onClick={() => setShowConfirmToggle(true)}>
                Deactivate
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowConfirmToggle(true)}
                className="text-emerald-700 dark:text-emerald-400 hover:text-emerald-700 dark:hover:text-emerald-300"
              >
                Activate
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Sign-in locations (30d) */}
      {insights && insights.total > 0 && (
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-baseline justify-between mb-2">
            <h3 className="text-sm font-medium text-muted-foreground">Sign-in locations</h3>
            <span className="text-xs text-muted-foreground">Last 30 days</span>
          </div>
          {insights.countries.length === 0 ? (
            <div className="text-sm text-muted-foreground py-4 text-center">
              All {insights.total} sign-in{insights.total === 1 ? "" : "s"} came from private or
              unresolvable addresses
            </div>
          ) : (
            <div className="grid grid-cols-[2fr_1fr] gap-6 items-start">
              <WorldMap countries={insights.countries} />
              <div>
                <BarList rows={insights.countries.map((c) => [c.name, c.count])} labelWidth={140} />
                {insights.unresolved > 0 && (
                  <div className="text-xs text-muted-foreground mt-3 px-1">
                    {insights.unresolved} from private/unresolvable addresses
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Social accounts */}
      {user.social_accounts.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-muted-foreground mb-2">Linked Accounts</h3>
          <div className="flex gap-2">
            {user.social_accounts.map((sa) => (
              <div
                key={sa.id}
                className="px-3 py-2 rounded-md border border-border bg-card text-sm"
              >
                <span className="font-medium capitalize text-foreground">{sa.provider}</span>
                <span className="text-muted-foreground ml-2 text-xs font-mono">{sa.provider_user_id}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Workspace memberships */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-muted-foreground">
            Workspaces ({user.memberships.length})
          </h3>
          <Button variant="outline" size="sm" onClick={() => setShowAddWorkspace(true)}>
            + Add to Workspace
          </Button>
        </div>
        {user.memberships.length === 0 ? (
          <div className="text-sm text-muted-foreground py-4">No workspace memberships</div>
        ) : (
          <div className="rounded-lg border border-border divide-y divide-border">
            {user.memberships.map((m) => (
              <Link
                key={m.workspace_id}
                to={`/workspaces/${m.workspace_id}`}
                className="flex items-center justify-between px-4 py-3 hover:bg-muted/50 transition-colors"
              >
                <div>
                  <div className="text-sm font-medium text-foreground">{m.workspace_name}</div>
                  <div className="text-xs text-muted-foreground font-mono">{m.workspace_slug}</div>
                </div>
                <div className="flex items-center gap-3">
                  <RoleBadge role={m.role} />
                  <span className="text-xs text-muted-foreground">
                    {new Date(m.joined_at).toLocaleDateString()}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Confirm deactivate/activate */}
      <ConfirmModal
        open={showConfirmToggle}
        onClose={() => setShowConfirmToggle(false)}
        onConfirm={() => toggleActive.mutate()}
        title={user.is_active ? "Deactivate User" : "Activate User"}
        message={
          user.is_active
            ? `Are you sure you want to deactivate ${user.name}? They will no longer be able to log in.`
            : `Are you sure you want to reactivate ${user.name}?`
        }
        confirmLabel={user.is_active ? "Deactivate" : "Activate"}
        danger={user.is_active}
        isPending={toggleActive.isPending}
      />

      {/* Confirm promote/demote admin */}
      <ConfirmModal
        open={showConfirmAdmin}
        onClose={() => setShowConfirmAdmin(false)}
        onConfirm={() => toggleAdmin.mutate()}
        title={user.is_admin ? "Demote Admin" : "Promote to Admin"}
        message={
          user.is_admin
            ? `Remove admin privileges from ${user.name}?`
            : `Grant admin privileges to ${user.name}? They will have full access to the admin panel.`
        }
        confirmLabel={user.is_admin ? "Demote" : "Promote"}
        danger={user.is_admin}
        isPending={toggleAdmin.isPending}
      />

      {/* Confirm revoke tokens */}
      <ConfirmModal
        open={showConfirmRevoke}
        onClose={() => setShowConfirmRevoke(false)}
        onConfirm={() => revokeTokens.mutate()}
        title="Revoke All Tokens"
        message={`This will invalidate all active sessions for ${user.name}. They will need to log in again.`}
        confirmLabel="Revoke All"
        danger
        isPending={revokeTokens.isPending}
      />

      {/* Add to workspace modal */}
      <Modal open={showAddWorkspace} onClose={() => setShowAddWorkspace(false)} title="Add to Workspace">
        <div className="space-y-3">
          <div>
            <Label htmlFor="add-ws-workspace" className="text-xs text-muted-foreground">Workspace</Label>
            <select
              id="add-ws-workspace"
              value={addWsId}
              onChange={(e) => setAddWsId(e.target.value)}
              className="mt-1 w-full px-3 py-2 border border-border bg-background rounded-md text-sm text-foreground"
            >
              <option value="">Select workspace...</option>
              {availableWorkspaces.map((ws) => (
                <option key={ws.id} value={ws.id}>
                  {ws.name} ({ws.slug})
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="add-ws-role" className="text-xs text-muted-foreground">Role</Label>
            <select
              id="add-ws-role"
              value={addWsRole}
              onChange={(e) => setAddWsRole(e.target.value)}
              className="mt-1 w-full px-3 py-2 border border-border bg-background rounded-md text-sm text-foreground"
            >
              {["viewer", "editor", "admin", "owner"].map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          {addToWorkspace.isError && (
            <div className="text-xs text-red-700 dark:text-red-400">{(addToWorkspace.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowAddWorkspace(false)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => addToWorkspace.mutate()}
              disabled={!addWsId || addToWorkspace.isPending}
            >
              Add
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
