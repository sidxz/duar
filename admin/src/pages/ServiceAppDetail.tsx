import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import {
  deleteServiceApp,
  getRealms,
  getServiceApp,
  purgeServicePermissions,
  rotateServiceAppKey,
  updateServiceApp,
} from "../api/client";
import { StatusBadge } from "../components/Badge";
import { ConfirmModal } from "../components/ConfirmModal";
import { Modal } from "../components/Modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ServiceAppCreateResponse } from "../types/api";

function copyValue(value: string) {
  navigator.clipboard.writeText(value);
  toast.success("Copied");
}

function KeyRevealModal({
  open,
  onClose,
  apiKey,
}: {
  open: boolean;
  onClose: () => void;
  apiKey: string;
}) {
  return (
    <Modal open={open} onClose={onClose} title="New API Key">
      <div className="space-y-4">
        <div className="text-xs text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-2">
          Copy this key now. It will not be shown again.
        </div>
        <div className="flex items-center gap-2">
          <code className="flex-1 px-3 py-2 bg-muted border border-border rounded-md text-sm text-foreground font-mono break-all select-all">
            {apiKey}
          </code>
          <Button
            variant="outline"
            size="sm"
            onClick={() => copyValue(apiKey)}
            className="shrink-0"
          >
            <Copy className="w-3.5 h-3.5" />
            Copy
          </Button>
        </div>
        <div className="flex justify-end pt-2">
          <Button size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function ServiceAppDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showEdit, setShowEdit] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [showRotate, setShowRotate] = useState(false);
  const [showPurge, setShowPurge] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [purgeName, setPurgeName] = useState("");
  const [editForm, setEditForm] = useState({ name: "", is_active: true, allowed_origins: "" });
  const [revealKey, setRevealKey] = useState<string | null>(null);

  const { data: app, isLoading, isError, error } = useQuery({
    queryKey: ["service-app", id],
    queryFn: () => getServiceApp(id!),
    enabled: !!id,
  });

  const { data: realms = [] } = useQuery({
    queryKey: ["realms"],
    queryFn: getRealms,
  });
  const realmName = app?.realm_id
    ? (realms.find((r) => r.id === app.realm_id)?.name ?? app.realm_id)
    : null;

  const update = useMutation({
    mutationFn: () =>
      updateServiceApp(id!, {
        name: editForm.name || undefined,
        is_active: editForm.is_active,
        allowed_origins: editForm.allowed_origins
          ? editForm.allowed_origins.split("\n").map((s) => s.trim()).filter(Boolean)
          : [],
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["service-app", id] });
      queryClient.invalidateQueries({ queryKey: ["service-apps"] });
      setShowEdit(false);
      toast.success("Service updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const rotate = useMutation({
    mutationFn: () => rotateServiceAppKey(id!),
    onSuccess: (result: ServiceAppCreateResponse) => {
      queryClient.invalidateQueries({ queryKey: ["service-app", id] });
      queryClient.invalidateQueries({ queryKey: ["service-apps"] });
      setShowRotate(false);
      toast.success("Key rotated");
      setRevealKey(result.api_key);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteServiceApp(id!),
    onSuccess: () => {
      setShowDelete(false);
      queryClient.invalidateQueries({ queryKey: ["service-apps"] });
      toast.success("Service and all its permissions deleted");
      navigate("/service-apps");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const purge = useMutation({
    mutationFn: () => purgeServicePermissions(app!.service_name),
    onSuccess: (data) => {
      setShowPurge(false);
      setPurgeName("");
      queryClient.invalidateQueries({ queryKey: ["admin-permissions"] });
      toast.success(`Purged ${data.deleted_count} permission(s) for ${app!.service_name}`);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const openEdit = () => {
    if (app) {
      setEditForm({
        name: app.name,
        is_active: app.is_active,
        allowed_origins: (app.allowed_origins || []).join("\n"),
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
          to="/service-apps"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Services
        </Link>
        <div className="border border-border rounded-lg p-6 text-center">
          <p className="text-sm text-foreground">
            {(error as Error)?.message ?? "Service not found."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/service-apps" className="hover:text-foreground">Services</Link>
        <span>/</span>
        <span className="text-foreground">{app.name}</span>
      </div>

      {/* Header card */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold">{app.name}</h2>
              <StatusBadge active={app.is_active} />
            </div>
            <div className="mt-2 space-y-1">
              <div className="text-xs text-muted-foreground">
                Service:{" "}
                <code className="text-foreground font-mono">{app.service_name}</code>
                <button
                  onClick={() => copyValue(app.service_name)}
                  className="ml-1 align-middle text-muted-foreground hover:text-foreground"
                  aria-label="Copy service name"
                >
                  <Copy className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="text-xs text-muted-foreground">
                Key: <code className="text-foreground font-mono">{app.key_prefix}</code>
              </div>
              {realmName && (
                <div className="text-xs text-muted-foreground">
                  Realm:{" "}
                  <Link
                    to={`/realms/${app.realm_id}`}
                    className="text-foreground hover:text-foreground/80 font-mono"
                  >
                    {realmName}
                  </Link>
                </div>
              )}
              <div className="text-xs text-muted-foreground">
                Last used: {app.last_used_at ? new Date(app.last_used_at).toLocaleString() : "Never"}
              </div>
              <div className="text-xs text-muted-foreground">
                Created {new Date(app.created_at).toLocaleDateString()}
              </div>
              {app.allowed_origins && app.allowed_origins.length > 0 && (
                <div className="text-xs text-muted-foreground">
                  Origins:{" "}
                  {app.allowed_origins.map((o: string, i: number) => (
                    <code key={i} className="text-foreground font-mono">
                      {o}{i < app.allowed_origins.length - 1 ? ", " : ""}
                    </code>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={openEdit}>
              Edit
            </Button>
            <Button
              size="sm"
              onClick={() => setShowRotate(true)}
              className="bg-amber-500/10 text-amber-700 dark:text-amber-400 hover:bg-amber-500/20 ring-1 ring-amber-500/20"
            >
              Rotate Key
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => { setShowPurge(true); setPurgeName(""); }}
            >
              Purge Permissions
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

      {/* Edit modal */}
      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Service">
        <div className="space-y-3">
          <div>
            <Label htmlFor="edit-name" className="text-xs text-muted-foreground">Name</Label>
            <Input
              id="edit-name"
              value={editForm.name}
              onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
              className="mt-1"
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
          <div>
            <Label htmlFor="edit-origins" className="text-xs text-muted-foreground">Allowed Origins</Label>
            <Textarea
              id="edit-origins"
              value={editForm.allowed_origins}
              onChange={(e) => setEditForm((f) => ({ ...f, allowed_origins: e.target.value }))}
              placeholder={"https://app.example.com\nhttps://staging.example.com"}
              rows={3}
              className="mt-1 font-mono"
            />
            <p className="mt-1 text-xs text-muted-foreground">One origin per line. Required for browser-direct authz mode.</p>
          </div>
          {update.isError && (
            <div className="text-xs text-destructive">{(update.error as Error).message}</div>
          )}
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

      {/* Rotate key confirmation */}
      <ConfirmModal
        open={showRotate}
        onClose={() => setShowRotate(false)}
        onConfirm={() => rotate.mutate()}
        title="Rotate API Key"
        message={`This will invalidate the current key for "${app.name}". Any services using the old key will stop working immediately.`}
        confirmLabel="Rotate Key"
        danger
        isPending={rotate.isPending}
      />

      {/* Delete confirmation */}
      <ConfirmModal
        open={showDelete}
        onClose={() => setShowDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete Service"
        message={`This will permanently delete "${app.name}", invalidate its API key, and purge all stored permissions for service "${app.service_name}". Type the service name to confirm.`}
        confirmLabel="Delete Service"
        danger
        isPending={remove.isPending}
        confirmInput={app.service_name}
        confirmInputValue={deleteName}
        onConfirmInputChange={setDeleteName}
      />

      {/* Purge permissions confirmation */}
      <ConfirmModal
        open={showPurge}
        onClose={() => setShowPurge(false)}
        onConfirm={() => purge.mutate()}
        title="Purge All Permissions"
        message={`This will delete ALL resource permissions and shares for service "${app.service_name}". This cannot be undone. Type the service name to confirm.`}
        confirmLabel="Purge Permissions"
        danger
        isPending={purge.isPending}
        confirmInput={app.service_name}
        confirmInputValue={purgeName}
        onConfirmInputChange={setPurgeName}
      />

      {/* Key reveal */}
      <KeyRevealModal
        open={!!revealKey}
        onClose={() => setRevealKey(null)}
        apiKey={revealKey ?? ""}
      />
    </div>
  );
}
