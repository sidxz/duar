import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getActivity, getAllWorkspaces, getUsers } from "../api/client";
import type { ActivityLog } from "../types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function Activity() {
  const [searchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState(searchParams.get("action") ?? "");
  const [targetType, setTargetType] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [actorQuery, setActorQuery] = useState("");

  const { data: actorMatches } = useQuery({
    queryKey: ["actor-search", actorQuery],
    queryFn: () => getUsers(1, 20, actorQuery),
    enabled: actorQuery.length >= 2,
  });
  // Filter applies once the typed text is a picked email (or a pasted UUID).
  const actorId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(actorQuery)
    ? actorQuery
    : actorMatches?.items.find((u) => u.email === actorQuery)?.id;

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["activity", page, action, targetType, workspaceId, fromDate, toDate, actorId],
    queryFn: () =>
      getActivity({
        page,
        page_size: 25,
        action: action || undefined,
        target_type: targetType || undefined,
        workspace_id: workspaceId || undefined,
        actor_id: actorId || undefined,
        from_date: fromDate || undefined,
        // date input yields midnight; make the To day inclusive
        to_date: toDate ? `${toDate}T23:59:59.999` : undefined,
      }),
  });

  const { data: workspaces = [] } = useQuery({
    queryKey: ["all-workspaces"],
    queryFn: getAllWorkspaces,
  });
  const wsNames = new Map(workspaces.map((w) => [w.id, w.name]));

  const actions = [
    "user_login", "admin_login",
    "user_logout", "admin_logout",
    "login_failed", "admin_login_failed",
    "refresh_context_changed", "refresh_reuse_detected",
    "token_family_revoked", "action_denied",
    "login_impossible_travel", "login_new_country", "login_new_device",
    "credential_stuffing_suspected",
    "user_activated", "user_deactivated", "user_updated",
    "user_promoted_admin", "user_demoted_admin",
    "workspace_created", "workspace_updated", "workspace_deleted",
    "member_invited", "member_role_changed", "member_removed",
    "invitation_created", "invitation_accepted", "invitation_revoked", "invitation_rejected",
    "self_serve_denied",
    "group_created", "group_updated", "group_deleted",
    "group_member_added", "group_member_removed",
    "role_created", "role_updated", "role_deleted",
    "role_action_added", "role_action_removed",
    "role_member_added", "role_member_removed",
    "role_group_added", "role_group_removed",
    "service_action_deleted",
    "permission_visibility_changed", "permission_shared", "permission_revoked",
    "permissions_purged",
    "client_app_created", "client_app_updated", "client_app_deleted",
    "service_app_created", "service_app_updated", "service_app_deleted",
    "service_app_key_rotated",
    "realm_created", "realm_updated", "realm_deleted",
    "realm_member_added", "realm_member_removed",
    "batch_import", "bulk_status_change", "tokens_revoked",
    "export_users", "export_workspaces", "view_system_settings",
    "org_create", "org_update", "org_public_toggle", "org_delete",
    "org_domain_add", "org_domain_remove", "workspace_allowed_orgs_set",
  ];

  const targetTypes = [
    "user", "workspace", "group", "resource_permission", "system", "organization",
  ];

  const resetFilters = () => {
    setAction("");
    setTargetType("");
    setWorkspaceId("");
    setFromDate("");
    setToDate("");
    setActorQuery("");
    setPage(1);
  };

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 0;

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Activity Log</h1>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Action</label>
          <select
            value={action}
            onChange={(e) => { setAction(e.target.value); setPage(1); }}
            className="px-2 py-1.5 border border-border bg-background rounded text-xs text-foreground"
          >
            <option value="">All actions</option>
            {actions.map((a) => (
              <option key={a} value={a}>{a.replace(/_/g, " ")}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Target Type</label>
          <select
            value={targetType}
            onChange={(e) => { setTargetType(e.target.value); setPage(1); }}
            className="px-2 py-1.5 border border-border bg-background rounded text-xs text-foreground"
          >
            <option value="">All types</option>
            {targetTypes.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Workspace</label>
          <select
            value={workspaceId}
            onChange={(e) => { setWorkspaceId(e.target.value); setPage(1); }}
            className="px-2 py-1.5 border border-border bg-background rounded text-xs text-foreground"
          >
            <option value="">All workspaces</option>
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>{ws.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Actor</label>
          <Input
            list="actor-options"
            value={actorQuery}
            onChange={(e) => { setActorQuery(e.target.value); setPage(1); }}
            placeholder="Search user…"
            className="w-44 text-xs"
          />
          <datalist id="actor-options">
            {actorMatches?.items.map((u) => (
              <option key={u.id} value={u.email}>{u.name}</option>
            ))}
          </datalist>
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">From</label>
          <Input
            type="date"
            value={fromDate}
            onChange={(e) => { setFromDate(e.target.value); setPage(1); }}
            className="w-auto text-xs"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">To</label>
          <Input
            type="date"
            value={toDate}
            onChange={(e) => { setToDate(e.target.value); setPage(1); }}
            className="w-auto text-xs"
          />
        </div>
        {(action || targetType || workspaceId || fromDate || toDate || actorQuery) && (
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            Clear filters
          </Button>
        )}
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : isError ? (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-6 text-center">
          <div className="text-sm text-red-600 dark:text-red-400">Failed to load activity</div>
          <div className="text-xs text-red-500/70 mt-1">{(error as Error).message}</div>
        </div>
      ) : (
        <>
          <div className="rounded-lg border border-border divide-y divide-border">
            <div className="grid grid-cols-[1fr_120px_150px_100px_140px] px-4 py-2 text-xs text-muted-foreground font-medium">
              <span>Action</span>
              <span>Actor</span>
              <span>Target</span>
              <span>Workspace</span>
              <span>Time</span>
            </div>
            {data?.items.map((entry) => (
              <ActivityRow
                key={entry.id}
                entry={entry}
                workspaceName={entry.workspace_id ? wsNames.get(entry.workspace_id) : undefined}
              />
            ))}
            {data?.items.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">No activity found</div>
            )}
          </div>

          {data && data.total > data.page_size && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{data.total} total</span>
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                >
                  Prev
                </Button>
                <span className="px-2 py-1">{page} / {totalPages}</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage(page + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function detailValue(v: unknown): string {
  const s = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
  return s.length > 60 ? `${s.slice(0, 60)}…` : s;
}

function ActivityRow({ entry, workspaceName }: { entry: ActivityLog; workspaceName?: string }) {
  const action = entry.action.replace(/_/g, " ");
  const time = new Date(entry.created_at).toLocaleString();

  return (
    <div className="grid grid-cols-[1fr_120px_150px_100px_140px] px-4 py-2.5 text-sm items-center">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 shrink-0" />
        <span className="text-foreground">{action}</span>
        {entry.detail &&
          Object.entries(entry.detail).map(([k, v]) => (
            <span key={k} className="text-muted-foreground text-xs" title={String(v)}>
              {k}: <span className="font-mono text-muted-foreground">{detailValue(v)}</span>
            </span>
          ))}
      </div>
      <span className="text-muted-foreground text-xs truncate">{entry.actor_name ?? entry.actor_email ?? "System"}</span>
      <span className="text-muted-foreground text-xs truncate" title={`${entry.target_type} ${entry.target_id}`}>
        {entry.target_label ?? (
          <>
            {entry.target_type} <span className="font-mono">{entry.target_id.slice(0, 8)}</span>
          </>
        )}
      </span>
      <span className="text-muted-foreground text-xs truncate" title={entry.workspace_id ?? undefined}>
        {workspaceName ?? (entry.workspace_id ? <span className="font-mono">{entry.workspace_id.slice(0, 8)}</span> : "--")}
      </span>
      <span className="text-muted-foreground text-xs">{time}</span>
    </div>
  );
}
