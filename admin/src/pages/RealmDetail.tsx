import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy } from "lucide-react";

import {
  addRealmMember,
  deleteRealm,
  getRealm,
  getRealmMembers,
  getServiceApps,
  removeRealmMember,
  updateRealm,
} from "../api/client";
import type { RealmMember } from "../types/api";
import { ConfirmModal } from "../components/ConfirmModal";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function RealmDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState({
    name: "",
    m2m_ttl_s: "300",
    is_active: true,
  });
  const [showDelete, setShowDelete] = useState(false);
  const [deleteSlug, setDeleteSlug] = useState("");
  const [selectedApp, setSelectedApp] = useState("");

  const {
    data: realm,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["realm", id],
    queryFn: () => getRealm(id!),
    enabled: !!id,
  });

  const { data: members = [] } = useQuery({
    queryKey: ["realm-members", id],
    queryFn: () => getRealmMembers(id!),
    enabled: !!id,
  });

  const { data: allApps = [] } = useQuery({
    queryKey: ["service-apps"],
    queryFn: getServiceApps,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["realm", id] });
    queryClient.invalidateQueries({ queryKey: ["realm-members", id] });
    queryClient.invalidateQueries({ queryKey: ["realms"] });
    queryClient.invalidateQueries({ queryKey: ["service-apps"] });
    queryClient.invalidateQueries({ queryKey: ["service-app"] });
  };

  const update = useMutation({
    mutationFn: () =>
      updateRealm(id!, {
        name: editForm.name || undefined,
        m2m_ttl_s: Number(editForm.m2m_ttl_s),
        is_active: editForm.is_active,
      }),
    onSuccess: () => {
      invalidate();
      setShowEdit(false);
      toast.success("Realm updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const addMember = useMutation({
    mutationFn: (serviceAppId: string) => addRealmMember(id!, serviceAppId),
    onSuccess: () => {
      invalidate();
      setSelectedApp("");
      toast.success("Service added to realm");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeMember = useMutation({
    mutationFn: (serviceAppId: string) => removeRealmMember(id!, serviceAppId),
    onSuccess: () => {
      invalidate();
      toast.success("Service removed from realm");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteRealm(id!),
    onSuccess: () => {
      setShowDelete(false);
      invalidate();
      toast.success("Realm deleted");
      navigate("/realms");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const openEdit = () => {
    if (realm) {
      setEditForm({
        name: realm.name,
        m2m_ttl_s: String(realm.m2m_ttl_s),
        is_active: realm.is_active,
      });
    }
    setShowEdit(true);
  };

  if (isLoading) {
    return <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />;
  }

  if (isError || !realm) {
    return (
      <div className="space-y-3 max-w-3xl">
        <Link
          to="/realms"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Realms
        </Link>
        <div className="border border-border rounded-lg p-6 text-center">
          <p className="text-sm text-foreground">
            {(error as Error)?.message ?? "Realm not found."}
          </p>
        </div>
      </div>
    );
  }

  // A service belongs to at most one realm — only standalone apps are candidates.
  // ponytail: candidate pre-add has_grants warning skipped — needs
  // ServiceAppResponse.has_grants (backend). Member-row badge covers it post-add.
  const candidates = allApps.filter((a) => a.realm_id == null);

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/realms" className="hover:text-foreground">
          Realms
        </Link>
        <span>/</span>
        <span className="text-foreground">{realm.name}</span>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold text-foreground">{realm.name}</h1>
          <div className="flex items-center gap-1.5">
            <code className="text-xs text-muted-foreground font-mono">
              {realm.slug}
            </code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(realm.slug);
                toast.success("Copied");
              }}
              className="text-muted-foreground hover:text-foreground transition-colors"
              title="Copy slug"
            >
              <Copy className="w-3.5 h-3.5" />
            </button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            m2m token TTL: <span className="font-mono">{realm.m2m_ttl_s}s</span>
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge active={realm.is_active} />
          <Button size="sm" onClick={openEdit}>
            Edit
          </Button>
        </div>
      </div>

      {/* Members */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-foreground">Member services</h2>
        {members.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No services yet. Add a standalone service below. Members share this
            realm's permission scope.
          </p>
        ) : (
          <ul className="space-y-1">
            {members.map((m: RealmMember) => (
              <li
                key={m.id}
                className="flex items-center justify-between border border-border rounded-md px-3 py-2"
              >
                <span className="text-sm">
                  <span className="text-foreground">{m.name}</span>
                  <code className="ml-2 text-xs text-muted-foreground font-mono">
                    {m.service_name}
                  </code>
                  {m.has_grants && (
                    <span
                      className="ml-2 text-xs text-amber-700 dark:text-amber-400"
                      title="This service has its own permission grants, which are NOT visible under the realm scope."
                    >
                      ⚠ has own grants
                    </span>
                  )}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => removeMember.mutate(m.id)}
                  className="text-destructive hover:text-destructive"
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Label htmlFor="realm_add_service" className="text-muted-foreground">
              Add a standalone service
            </Label>
            <select
              id="realm_add_service"
              value={selectedApp}
              onChange={(e) => setSelectedApp(e.target.value)}
              className="mt-1 w-full px-3 py-2 bg-background border border-border rounded-md text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">Select a service…</option>
              {candidates.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.service_name})
                </option>
              ))}
            </select>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              if (selectedApp) addMember.mutate(selectedApp);
            }}
            disabled={!selectedApp || addMember.isPending}
          >
            Add
          </Button>
        </div>
      </section>

      {/* Danger zone */}
      <section className="space-y-2 border border-destructive/30 bg-destructive/5 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-destructive">Danger zone</h2>
        <p className="text-sm text-muted-foreground">
          Deleting a realm un-assigns its member services (their{" "}
          <code className="font-mono">realm_id</code> is cleared). Permissions
          written under the realm scope are NOT deleted.
        </p>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => {
            setDeleteSlug("");
            setShowDelete(true);
          }}
        >
          Delete realm
        </Button>
      </section>

      {/* Edit modal */}
      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Realm">
        <div className="space-y-3">
          <div>
            <Label htmlFor="realm_name" className="text-muted-foreground">
              Name
            </Label>
            <Input
              id="realm_name"
              value={editForm.name}
              onChange={(e) =>
                setEditForm((f) => ({ ...f, name: e.target.value }))
              }
              className="mt-1"
            />
          </div>
          <div>
            <Label htmlFor="realm_ttl" className="text-muted-foreground">
              m2m token TTL (seconds)
            </Label>
            <Input
              id="realm_ttl"
              type="number"
              min={30}
              max={3600}
              value={editForm.m2m_ttl_s}
              onChange={(e) =>
                setEditForm((f) => ({ ...f, m2m_ttl_s: e.target.value }))
              }
              className="mt-1 font-mono"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Between 30 and 3600.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="realm_is_active"
              checked={editForm.is_active}
              onChange={(e) =>
                setEditForm((f) => ({ ...f, is_active: e.target.checked }))
              }
              className="rounded border-border bg-background text-foreground"
            />
            <Label htmlFor="realm_is_active" className="text-muted-foreground">
              Active
            </Label>
          </div>
          <p className="text-xs text-muted-foreground">
            Slug is immutable and cannot be changed.
          </p>
          {update.isError && (
            <div className="text-xs text-destructive">
              {(update.error as Error).message}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowEdit(false)}
            >
              Cancel
            </Button>
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
        title="Delete Realm"
        message={`This permanently deletes "${realm.name}" and clears realm_id on its member services. Type the slug to confirm.`}
        confirmLabel="Delete Realm"
        danger
        isPending={remove.isPending}
        confirmInput={realm.slug}
        confirmInputValue={deleteSlug}
        onConfirmInputChange={setDeleteSlug}
      />
    </div>
  );
}
