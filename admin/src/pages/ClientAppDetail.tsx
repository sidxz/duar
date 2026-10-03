import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  deleteClientApp,
  getClientApp,
  updateClientApp,
} from "../api/client";
import { StatusBadge } from "../components/Badge";
import { ConfirmModal } from "../components/ConfirmModal";
import { Modal } from "../components/Modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export function ClientAppDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showEdit, setShowEdit] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [showDeactivate, setShowDeactivate] = useState(false);
  const [revokeOnDeactivate, setRevokeOnDeactivate] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [editForm, setEditForm] = useState({ name: "", redirect_uris: "", is_active: true });

  const { data: app, isLoading, isError, error } = useQuery({
    queryKey: ["client-app", id],
    queryFn: () => getClientApp(id!),
    enabled: !!id,
  });

  const update = useMutation({
    mutationFn: (opts?: { revoke_sessions?: boolean }) =>
      updateClientApp(id!, {
        name: editForm.name || undefined,
        redirect_uris: editForm.redirect_uris
          .split("\n")
          .map((u) => u.trim())
          .filter(Boolean),
        is_active: editForm.is_active,
        ...(opts?.revoke_sessions ? { revoke_sessions: true } : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["client-app", id] });
      queryClient.invalidateQueries({ queryKey: ["client-apps"] });
      setShowEdit(false);
      setShowDeactivate(false);
      toast.success("App updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteClientApp(id!),
    onSuccess: () => {
      setShowDelete(false);
      queryClient.invalidateQueries({ queryKey: ["client-apps"] });
      toast.success("App deleted");
      navigate("/client-apps");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const openEdit = () => {
    if (app) {
      setEditForm({
        name: app.name,
        redirect_uris: app.redirect_uris.join("\n"),
        is_active: app.is_active,
      });
    }
    setShowEdit(true);
  };

  if (isLoading) {
    return <div className="animate-pulse h-64 bg-muted/50 rounded-lg" />;
  }

  if (isError || !app) {
    return (
      <div className="space-y-3">
        <Link
          to="/client-apps"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Login Apps
        </Link>
        <div className="border border-border rounded-lg p-6 text-center">
          <p className="text-sm text-foreground">
            {(error as Error)?.message ?? "App not found."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/client-apps" className="hover:text-foreground">Login Apps</Link>
        <span>/</span>
        <span className="text-foreground">{app.name}</span>
      </div>

      {/* Header card */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold text-foreground">{app.name}</h2>
              <StatusBadge active={app.is_active} />
            </div>
            <div className="flex items-center gap-1.5 mt-2">
              <code className="text-xs text-muted-foreground font-mono">{app.id}</code>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => {
                  navigator.clipboard.writeText(app.id);
                  toast.success("Copied");
                }}
                aria-label="Copy app ID"
              >
                <Copy className="w-3.5 h-3.5" />
              </Button>
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              Created {new Date(app.created_at).toLocaleDateString()}
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={openEdit}>
              Edit
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => { setShowDelete(true); setDeleteName(""); }}
            >
              Delete
            </Button>
          </div>
        </div>
      </div>

      {/* Redirect URIs */}
      <div>
        <h3 className="text-sm font-medium text-foreground mb-2">Redirect URIs</h3>
        <div className="rounded-lg border border-border divide-y divide-border">
          {app.redirect_uris.map((uri) => (
            <div key={uri} className="px-4 py-2.5">
              <code className="text-sm text-muted-foreground font-mono">{uri}</code>
            </div>
          ))}
          {app.redirect_uris.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">No redirect URIs</div>
          )}
        </div>
      </div>

      {/* Edit modal */}
      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Login App">
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="edit-name">Name</Label>
            <Input
              id="edit-name"
              value={editForm.name}
              onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="edit-redirect-uris">Redirect URIs (one per line)</Label>
            <Textarea
              id="edit-redirect-uris"
              value={editForm.redirect_uris}
              onChange={(e) => setEditForm((f) => ({ ...f, redirect_uris: e.target.value }))}
              rows={3}
              className="font-mono resize-none"
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="is_active"
              checked={editForm.is_active}
              onChange={(e) => setEditForm((f) => ({ ...f, is_active: e.target.checked }))}
              className="rounded border-border bg-background text-foreground"
            />
            <Label htmlFor="is_active" className="text-xs text-muted-foreground">Active</Label>
          </div>
          {update.isError && (
            <div className="text-xs text-destructive">{(update.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowEdit(false)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => {
                if (app && app.is_active && !editForm.is_active) {
                  setShowEdit(false);
                  setRevokeOnDeactivate(false);
                  setShowDeactivate(true);
                } else {
                  update.mutate({});
                }
              }}
              disabled={update.isPending}
            >
              Save
            </Button>
          </div>
        </div>
      </Modal>

      {/* Deactivation confirmation */}
      <Modal open={showDeactivate} onClose={() => setShowDeactivate(false)} title="Deactivate Login App">
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            This will prevent new logins through <span className="text-foreground font-medium">{app.name}</span>. Users with active sessions can continue until their tokens expire.
          </p>
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={revokeOnDeactivate}
              onChange={(e) => setRevokeOnDeactivate(e.target.checked)}
              className="mt-0.5 rounded border-border bg-background text-foreground"
            />
            <span className="text-sm text-foreground">Also revoke all active sessions</span>
          </label>
          {revokeOnDeactivate && (
            <p className="text-xs text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-2">
              All users currently signed in through this app will be signed out immediately.
            </p>
          )}
          {update.isError && (
            <div className="text-xs text-destructive">{(update.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => { setShowDeactivate(false); setShowEdit(true); }}>Back</Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => update.mutate({ revoke_sessions: revokeOnDeactivate })}
              disabled={update.isPending}
            >
              {update.isPending ? "Deactivating..." : "Deactivate"}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Delete confirmation */}
      <ConfirmModal
        open={showDelete}
        onClose={() => setShowDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete Login App"
        message={`This will permanently delete "${app.name}".`}
        confirmLabel="Delete App"
        danger
        isPending={remove.isPending}
        confirmInput={app.name}
        confirmInputValue={deleteName}
        onConfirmInputChange={setDeleteName}
      />
    </div>
  );
}
