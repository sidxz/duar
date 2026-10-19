import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getActionsInsights, getServiceActions, getServiceApps } from "../../api/client";
import { mcpApps } from "./agentTools";

function Tile({ to, value, label }: { to: string; value: number; label: string }) {
  return (
    <Link
      to={to}
      className="rounded-lg border border-border bg-card p-5 hover:bg-muted/50 transition-colors"
    >
      <div className="text-2xl font-bold tabular-nums text-foreground">{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{label}</div>
    </Link>
  );
}

export function McpDashboard() {
  const apps = useQuery({ queryKey: ["service-apps"], queryFn: getServiceApps });
  const actions = useQuery({ queryKey: ["service-actions"], queryFn: () => getServiceActions() });
  // Every agent-gated action starts with "mcp:", so this is exact across all apps.
  const { data: insights } = useQuery({
    queryKey: ["actions-insights", 30, "agent", "all"],
    queryFn: () => getActionsInsights(30, undefined, "mcp:"),
  });

  const list = mcpApps(apps.data ?? [], actions.data ?? []);
  const toolCount = (actions.data ?? []).filter((a) => a.action.startsWith("mcp:")).length;
  const calls30 = insights?.by_service.reduce((sum, s) => sum + s.count, 0) ?? 0;

  if (apps.isLoading || actions.isLoading) {
    return (
      <div className="grid grid-cols-3 gap-4">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-24 rounded-lg bg-muted/50 animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-4">
        <Tile to="/mcp/apps" value={list.length} label="MCP apps" />
        <Tile to="/mcp/apps" value={toolCount} label="Agent-gated tools" />
        <Tile to="/mcp/apps" value={calls30} label="Allowed gated-tool calls (30d)" />
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium">How Duar MCP works</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          An app serves MCP tools from its own API behind its Duar middleware. An agent calls them
          with the signed-in user's identity, so it can do what that user can do in the app. Tools
          the app marks as agent-gated also need a grant on <code className="font-mono">mcp:&lt;app&gt;.&lt;tool&gt;</code>,
          checked live on every call and shown here.
        </p>
        {list.length === 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            No app has registered agent-gated tools yet — see the SDK guide's MCP page.
          </p>
        )}
      </div>
    </div>
  );
}
