import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import {
  adminGetPermission,
  adminListPermissions,
  adminRevokeShare,
  adminSharePermission,
  adminUpdateVisibility,
  getAllWorkspaces,
} from "../api/client";
import { VisibilityBadge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { SearchInput } from "../components/SearchInput";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { AdminResourcePermission } from "../types/api";

export function Permissions() {
  const [page, setPage] = useState(1);
  const [workspaceFilter, setWorkspaceFilter] = useState("");
  const [serviceFilter, setServiceFilter] = useState("");
  const [resourceIdFilter, setResourceIdFilter] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [sortBy, setSortBy] = useState<string | undefined>();
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: workspaces = [] } = useQuery({
    queryKey: ["all-workspaces"],
    queryFn: getAllWorkspaces,
  });

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admin-permissions", page, workspaceFilter, serviceFilter, resourceIdFilter, ownerFilter, sortBy, sortOrder],
    queryFn: () =>
      adminListPermissions({
        page,
        workspaceId: workspaceFilter || undefined,
        serviceName: serviceFilter || undefined,
        resourceId: resourceIdFilter || undefined,
        owner: ownerFilter || undefined,
        sortBy,
        sortOrder,
      }),
  });

  const toggleSort = (col: string) => {
    if (sortBy === col) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(col);
      setSortOrder("desc");
    }
    setPage(1);
  };

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold">Permissions</h1>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          value={workspaceFilter}
          onChange={(e) => { setWorkspaceFilter(e.target.value); setPage(1); }}
          className="px-3 py-2 bg-background border border-border rounded-md text-sm text-foreground"
        >
          <option value="">All workspaces</option>
          {workspaces.map((ws) => (
            <option key={ws.id} value={ws.id}>
              {ws.name} ({ws.slug})
            </option>
          ))}
        </select>
        <SearchInput value={serviceFilter} onChange={(v) => { setServiceFilter(v); setPage(1); }} placeholder="Service name..." />
        <SearchInput value={resourceIdFilter} onChange={(v) => { setResourceIdFilter(v); setPage(1); }} placeholder="Resource ID..." />
        <SearchInput value={ownerFilter} onChange={(v) => { setOwnerFilter(v); setPage(1); }} placeholder="Owner email..." />
      </div>

      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : isError ? (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-6 text-center">
          <div className="text-sm text-red-600 dark:text-red-400">Failed to load permissions</div>
          <div className="text-xs text-red-500/70 mt-1">{(error as Error).message}</div>
        </div>
      ) : (
        <>
          {/* Table */}
          <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-muted-foreground text-xs">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Service</th>
                  <th className="text-left px-4 py-2 font-medium">Type</th>
                  <th className="text-left px-4 py-2 font-medium">Resource ID</th>
                  <th className="text-left px-4 py-2 font-medium">Owner</th>
                  <th className="text-left px-4 py-2 font-medium">Visibility</th>
                  <th
                    className="text-left px-4 py-2 font-medium w-20 cursor-pointer select-none hover:text-foreground transition-colors"
                    onClick={() => toggleSort("shares")}
                  >
                    Shares{sortBy === "shares" ? (sortOrder === "asc" ? " \u2191" : " \u2193") : ""}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {(data?.items ?? []).map((p) => (
                  <PermissionRow
                    key={p.id}
                    perm={p}
                    expanded={expandedId === p.id}
                    onToggle={() => setExpandedId(expandedId === p.id ? null : p.id)}
                  />
                ))}
              </tbody>
            </table>
            {(data?.items ?? []).length === 0 && (
              <div className="px-4 py-12 text-center text-sm text-muted-foreground">No resources found</div>
            )}
          </div>

          {/* Pagination */}
          {data && data.total > data.page_size && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{data.total} total</span>
              <div className="flex gap-1">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</Button>
                <span className="px-2 py-1">{data.page} / {Math.ceil(data.total / data.page_size)}</span>
                <Button variant="outline" size="sm" disabled={page >= Math.ceil(data.total / data.page_size)} onClick={() => setPage(page + 1)}>Next</Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function PermissionRow({
  perm,
  expanded,
  onToggle,
}: {
  perm: AdminResourcePermission;
  expanded: boolean;
  onToggle: () => void;
}) {
  const queryClient = useQueryClient();
  const [showShare, setShowShare] = useState(false);
  const [shareForm, setShareForm] = useState({ grantee_type: "user", grantee_id: "", permission: "view" });

  const { data: detail } = useQuery({
    queryKey: ["admin-permission", perm.id],
    queryFn: () => adminGetPermission(perm.id),
    enabled: expanded,
  });

  const toggleVisibility = useMutation({
    mutationFn: () =>
      adminUpdateVisibility(perm.id, perm.visibility === "workspace" ? "private" : "workspace"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-permissions"] });
      queryClient.invalidateQueries({ queryKey: ["admin-permission", perm.id] });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const revoke = useMutation({
    mutationFn: ({ granteeType, granteeId }: { granteeType: string; granteeId: string }) =>
      adminRevokeShare(perm.id, granteeType, granteeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-permission", perm.id] });
      queryClient.invalidateQueries({ queryKey: ["admin-permissions"] });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const share = useMutation({
    mutationFn: () =>
      adminSharePermission(perm.id, shareForm),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-permission", perm.id] });
      queryClient.invalidateQueries({ queryKey: ["admin-permissions"] });
      setShowShare(false);
      setShareForm({ grantee_type: "user", grantee_id: "", permission: "view" });
    },
  });

  return (
    <>
      <tr
        onClick={onToggle}
        className="hover:bg-muted/50 cursor-pointer transition-colors"
      >
        <td className="px-4 py-2.5 font-mono text-foreground">{perm.service_name}</td>
        <td className="px-4 py-2.5 font-mono text-muted-foreground">{perm.resource_type}</td>
        <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">{String(perm.resource_id).slice(0, 8)}...</td>
        <td className="px-4 py-2.5 text-muted-foreground text-xs">{perm.owner_email ?? "-"}</td>
        <td className="px-4 py-2.5"><VisibilityBadge visibility={perm.visibility} /></td>
        <td className="px-4 py-2.5 text-muted-foreground tabular-nums">{perm.share_count}</td>
      </tr>

      {expanded && detail && (
        <tr>
          <td colSpan={6} className="px-4 py-4 bg-muted/50 border-t border-border">
            <div className="space-y-4">
              {/* Details */}
              <dl className="grid grid-cols-3 gap-x-6 gap-y-2 text-xs">
                <div>
                  <dt className="text-muted-foreground">Resource ID</dt>
                  <dd className="font-mono text-foreground">{String(detail.resource_id)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Owner</dt>
                  <dd className="text-foreground">{detail.owner_email ?? String(detail.owner_id)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Created</dt>
                  <dd className="text-foreground">{new Date(detail.created_at).toLocaleString()}</dd>
                </div>
              </dl>

              {/* Visibility toggle */}
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">Visibility:</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={(e) => { e.stopPropagation(); toggleVisibility.mutate(); }}
                  disabled={toggleVisibility.isPending}
                >
                  Switch to {detail.visibility === "workspace" ? "private" : "workspace"}
                </Button>
              </div>

              {/* Shares */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-muted-foreground font-medium">Shares ({detail.shares.length})</span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => { e.stopPropagation(); setShowShare(true); }}
                  >
                    + Add Share
                  </Button>
                </div>
                {detail.shares.length > 0 ? (
                  <div className="rounded border border-border divide-y divide-border">
                    {detail.shares.map((s) => (
                      <div key={s.id} className="flex items-center justify-between px-3 py-2 text-xs">
                        <div>
                          <span className="capitalize text-muted-foreground">{s.grantee_type}</span>{" "}
                          <span className="font-mono text-muted-foreground">{s.grantee_id}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className={`font-medium ${s.permission === "edit" ? "text-blue-700 dark:text-blue-400" : "text-muted-foreground"}`}>
                            {s.permission}
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              revoke.mutate({ granteeType: s.grantee_type, granteeId: s.grantee_id });
                            }}
                            className="text-red-700 dark:text-red-400 hover:text-red-800 dark:hover:text-red-300"
                          >
                            Revoke
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">No shares</div>
                )}
              </div>
            </div>

            {/* Add share modal */}
            <Modal open={showShare} onClose={() => setShowShare(false)} title="Add Share">
              <div className="space-y-3">
                <div className="space-y-1">
                  <Label htmlFor="share-grantee-type">Grantee Type</Label>
                  <select
                    id="share-grantee-type"
                    value={shareForm.grantee_type}
                    onChange={(e) => setShareForm((f) => ({ ...f, grantee_type: e.target.value }))}
                    className="w-full px-3 py-2 bg-background border border-border rounded-md text-sm text-foreground"
                  >
                    <option value="user">User</option>
                    <option value="group">Group</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="share-grantee-id">Grantee ID (UUID)</Label>
                  <Input
                    id="share-grantee-id"
                    value={shareForm.grantee_id}
                    onChange={(e) => setShareForm((f) => ({ ...f, grantee_id: e.target.value }))}
                    placeholder="UUID"
                    className="font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="share-permission">Permission</Label>
                  <select
                    id="share-permission"
                    value={shareForm.permission}
                    onChange={(e) => setShareForm((f) => ({ ...f, permission: e.target.value }))}
                    className="w-full px-3 py-2 bg-background border border-border rounded-md text-sm text-foreground"
                  >
                    <option value="view">View</option>
                    <option value="edit">Edit</option>
                  </select>
                </div>
                {share.isError && (
                  <div className="text-xs text-red-700 dark:text-red-400">{(share.error as Error).message}</div>
                )}
                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="ghost" size="sm" onClick={() => setShowShare(false)}>Cancel</Button>
                  <Button
                    size="sm"
                    onClick={() => share.mutate()}
                    disabled={!shareForm.grantee_id || share.isPending}
                  >
                    Share
                  </Button>
                </div>
              </div>
            </Modal>
          </td>
        </tr>
      )}
    </>
  );
}
