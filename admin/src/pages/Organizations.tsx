import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { createOrganization, getOrganizations } from "../api/client";
import type { Organization } from "../types/api";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function Organizations() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "" });

  const { data: orgs = [], isLoading, error } = useQuery({
    queryKey: ["organizations"],
    queryFn: getOrganizations,
  });

  const create = useMutation({
    mutationFn: () => createOrganization({ name: form.name, slug: form.slug }),
    onSuccess: (org: Organization) => {
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
      setShowCreate(false);
      setForm({ name: "", slug: "" });
      toast.success("Organization created");
      navigate(`/organizations/${org.id}`);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const columns = [
    {
      key: "name",
      header: "Name",
      render: (o: Organization) => (
        <span className="font-medium text-sm">
          {o.is_public && <span className="mr-1">🌐</span>}
          {o.name}
          {o.is_public && (
            <span className="ml-2 text-xs text-muted-foreground">
              (public catch-all)
            </span>
          )}
        </span>
      ),
    },
    {
      key: "slug",
      header: "Slug",
      render: (o: Organization) => (
        <code className="text-xs text-muted-foreground font-mono">{o.slug}</code>
      ),
    },
    {
      key: "domains",
      header: "Domains",
      render: (o: Organization) => (
        <span className="text-sm text-muted-foreground">
          {o.is_public ? "-" : o.domain_count}
        </span>
      ),
      className: "w-24",
    },
    {
      key: "enabled",
      header: "Enabled",
      render: (o: Organization) => <StatusBadge active={o.enabled} />,
      className: "w-28",
    },
    {
      key: "users",
      header: "Users",
      render: (o: Organization) => (
        <span className="text-sm text-muted-foreground">{o.user_count}</span>
      ),
      className: "w-20",
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Organizations</h1>
          <p className="text-sm text-muted-foreground">
            Email-domain tenancy. The public org is the catch-all for unclaimed
            domains; its status is the public sign-in switch.
          </p>
        </div>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          New org
        </Button>
      </div>

      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : (
        <DataTable
          columns={columns}
          data={orgs}
          onRowClick={(o) => navigate(`/organizations/${o.id}`)}
          emptyMessage="No organizations"
          error={error}
        />
      )}

      <Modal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="New Organization"
      >
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="org-name">Display Name</Label>
            <Input
              id="org-name"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Texas A&M University"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="org-slug">
              Slug (in the token's org claim; immutable)
            </Label>
            <Input
              id="org-slug"
              value={form.slug}
              onChange={(e) =>
                setForm((f) => ({ ...f, slug: e.target.value }))
              }
              placeholder="tamu"
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">
              Lowercase letters, digits, and hyphens; min 2 characters.
            </p>
          </div>
          {create.isError && (
            <div className="text-xs text-red-700 dark:text-red-400">
              {(create.error as Error).message}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowCreate(false)}
            >
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
