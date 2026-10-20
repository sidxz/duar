import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plug } from "lucide-react";
import { getServiceActions, getServiceApps } from "../../api/client";
import { DataTable } from "../../components/DataTable";
import { StatusBadge } from "../../components/Badge";
import { TableIdentity } from "../../components/TableIdentity";
import type { ServiceApp } from "../../types/api";
import { agentTools, mcpApps, useRealms } from "./agentTools";

export function McpApps() {
  const navigate = useNavigate();
  const apps = useQuery({ queryKey: ["service-apps"], queryFn: getServiceApps });
  const actions = useQuery({ queryKey: ["service-actions"], queryFn: () => getServiceActions() });

  const realms = useRealms();

  const list = mcpApps(apps.data ?? [], actions.data ?? [], realms.data ?? []);

  const columns = [
    {
      key: "name",
      header: "App",
      render: (a: ServiceApp) => <TableIdentity name={a.name} Icon={Plug} tone="violet" />,
    },
    {
      key: "service_name",
      header: "Service Name",
      render: (a: ServiceApp) => (
        <code className="text-xs text-muted-foreground font-mono">{a.service_name}</code>
      ),
    },
    {
      key: "tools",
      header: "Gated Tools",
      render: (a: ServiceApp) => (
        <span className="tabular-nums text-sm">
          {agentTools(actions.data ?? [], a, realms.data ?? []).length}
        </span>
      ),
      className: "w-28",
    },
    {
      key: "status",
      header: "Status",
      render: (a: ServiceApp) => <StatusBadge active={a.is_active} />,
      className: "w-24",
    },
  ];

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">MCP Apps</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Apps that serve MCP tools from their own API and have agent-gated tools registered with Duar.
        </p>
      </div>

      {apps.isLoading || actions.isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : (
        <DataTable
          columns={columns}
          data={list}
          onRowClick={(a) => navigate(`/mcp/apps/${a.id}`)}
          emptyMessage="No apps with agent-gated tools"
          error={apps.error ?? actions.error}
        />
      )}
    </div>
  );
}
