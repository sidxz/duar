import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getStats, getActivity, getActivitySummary } from "../api/client";
import { StatusBadge } from "../components/Badge";
import { SignInsChart, ActivityMixChart } from "../components/charts";
import type { ActivityLog } from "../types/api";

export function Dashboard() {
  const { data, isLoading, error } = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const { data: activityData } = useQuery({
    queryKey: ["activity-dashboard"],
    queryFn: () => getActivity({ page: 1, page_size: 15 }),
  });
  const { data: summary } = useQuery({
    queryKey: ["activity-summary"],
    queryFn: () => getActivitySummary(30),
  });
  const activity = activityData?.items;

  if (isLoading) return <Skeleton />;
  if (error) return <ErrorState message={(error as Error).message} />;
  if (!data) return null;

  const cards = [
    { label: "Active Users", value: data.active_users, href: "/users" },
    { label: "Inactive Users", value: data.inactive_users, href: "/users", dim: true },
    { label: "Workspaces", value: data.total_workspaces, href: "/workspaces" },
    { label: "Groups", value: data.total_groups, href: "/workspaces" },
    { label: "Resources", value: data.total_resources, href: "/permissions" },
  ];

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold">Dashboard</h1>

      {/* Stat cards */}
      <div className="grid grid-cols-5 gap-4">
        {cards.map((c) => (
          <Link
            key={c.label}
            to={c.href}
            className="rounded-lg border border-border bg-card p-5 transition-colors hover:bg-muted/50"
          >
            <div className={`text-2xl font-bold tabular-nums ${c.dim ? "text-muted-foreground" : ""}`}>
              {c.value}
            </div>
            <div className="text-xs text-muted-foreground mt-1">{c.label}</div>
          </Link>
        ))}
      </div>

      {summary ? (
        <SecuritySignalsCard items={summary.items} days={summary.days} />
      ) : (
        <div className="h-28 bg-muted/50 rounded-lg animate-pulse" />
      )}

      {/* Security charts */}
      <div className="grid grid-cols-2 gap-6">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-baseline justify-between mb-1">
            <h2 className="text-sm font-medium text-muted-foreground">Sign-in activity</h2>
            <span className="text-xs text-muted-foreground">Last 30 days</span>
          </div>
          {summary ? (
            <SignInsChart items={summary.items} days={summary.days} />
          ) : (
            <div className="h-40 bg-muted/50 rounded animate-pulse" />
          )}
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-baseline justify-between mb-1">
            <h2 className="text-sm font-medium text-muted-foreground">Activity mix</h2>
            <span className="text-xs text-muted-foreground">Last 30 days</span>
          </div>
          {summary ? (
            <ActivityMixChart items={summary.items} days={summary.days} />
          ) : (
            <div className="h-40 bg-muted/50 rounded animate-pulse" />
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Top workspaces */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-muted-foreground">Top Workspaces</h2>
            <Link to="/workspaces" className="text-xs text-muted-foreground hover:text-foreground">
              View all &rarr;
            </Link>
          </div>
          <div className="rounded-lg border border-border divide-y divide-border">
            {data.top_workspaces.map((w) => (
              <Link
                key={w.id}
                to={`/workspaces/${w.id}`}
                className="flex items-center justify-between px-4 py-3 hover:bg-muted/50 transition-colors"
              >
                <div>
                  <div className="text-sm font-medium">{w.name}</div>
                  <div className="text-xs text-muted-foreground font-mono">{w.slug}</div>
                </div>
                <div className="text-xs text-muted-foreground tabular-nums">
                  {w.member_count} member{w.member_count !== 1 ? "s" : ""}
                </div>
              </Link>
            ))}
            {data.top_workspaces.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">No workspaces yet</div>
            )}
          </div>
        </div>

        {/* Recent users */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-muted-foreground">Recent Users</h2>
            <Link to="/users" className="text-xs text-muted-foreground hover:text-foreground">
              View all &rarr;
            </Link>
          </div>
          <div className="rounded-lg border border-border divide-y divide-border">
            {data.recent_users.map((u) => (
              <Link
                key={u.id}
                to={`/users/${u.id}`}
                className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50 transition-colors"
              >
                <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-xs font-medium text-muted-foreground shrink-0">
                  {u.name.charAt(0).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{u.name}</div>
                  <div className="text-xs text-muted-foreground truncate">{u.email}</div>
                </div>
                <StatusBadge active={u.is_active} />
              </Link>
            ))}
            {data.recent_users.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">No users yet</div>
            )}
          </div>
        </div>
      </div>

      {/* Recent activity */}
      {activity && activity.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-muted-foreground">Recent Activity</h2>
            <Link to="/activity" className="text-xs text-muted-foreground hover:text-foreground">
              View all &rarr;
            </Link>
          </div>
          <div className="rounded-lg border border-border divide-y divide-border">
            {activity.map((a) => (
              <ActivityEntry key={a.id} entry={a} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ActivityEntry({ entry }: { entry: ActivityLog }) {
  const action = entry.action.replace(/_/g, " ");
  const ago = timeAgo(entry.created_at);

  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 shrink-0" />
      <div className="flex-1 min-w-0 text-sm">
        <span className="text-foreground">{entry.actor_name ?? entry.actor_email ?? "System"}</span>{" "}
        <span className="text-muted-foreground">{action}</span>
        {entry.detail &&
          Object.entries(entry.detail).map(([k, v]) => (
            <span key={k} className="text-muted-foreground ml-1">
              {k}: <span className="text-foreground">{String(v)}</span>
            </span>
          ))}
      </div>
      <span className="text-xs text-muted-foreground shrink-0">{ago}</span>
    </div>
  );
}

const SIGNAL_TILES = [
  { action: "login_impossible_travel", label: "Impossible travel", high: true },
  { action: "credential_stuffing_suspected", label: "Credential stuffing", high: true },
  { action: "login_new_country", label: "New countries", high: false },
  { action: "login_new_device", label: "New devices", high: false },
];

function SecuritySignalsCard({ items, days = 30 }: { items: import("../types/api").ActivityDailyCount[]; days?: number }) {
  const counts = new Map<string, number>();
  for (const it of items) counts.set(it.action, (counts.get(it.action) ?? 0) + it.count);
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-medium text-muted-foreground">Security signals</h2>
        <span className="text-xs text-muted-foreground">Last {days} days</span>
      </div>
      <div className="grid grid-cols-4 gap-3">
        {SIGNAL_TILES.map((t) => {
          const n = counts.get(t.action) ?? 0;
          return (
            <Link
              key={t.action}
              to={`/activity?action=${t.action}`}
              className="rounded-md border border-border p-3 transition-colors hover:bg-muted/50"
            >
              <div
                className={`text-2xl font-bold tabular-nums ${
                  n === 0
                    ? "text-muted-foreground"
                    : t.high
                      ? "text-red-600 dark:text-red-400"
                      : ""
                }`}
              >
                {n}
              </div>
              <div className="text-xs text-muted-foreground mt-1">{t.label}</div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function Skeleton() {
  return (
    <div className="space-y-8 animate-pulse">
      <div className="h-6 w-32 bg-muted rounded" />
      <div className="grid grid-cols-5 gap-4">
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="h-20 bg-muted rounded-lg" />
        ))}
      </div>
      <div className="h-48 bg-muted rounded-lg" />
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-6 text-center">
      <div className="text-sm text-red-600 dark:text-red-400">Failed to load dashboard</div>
      <div className="text-xs text-red-500/70 mt-1">{message}</div>
    </div>
  );
}
