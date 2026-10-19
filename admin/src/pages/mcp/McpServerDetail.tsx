import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import {
  deleteServiceApp,
  getActionsInsights,
  getServiceActions,
  getServiceApp,
  rotateServiceAppKey,
} from "../../api/client";
import { StatusBadge } from "../../components/Badge";
import { ConfirmModal } from "../../components/ConfirmModal";
import { Modal } from "../../components/Modal";
import { Button } from "@/components/ui/button";
import type { ServiceAppCreateResponse } from "../../types/api";

type Tab = "tools" | "grants" | "audit";

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
    <Modal open={open} onClose={onClose} title="New Server Key">
      <div className="space-y-4">
        <div className="text-xs text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-2">
          Copy this key now. It will not be shown again.
        </div>
        <div className="flex items-center gap-2">
          <code className="flex-1 px-3 py-2 bg-muted border border-border rounded-md text-sm text-foreground font-mono break-all select-all">
            {apiKey}
          </code>
          <Button variant="outline" size="sm" onClick={() => copyValue(apiKey)} className="shrink-0">
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

function ToolsTab({ serviceName }: { serviceName: string }) {
  const { data: tools = [], isLoading } = useQuery({
    queryKey: ["service-actions", serviceName],
    queryFn: () => getServiceActions(serviceName),
  });

  if (isLoading) return <div className="h-40 bg-muted/50 rounded-lg animate-pulse" />;

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Tools are registered by the server (via <code className="font-mono">sync_tools</code> at
        startup, or the roles API) and are RBAC actions under{" "}
        <code className="font-mono">{serviceName}</code>. Grant them to principals from a
        workspace's Roles.
      </p>
      {tools.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
          No tools registered yet for this server.
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground border-b border-border">
                <th className="px-4 py-2 font-medium">Tool</th>
                <th className="px-4 py-2 font-medium">Description</th>
                <th className="px-4 py-2 font-medium w-28">Registered</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {tools.map((t) => (
                <tr key={t.id} className="hover:bg-muted/50">
                  <td className="px-4 py-2 font-mono text-xs text-foreground">{t.action}</td>
                  <td className="px-4 py-2 text-muted-foreground">{t.description || "-"}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {new Date(t.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function GrantsTab({ serviceName }: { serviceName: string }) {
  const { data: tools = [] } = useQuery({
    queryKey: ["service-actions", serviceName],
    queryFn: () => getServiceActions(serviceName),
  });

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium">Granting tools</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Each MCP tool is an RBAC action. To let a user call it, assign them a workspace
          Role that includes the action. Manage grants from a workspace's{" "}
          <Link to="/workspaces" className="text-mcp-accent hover:underline">
            Roles
          </Link>{" "}
          tab — a grant made there is the exact object this server checks on every call.
        </p>
      </div>
      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium">This server's grantable tools</h3>
        {tools.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">No tools registered yet.</p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {tools.map((t) => (
              <code
                key={t.id}
                className="rounded bg-muted px-2 py-0.5 font-mono text-xs text-foreground"
              >
                {t.action}
              </code>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const RANGES = [7, 30, 90] as const;

function AuditTab({ serviceName }: { serviceName: string }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const { data, isLoading } = useQuery({
    queryKey: ["actions-insights", days, "all"],
    queryFn: () => getActionsInsights(days),
  });

  if (isLoading || !data) return <div className="h-40 bg-muted/50 rounded-lg animate-pulse" />;

  // by_service is the unlimited per-service rollup, so this count is exact for
  // this server. (top_actions is a global top-20 and can't be reliably scoped to
  // one service — the per-tool breakdown lives behind "Full usage".)
  const calls = data.by_service.find((s) => s.service_name === serviceName)?.count ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex rounded-md border border-border overflow-hidden">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setDays(r)}
              aria-pressed={days === r}
              className={`px-3 py-1 text-xs ${
                days === r
                  ? "bg-mcp-accent text-mcp-accent-foreground"
                  : "bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              {r}d
            </button>
          ))}
        </div>
        <div className="flex gap-3 text-xs">
          <Link to="/usage" className="text-mcp-accent hover:underline">
            Full usage →
          </Link>
          <Link to="/activity" className="text-mcp-accent hover:underline">
            Denied calls →
          </Link>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-5">
        <div className="text-2xl font-bold tabular-nums text-foreground">{calls}</div>
        <div className="mt-1 text-xs text-muted-foreground">
          Allowed tool calls in the last {days} days
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          Per-tool breakdown is in{" "}
          <Link to="/usage" className="text-mcp-accent hover:underline">
            Full usage
          </Link>
          ; denied calls are in{" "}
          <Link to="/activity" className="text-mcp-accent hover:underline">
            Activity
          </Link>
          .
        </p>
      </div>
    </div>
  );
}

export function McpServerDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("tools");
  const [showRotate, setShowRotate] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deleteName, setDeleteName] = useState("");
  const [revealKey, setRevealKey] = useState<string | null>(null);

  const { data: app, isLoading, isError, error } = useQuery({
    queryKey: ["service-app", id],
    queryFn: () => getServiceApp(id!),
    enabled: !!id,
  });

  const rotate = useMutation({
    mutationFn: () => rotateServiceAppKey(id!),
    onSuccess: (result: ServiceAppCreateResponse) => {
      queryClient.invalidateQueries({ queryKey: ["service-app", id] });
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
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
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
      // Realm picker lists all apps under ["service-apps","all"] (prefix match).
      queryClient.invalidateQueries({ queryKey: ["service-apps"] });
      toast.success("MCP server and its permissions deleted");
      navigate("/mcp/servers");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  if (isLoading) {
    return <div className="animate-pulse h-64 bg-muted/50 rounded-lg" />;
  }

  // Not-found guard — missing id, load error, or an app that isn't an MCP server
  // (e.g. a plain service app id pasted into /mcp/servers/:id). Review Focus 5.
  if (isError || !app || app.kind !== "mcp_server") {
    return (
      <div className="space-y-3">
        <Link to="/mcp/servers" className="text-sm text-muted-foreground hover:text-foreground">
          ← MCP Servers
        </Link>
        <div className="border border-border rounded-lg p-6 text-center">
          <p className="text-sm text-foreground">
            {(error as Error)?.message ?? "MCP server not found."}
          </p>
        </div>
      </div>
    );
  }

  const TABS: { key: Tab; label: string }[] = [
    { key: "tools", label: "Tools" },
    { key: "grants", label: "Grants" },
    { key: "audit", label: "Tool-call audit" },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/mcp/servers" className="hover:text-foreground">
          MCP Servers
        </Link>
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
                Service: <code className="text-foreground font-mono">{app.service_name}</code>
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
              <div className="text-xs text-muted-foreground">
                Last used:{" "}
                {app.last_used_at ? new Date(app.last_used_at).toLocaleString() : "Never"}
              </div>
              <div className="text-xs text-muted-foreground">
                Created {new Date(app.created_at).toLocaleDateString()}
              </div>
            </div>
          </div>
          <div className="flex gap-2">
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
              onClick={() => {
                setShowDelete(true);
                setDeleteName("");
              }}
            >
              Delete
            </Button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            aria-pressed={tab === t.key}
            className={`-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
              tab === t.key
                ? "border-mcp-accent text-foreground font-medium"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "tools" && <ToolsTab serviceName={app.service_name} />}
      {tab === "grants" && <GrantsTab serviceName={app.service_name} />}
      {tab === "audit" && <AuditTab serviceName={app.service_name} />}

      <ConfirmModal
        open={showRotate}
        onClose={() => setShowRotate(false)}
        onConfirm={() => rotate.mutate()}
        title="Rotate Server Key"
        message={`This invalidates the current key for "${app.name}". The MCP server must be redeployed with the new key or it will stop authorizing tool calls.`}
        confirmLabel="Rotate Key"
        danger
        isPending={rotate.isPending}
      />

      <ConfirmModal
        open={showDelete}
        onClose={() => setShowDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete MCP Server"
        message={`This permanently deletes "${app.name}", invalidates its key, and purges all stored permissions for "${app.service_name}". Type the service name to confirm.`}
        confirmLabel="Delete Server"
        danger
        isPending={remove.isPending}
        confirmInput={app.service_name}
        confirmInputValue={deleteName}
        onConfirmInputChange={setDeleteName}
      />

      <KeyRevealModal
        open={!!revealKey}
        onClose={() => setRevealKey(null)}
        apiKey={revealKey ?? ""}
      />
    </div>
  );
}
