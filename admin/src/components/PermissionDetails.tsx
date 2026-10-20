import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { adminGetPermission, adminRevokeShare, adminSharePermission, adminUpdateVisibility } from "../api/client";
import { VisibilityBadge } from "./Badge";
import { SearchInput } from "./SearchInput";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

const EMPTY_SHARE = { grantee_type: "user", grantee_id: "", permission: "view" };

export function PermissionDetails({ permissionId }: { permissionId: string }) {
  const queryClient = useQueryClient();
  const [showShare, setShowShare] = useState(false);
  const [shareSearch, setShareSearch] = useState("");
  const [shareForm, setShareForm] = useState(EMPTY_SHARE);
  const { data: detail, isLoading, error, refetch } = useQuery({
    queryKey: ["admin-permission", permissionId], queryFn: () => adminGetPermission(permissionId),
  });

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["admin-permissions"] }),
      queryClient.invalidateQueries({ queryKey: ["admin-permission", permissionId] }),
    ]);
  }

  const visibility = useMutation({
    mutationFn: (value: string) => adminUpdateVisibility(permissionId, value),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  });
  const revoke = useMutation({
    mutationFn: ({ type, id }: { type: string; id: string }) => adminRevokeShare(permissionId, type, id),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  });
  const share = useMutation({
    mutationFn: () => adminSharePermission(permissionId, { ...shareForm, grantee_id: shareForm.grantee_id.trim() }),
    onSuccess: async () => { await refresh(); setShowShare(false); setShareForm(EMPTY_SHARE); },
  });

  if (isLoading) return <div aria-label="Loading resource access" className="h-64 animate-pulse rounded-lg bg-muted" />;
  if (error) return <div role="alert" className="space-y-3"><p className="text-sm text-destructive">Unable to load resource access: {error.message}</p><Button variant="outline" size="sm" onClick={() => void refetch()}>Try again</Button></div>;
  if (!detail) return null;

  const shares = detail.shares.filter((item) => [item.grantee_type, item.grantee_id, item.permission].some((value) => value.toLowerCase().includes(shareSearch.trim().toLowerCase())));

  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-5 gap-y-5 text-sm">
        <div className="col-span-2"><dt className="text-xs text-muted-foreground">Resource ID</dt><dd className="mt-1.5 break-all font-mono text-xs">{String(detail.resource_id)}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Service</dt><dd className="mt-1.5 break-all">{detail.service_name}</dd></div>
        <div><dt className="text-xs text-muted-foreground">Resource type</dt><dd className="mt-1.5 break-all">{detail.resource_type}</dd></div>
        <div className="col-span-2"><dt className="text-xs text-muted-foreground">Owner</dt><dd className="mt-1.5 break-all">{detail.owner_email ?? String(detail.owner_id)}</dd></div>
        <div className="col-span-2"><dt className="text-xs text-muted-foreground">Workspace ID</dt><dd className="mt-1.5 break-all font-mono text-xs">{String(detail.workspace_id)}</dd></div>
        <div className="col-span-2"><dt className="text-xs text-muted-foreground">Registered</dt><dd className="mt-1.5 text-xs"><time dateTime={detail.created_at}>{new Date(detail.created_at).toLocaleString()}</time></dd></div>
      </dl>

      <section className="my-6 border-y border-border py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="section-title">Visibility</h3><VisibilityBadge visibility={detail.visibility} />
        </div>
        <p className="my-3 text-xs leading-relaxed text-muted-foreground">{detail.visibility === "workspace" ? "All workspace members can view this resource. Their role and shares determine whether they can edit it." : "The owner and workspace admins can access this resource. Share it with other members to give them access."}</p>
        <Button variant="outline" size="sm" disabled={visibility.isPending} onClick={() => visibility.mutate(detail.visibility === "workspace" ? "private" : "workspace")}>Switch to {detail.visibility === "workspace" ? "private" : "workspace"}</Button>
      </section>

      <section aria-label="Resource shares">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="section-title">Shares <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">{detail.shares.length}</span></h3>
          <Button variant="outline" size="sm" onClick={() => { setShowShare(!showShare); share.reset(); }} aria-expanded={showShare} aria-controls="permission-share-form">Add share</Button>
        </div>

        {showShare && <form id="permission-share-form" className="mb-4 space-y-4 rounded-lg border border-border bg-muted/40 p-4" onSubmit={(event) => { event.preventDefault(); if (shareForm.grantee_id.trim()) share.mutate(); }}>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5"><Label htmlFor="share-type">Recipient type</Label><select id="share-type" value={shareForm.grantee_type} onChange={(event) => setShareForm((form) => ({ ...form, grantee_type: event.target.value }))} className="h-9 w-full rounded-md border border-input bg-card px-2 text-sm"><option value="user">User</option><option value="group">Group</option></select></div>
            <div className="space-y-1.5"><Label htmlFor="share-permission">Permission</Label><select id="share-permission" value={shareForm.permission} onChange={(event) => setShareForm((form) => ({ ...form, permission: event.target.value }))} className="h-9 w-full rounded-md border border-input bg-card px-2 text-sm"><option value="view">View</option><option value="edit">Edit</option></select></div>
          </div>
          <div className="space-y-1.5"><Label htmlFor="share-id">Recipient ID (UUID)</Label><Input id="share-id" autoFocus required value={shareForm.grantee_id} onChange={(event) => setShareForm((form) => ({ ...form, grantee_id: event.target.value }))} placeholder="User or group UUID" className="bg-card font-mono text-xs" /></div>
          {share.isError && <p role="alert" className="text-xs text-destructive">{share.error.message}</p>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" size="sm" onClick={() => { setShowShare(false); share.reset(); }}>Cancel</Button><Button type="submit" size="sm" disabled={!shareForm.grantee_id.trim() || share.isPending}>{share.isPending ? "Sharing…" : "Share"}</Button></div>
        </form>}

        {(detail.shares.length > 8 || shareSearch) && <div className="mb-3"><SearchInput value={shareSearch} onChange={setShareSearch} placeholder="Find a share…" className="sm:w-full" /></div>}
        {shares.length ? (
          <ul className="max-h-80 overflow-y-auto divide-y divide-border rounded-lg border border-border">
            {shares.map((item) => <li key={item.id} className="flex items-start justify-between gap-3 px-3 py-3">
              <div className="min-w-0"><p className="mb-1 text-xs capitalize text-muted-foreground">{item.grantee_type} · {item.permission}</p><p className="break-all font-mono text-xs">{item.grantee_id}</p></div>
              <Button variant="ghost" size="sm" disabled={revoke.isPending} aria-label={`Revoke ${item.permission} access for ${item.grantee_id}`} onClick={() => revoke.mutate({ type: item.grantee_type, id: item.grantee_id })} className="shrink-0 text-destructive hover:bg-destructive/10 hover:text-destructive">Revoke</Button>
            </li>)}
          </ul>
        ) : <p role="status" className="text-xs text-muted-foreground">{shareSearch ? "No shares match your search." : "This resource hasn’t been shared with any users or groups."}</p>}
      </section>
    </div>
  );
}
