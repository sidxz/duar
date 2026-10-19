import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import { createServiceApp, getServiceApps } from "../api/client";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ServiceApp, ServiceAppCreateResponse } from "../types/api";

function KeyRevealModal({
  open,
  onClose,
  apiKey,
}: {
  open: boolean;
  onClose: () => void;
  apiKey: string;
}) {
  const copy = () => {
    navigator.clipboard.writeText(apiKey);
    toast.success("Copied");
  };

  return (
    <Modal open={open} onClose={onClose} title="API Key Created">
      <div className="space-y-4">
        <div className="text-xs text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-2">
          Copy this key now. It will not be shown again.
        </div>
        <div className="flex items-center gap-2">
          <code className="flex-1 px-3 py-2 bg-muted border border-border rounded-md text-sm text-foreground font-mono break-all select-all">
            {apiKey}
          </code>
          <Button variant="outline" size="sm" onClick={copy} className="shrink-0">
            <Copy className="size-4" />
            Copy
          </Button>
        </div>
        <div className="flex justify-end pt-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function ServiceApps() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", service_name: "", allowed_origins: "" });
  const [revealKey, setRevealKey] = useState<string | null>(null);

  const { data: apps = [], isLoading, error } = useQuery({
    queryKey: ["service-apps"],
    queryFn: getServiceApps,
  });

  const create = useMutation({
    mutationFn: () =>
      createServiceApp({
        name: form.name,
        service_name: form.service_name,
        allowed_origins: form.allowed_origins
          ? form.allowed_origins.split("\n").map((s) => s.trim()).filter(Boolean)
          : [],
      }),
    onSuccess: (result: ServiceAppCreateResponse) => {
      queryClient.invalidateQueries({ queryKey: ["service-apps"] });
      setShowCreate(false);
      setForm({ name: "", service_name: "", allowed_origins: "" });
      toast.success("Service registered");
      setRevealKey(result.api_key);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const columns = [
    {
      key: "name",
      header: "Name",
      render: (a: ServiceApp) => <span className="font-medium text-sm">{a.name}</span>,
    },
    {
      key: "service_name",
      header: "Service Name",
      render: (a: ServiceApp) => (
        <code className="text-xs text-muted-foreground font-mono">{a.service_name}</code>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (a: ServiceApp) => <StatusBadge active={a.is_active} />,
      className: "w-24",
    },
    {
      key: "last_used",
      header: "Last Used",
      render: (a: ServiceApp) => (
        <span className="text-muted-foreground text-xs">
          {a.last_used_at ? new Date(a.last_used_at).toLocaleDateString() : "Never"}
        </span>
      ),
      className: "w-28",
    },
    {
      key: "created",
      header: "Created",
      render: (a: ServiceApp) => (
        <span className="text-muted-foreground text-xs">{new Date(a.created_at).toLocaleDateString()}</span>
      ),
      className: "w-28",
    },
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Services</h1>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          + Register Service
        </Button>
      </div>

      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : (
        <DataTable
          columns={columns}
          data={apps}
          onRowClick={(a) => navigate(`/service-apps/${a.id}`)}
          emptyMessage="No services registered"
          error={error}
        />
      )}

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Register Service">
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="svc-name">Display Name</Label>
            <Input
              id="svc-name"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="My Backend"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="svc-service-name">Service Name</Label>
            <Input
              id="svc-service-name"
              value={form.service_name}
              onChange={(e) => setForm((f) => ({ ...f, service_name: e.target.value }))}
              placeholder="my-backend"
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">Lowercase, hyphens only (e.g. my-backend)</p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="svc-origins">Allowed Origins</Label>
            <Textarea
              id="svc-origins"
              value={form.allowed_origins}
              onChange={(e) => setForm((f) => ({ ...f, allowed_origins: e.target.value }))}
              placeholder={"https://app.example.com\nhttps://staging.example.com"}
              rows={3}
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">One origin per line. Required for browser-direct authz mode.</p>
          </div>
          {create.isError && (
            <div className="text-xs text-red-700 dark:text-red-400">{(create.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => create.mutate()}
              disabled={!form.name || !form.service_name || create.isPending}
            >
              {create.isPending ? "Creating..." : "Create"}
            </Button>
          </div>
        </div>
      </Modal>

      <KeyRevealModal
        open={!!revealKey}
        onClose={() => setRevealKey(null)}
        apiKey={revealKey ?? ""}
      />
    </div>
  );
}
