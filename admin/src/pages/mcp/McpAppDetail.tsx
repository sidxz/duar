import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import { getActionsInsights, getServiceActions, getServiceApp } from "../../api/client";
import { StatusBadge } from "../../components/Badge";
import { agentPrefix, agentTools, toolName } from "./agentTools";

type Tab = "tools" | "grants" | "audit";

function copyValue(value: string) {
  navigator.clipboard.writeText(value);
  toast.success("Copied");
}

function CopyButton({ value, label }: { value: string; label: string }) {
  return (
    <button
      onClick={() => copyValue(value)}
      className="ml-1 align-middle text-muted-foreground hover:text-foreground"
      aria-label={label}
    >
      <Copy className="w-3.5 h-3.5" />
    </button>
  );
}

function useAgentTools(serviceName: string) {
  const { data = [], isLoading } = useQuery({
    queryKey: ["service-actions"],
    queryFn: () => getServiceActions(),
  });
  return { tools: agentTools(data, serviceName), isLoading };
}

function ToolsTab({ serviceName }: { serviceName: string }) {
  const { tools, isLoading } = useAgentTools(serviceName);
  if (isLoading) return <div className="h-40 bg-muted/50 rounded-lg animate-pulse" />;

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Agents need a grant to use these tools, on top of the user's own permissions. Other tools use
        the user's permissions only and aren't listed here.
      </p>
      {tools.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
          This app has no agent-gated tools.
        </div>
      ) : (
        <div className="table-surface">
          <table className="admin-table">
            <thead>
              <tr className="text-left text-xs text-muted-foreground border-b border-border">
                <th className="px-4 py-2 font-medium">Tool</th>
                <th className="px-4 py-2 font-medium">Grant action</th>
                <th className="px-4 py-2 font-medium">Description</th>
                <th className="px-4 py-2 font-medium w-28">Registered</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((t) => (
                <tr key={t.id}>
                  <td className="px-4 py-2 font-mono text-xs text-foreground">
                    {toolName(t.action, serviceName)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                    {t.action}
                    <CopyButton value={t.action} label={`Copy ${t.action}`} />
                  </td>
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
  const { tools } = useAgentTools(serviceName);

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium">Give agents access</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Agents act as the signed-in user. To let them use a gated tool, add its action to a
          workspace role and assign that role. Open a{" "}
          <Link to="/workspaces" className="text-mcp-accent hover:underline">workspace</Link> to manage
          its roles. Owners and admins need the grant too.
        </p>
      </div>
      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium">Available actions</h3>
        {tools.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">No agent-gated tools.</p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {tools.map((t) => (
              <code
                key={t.id}
                className="rounded bg-muted px-2 py-0.5 font-mono text-xs text-foreground"
              >
                {t.action}
                <CopyButton value={t.action} label={`Copy ${t.action}`} />
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
  const prefix = agentPrefix(serviceName);
  const { data, isLoading } = useQuery({
    queryKey: ["actions-insights", days, "agent", serviceName],
    queryFn: () => getActionsInsights(days, undefined, prefix),
  });

  if (isLoading || !data) return <div className="h-40 bg-muted/50 rounded-lg animate-pulse" />;

  // Scoped server-side to this app's agent actions, so every number here is exact.
  const allowed = data.by_service.reduce((sum, s) => sum + s.count, 0);
  const denied = data.trend.reduce((sum, t) => sum + t.denied, 0);

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
        <Link to="/activity" className="text-xs text-mcp-accent hover:underline">
          Denied calls →
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-5">
          <div className="text-2xl font-bold tabular-nums text-foreground">{allowed}</div>
          <div className="mt-1 text-xs text-muted-foreground">Allowed gated-tool calls ({days}d)</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-5">
          <div className="text-2xl font-bold tabular-nums text-foreground">{denied}</div>
          <div className="mt-1 text-xs text-muted-foreground">Denied gated-tool calls ({days}d)</div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium">Most-used tools</h3>
        {data.top_actions.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">No allowed calls in this period.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border">
            {data.top_actions.map((a) => (
              <li key={`${a.service_name}/${a.action}`} className="flex justify-between py-1.5 text-sm">
                <code className="font-mono text-xs text-foreground">{toolName(a.action, serviceName)}</code>
                <span className="tabular-nums text-muted-foreground">{a.count}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function McpAppDetail() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<Tab>("tools");

  const { data: app, isLoading, isError, error } = useQuery({
    queryKey: ["service-app", id],
    queryFn: () => getServiceApp(id!),
    enabled: !!id,
  });

  if (isLoading) {
    return <div className="animate-pulse h-64 bg-muted/50 rounded-lg" />;
  }

  if (isError || !app) {
    return (
      <div className="space-y-3">
        <Link to="/mcp/apps" className="text-sm text-muted-foreground hover:text-foreground">
          ← MCP Apps
        </Link>
        <div className="border border-border rounded-lg p-6 text-center">
          <p className="text-sm text-foreground">{(error as Error)?.message ?? "App not found."}</p>
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
        <Link to="/mcp/apps" className="hover:text-foreground">
          MCP Apps
        </Link>
        <span>/</span>
        <span className="text-foreground">{app.name}</span>
      </div>

      <div className="rounded-lg border border-border bg-card p-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold">{app.name}</h2>
              <StatusBadge active={app.is_active} />
            </div>
            <div className="mt-2 space-y-1 text-xs text-muted-foreground">
              <div>
                Service: <code className="text-foreground font-mono">{app.service_name}</code>
                <CopyButton value={app.service_name} label="Copy service name" />
              </div>
              <div>
                MCP endpoint: <code className="text-foreground font-mono">&lt;app API&gt;/mcp/</code>
              </div>
            </div>
          </div>
          <Link
            to={`/service-apps/${app.id}`}
            className="text-xs text-mcp-accent hover:underline"
          >
            Key &amp; settings in Services →
          </Link>
        </div>
      </div>

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
    </div>
  );
}
