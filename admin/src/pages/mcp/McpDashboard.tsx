import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getActionsInsights, getServiceActions, getServiceApps } from "../../api/client";

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
  const { data: servers = [], isLoading } = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => getServiceApps("mcp_server"),
  });
  const { data: actions = [] } = useQuery({
    queryKey: ["service-actions"],
    queryFn: () => getServiceActions(),
  });
  const { data: insights } = useQuery({
    queryKey: ["actions-insights", 30, "all"],
    queryFn: () => getActionsInsights(30),
  });

  const serverNames = new Set(servers.map((s) => s.service_name));
  const toolCount = actions.filter((a) => serverNames.has(a.service_name)).length;
  const calls30 =
    insights?.by_service
      .filter((s) => serverNames.has(s.service_name))
      .reduce((sum, s) => sum + s.count, 0) ?? 0;

  if (isLoading) {
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
        <Tile to="/mcp/servers" value={servers.length} label="MCP servers" />
        <Tile to="/mcp/servers" value={toolCount} label="Registered tools" />
        <Tile to="/usage" value={calls30} label="Tool calls (30d)" />
      </div>

      {servers.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card px-4 py-10 text-center">
          <p className="text-sm text-foreground">No MCP servers registered yet.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Register an MCP server to let human-driven AI agents call its tools — Duar
            authorizes every call.
          </p>
          <Link
            to="/mcp/servers"
            className="mt-4 inline-block rounded-md bg-mcp-accent px-3 py-1.5 text-sm font-medium text-mcp-accent-foreground hover:opacity-90"
          >
            Register an MCP server
          </Link>
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-medium">How Duar MCP works</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Each MCP server verifies the user's Duar token, then asks Duar whether that
            user may call the requested tool in their workspace. Tools are RBAC actions;
            grant them to principals from a workspace's Roles. Open a server to see its
            tools, grants, and tool-call audit.
          </p>
        </div>
      )}
    </div>
  );
}
