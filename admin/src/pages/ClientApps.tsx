import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { createClientApp, getClientApps } from "../api/client";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ClientApp } from "../types/api";

export function ClientApps() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", redirect_uris: "" });

  const { data: apps = [], isLoading, error } = useQuery({
    queryKey: ["client-apps"],
    queryFn: getClientApps,
  });

  const create = useMutation({
    mutationFn: () =>
      createClientApp({
        name: form.name,
        redirect_uris: form.redirect_uris
          .split("\n")
          .map((u) => u.trim())
          .filter(Boolean),
      }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["client-apps"] });
      setShowCreate(false);
      setForm({ name: "", redirect_uris: "" });
      toast.success("App registered");
      navigate(`/client-apps/${result.id}`);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const columns = [
    {
      key: "name",
      header: "Name",
      render: (a: ClientApp) => <span className="font-medium text-sm">{a.name}</span>,
    },
    {
      key: "uris",
      header: "URIs",
      render: (a: ClientApp) => (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ring-1 ring-inset bg-muted text-muted-foreground ring-border">
          {a.redirect_uris.length}
        </span>
      ),
      className: "w-20",
    },
    {
      key: "status",
      header: "Status",
      render: (a: ClientApp) => <StatusBadge active={a.is_active} />,
      className: "w-24",
    },
    {
      key: "created",
      header: "Created",
      render: (a: ClientApp) => (
        <span className="text-muted-foreground text-xs">
          {new Date(a.created_at).toLocaleDateString()}
        </span>
      ),
      className: "w-28",
    },
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Login Apps</h1>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          + Register App
        </Button>
      </div>

      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : (
        <DataTable
          columns={columns}
          data={apps}
          onRowClick={(a) => navigate(`/client-apps/${a.id}`)}
          emptyMessage="No login apps registered"
          error={error}
        />
      )}

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Register Login App">
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="client-app-name">Name</Label>
            <Input
              id="client-app-name"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="My Application"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="client-app-uris">Redirect URIs (one per line)</Label>
            <Textarea
              id="client-app-uris"
              value={form.redirect_uris}
              onChange={(e) => setForm((f) => ({ ...f, redirect_uris: e.target.value }))}
              placeholder={"https://app.example.com/callback\nhttp://localhost:3000/callback"}
              rows={3}
              className="font-mono resize-none"
            />
          </div>
          {create.isError && (
            <div className="text-xs text-destructive">{(create.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => create.mutate()}
              disabled={!form.name || !form.redirect_uris.trim() || create.isPending}
            >
              {create.isPending ? "Creating..." : "Create"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
