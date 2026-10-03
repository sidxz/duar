import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getActionsInsights, getAllWorkspaces } from "../api/client";
import { ActionsTrendChart, BarList } from "../components/charts";
import type { DormantGrant, UnusedRole } from "../types/api";

const RANGES = [7, 30, 90] as const;
type Days = (typeof RANGES)[number];

function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className="text-sm font-medium text-muted-foreground">{title}</h2>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="h-32 flex items-center justify-center text-sm text-muted-foreground">{text}</div>
  );
}

function DormantGrantsTable({ items, total }: { items: DormantGrant[]; total: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground border-b border-border">
            <th className="py-1.5 pr-3 font-medium">User</th>
            <th className="py-1.5 pr-3 font-medium">Action</th>
            <th className="py-1.5 pr-3 font-medium">Via role</th>
            <th className="py-1.5 font-medium">Workspace</th>
          </tr>
        </thead>
        <tbody>
          {items.map((g) => (
            <tr
              key={`${g.user_id}-${g.workspace_id}-${g.service_name}-${g.action}-${g.role_name}`}
              className="border-b border-border last:border-0"
            >
              <td className="py-1.5 pr-3">
                <div>{g.name}</div>
                <div className="text-xs text-muted-foreground">{g.email}</div>
              </td>
              <td className="py-1.5 pr-3 font-mono text-xs">
                {g.service_name}:{g.action}
              </td>
              <td className="py-1.5 pr-3">{g.role_name}</td>
              <td className="py-1.5">{g.workspace_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > items.length && (
        <div className="text-xs text-muted-foreground mt-2">
          Showing {items.length} of {total.toLocaleString()}
        </div>
      )}
    </div>
  );
}

function UnusedRolesTable({ roles }: { roles: UnusedRole[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-muted-foreground border-b border-border">
          <th className="py-1.5 pr-3 font-medium">Role</th>
          <th className="py-1.5 pr-3 font-medium">Workspace</th>
          <th className="py-1.5 font-medium">Assignees</th>
        </tr>
      </thead>
      <tbody>
        {roles.map((r) => (
          <tr key={r.id} className="border-b border-border last:border-0">
            <td className="py-1.5 pr-3">{r.name}</td>
            <td className="py-1.5 pr-3">{r.workspace_name}</td>
            <td className="py-1.5">
              {r.no_assignees ? (
                <span className="text-xs text-muted-foreground">none</span>
              ) : (
                r.assignees
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ActionsInsightsView({ workspaceId }: { workspaceId?: string }) {
  const [days, setDays] = useState<Days>(30);
  const { data, isLoading } = useQuery({
    queryKey: ["actions-insights", days, workspaceId ?? "all"],
    queryFn: () => getActionsInsights(days, workspaceId),
  });

  if (isLoading || !data) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-40 bg-muted rounded-lg" />
        <div className="grid grid-cols-2 gap-6">
          <div className="h-48 bg-muted rounded-lg" />
          <div className="h-48 bg-muted rounded-lg" />
        </div>
      </div>
    );
  }

  const partialData = data.data_since !== null && data.data_since > data.since;
  const windowHint = `Last ${days} days`;
  const sinceHint = partialData ? `Data since ${data.data_since}` : windowHint;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex rounded-md border border-border overflow-hidden">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setDays(r)}
              aria-pressed={days === r}
              className={`px-3 py-1 text-xs ${
                days === r
                  ? "bg-primary text-primary-foreground"
                  : "bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              {r}d
            </button>
          ))}
        </div>
        {partialData && (
          <span className="text-xs text-muted-foreground">
            Usage recording began {data.data_since}
          </span>
        )}
      </div>

      <Card title="Action checks" hint={sinceHint}>
        <ActionsTrendChart items={data.trend} days={days} />
      </Card>

      <div className="grid grid-cols-2 gap-6">
        <Card title="Top actions" hint={windowHint}>
          {data.top_actions.length === 0 ? (
            <Empty text="No action checks recorded yet" />
          ) : (
            <BarList
              rows={data.top_actions.map((a) => [`${a.service_name}:${a.action}`, a.count])}
              labelWidth={180}
            />
          )}
        </Card>
        <Card title="By service" hint={windowHint}>
          {data.by_service.length === 0 ? (
            <Empty text="No action checks recorded yet" />
          ) : (
            <BarList rows={data.by_service.map((s) => [s.service_name, s.count])} />
          )}
        </Card>
      </div>

      <Card title="Most active users" hint={windowHint}>
        {data.top_users.length === 0 ? (
          <Empty text="No action checks recorded yet" />
        ) : (
          <BarList rows={data.top_users.map((u) => [u.name || u.email, u.count])} labelWidth={160} />
        )}
      </Card>

      <Card title="Dormant grants" hint={`No usage since ${partialData ? data.data_since : data.since}`}>
        {data.dormant_grants.total === 0 ? (
          <Empty text="Every grant was exercised in this window" />
        ) : (
          <DormantGrantsTable items={data.dormant_grants.items} total={data.dormant_grants.total} />
        )}
      </Card>

      <Card title="Unused roles" hint={windowHint}>
        {data.unused_roles.length === 0 ? (
          <Empty text="Every role was exercised in this window" />
        ) : (
          <UnusedRolesTable roles={data.unused_roles} />
        )}
      </Card>
    </div>
  );
}

export function ActionsInsightsPage() {
  const [workspaceId, setWorkspaceId] = useState<string>();
  const { data: workspaces } = useQuery({
    queryKey: ["workspace-options"],
    queryFn: getAllWorkspaces,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Usage</h1>
        <select
          aria-label="Workspace"
          value={workspaceId ?? ""}
          onChange={(e) => setWorkspaceId(e.target.value || undefined)}
          className="px-2 py-1.5 border border-border bg-background rounded text-xs text-foreground"
        >
          <option value="">All workspaces</option>
          {workspaces?.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      </div>
      <ActionsInsightsView workspaceId={workspaceId} />
    </div>
  );
}
