import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { createRealm, getRealms } from "../api/client";
import type { Realm } from "../types/api";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function Realms() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "", m2m_ttl_s: "300" });

  const { data: realms = [], isLoading, error } = useQuery({
    queryKey: ["realms"],
    queryFn: getRealms,
  });

  const create = useMutation({
    mutationFn: () =>
      createRealm({
        name: form.name,
        slug: form.slug,
        m2m_ttl_s: Number(form.m2m_ttl_s),
      }),
    onSuccess: (realm: Realm) => {
      queryClient.invalidateQueries({ queryKey: ["realms"] });
      setShowCreate(false);
      setForm({ name: "", slug: "", m2m_ttl_s: "300" });
      toast.success("Realm created");
      navigate(`/realms/${realm.id}`);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const columns = [
    {
      key: "name",
      header: "Name",
      render: (r: Realm) => <span className="font-medium text-sm">{r.name}</span>,
    },
    {
      key: "slug",
      header: "Slug",
      render: (r: Realm) => (
        <code className="text-xs text-muted-foreground font-mono">{r.slug}</code>
      ),
    },
    {
      key: "m2m_ttl_s",
      header: "m2m TTL",
      render: (r: Realm) => (
        <span className="text-sm text-foreground font-mono">{r.m2m_ttl_s}s</span>
      ),
      className: "w-24",
    },
    {
      key: "is_active",
      header: "Active",
      render: (r: Realm) => <StatusBadge active={r.is_active} />,
      className: "w-28",
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Realms</h1>
          <p className="text-sm text-muted-foreground">
            Trusted app groups that share one permission scope. Member services
            read and write permissions under the realm slug, and the realm mints
            no-user (m2m) tokens for them.
          </p>
        </div>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          New realm
        </Button>
      </div>

      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : (
        <DataTable
          columns={columns}
          data={realms}
          onRowClick={(r) => navigate(`/realms/${r.id}`)}
          emptyMessage="No realms"
          error={error}
        />
      )}

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New Realm">
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="realm-name">Display Name</Label>
            <Input
              id="realm-name"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Acme Suite"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="realm-slug">
              Slug (the shared permission scope; immutable)
            </Label>
            <Input
              id="realm-slug"
              value={form.slug}
              onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
              placeholder="acme-suite"
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">
              Starts with a letter; lowercase letters, digits, and hyphens.
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="realm-ttl">m2m token TTL (seconds)</Label>
            <Input
              id="realm-ttl"
              type="number"
              min={30}
              max={3600}
              value={form.m2m_ttl_s}
              onChange={(e) =>
                setForm((f) => ({ ...f, m2m_ttl_s: e.target.value }))
              }
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">Between 30 and 3600. Default 300.</p>
          </div>
          {create.isError && (
            <div className="text-xs text-red-700 dark:text-red-400">
              {(create.error as Error).message}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => create.mutate()}
              disabled={!form.name || !form.slug || create.isPending}
            >
              {create.isPending ? "Creating..." : "Create"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
