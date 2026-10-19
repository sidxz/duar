import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react";
import { Button } from "../components/ui/button";
import { PageHeader } from "../components/PageHeader";
import { getStats, getActivity, getActivitySummary } from "../api/client";
import { StatusBadge } from "../components/Badge";
import { SignInsChart, ActivityMixChart } from "../components/charts";
import type { ActivityDailyCount, ActivityLog } from "../types/api";

export function Dashboard() {
  const statsQuery = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const activityQuery = useQuery({
    queryKey: ["activity-dashboard"],
    queryFn: () => getActivity({ page: 1, page_size: 8 }),
  });
  const summaryQuery = useQuery({ queryKey: ["activity-summary"], queryFn: () => getActivitySummary(30) });
  const { data, isLoading, error } = statsQuery;
  const summary = summaryQuery.data;
  const activity = activityQuery.data?.items;
  const refreshing = statsQuery.isFetching || activityQuery.isFetching || summaryQuery.isFetching;
  const refresh = () => { void statsQuery.refetch(); void activityQuery.refetch(); void summaryQuery.refetch(); };

  if (isLoading) return <div role="status" aria-label="Loading dashboard" className="space-y-8 animate-pulse"><div className="h-16 w-64 rounded bg-muted" /><div className="h-24 bg-muted" /><div className="h-80 bg-muted" /></div>;
  if (error) return <ErrorState message={error.message} onRetry={refresh} />;
  if (!data) return null;

  const metrics = [
    { label: "Active users", value: data.active_users, href: "/users" },
    { label: "Inactive users", value: data.inactive_users, href: "/users" },
    { label: "Workspaces", value: data.total_workspaces, href: "/workspaces" },
    { label: "Groups", value: data.total_groups, href: "/workspaces" },
    { label: "Resources", value: data.total_resources, href: "/permissions" },
  ];

  return (
    <div>
      <PageHeader title="Dashboard" description="Users, workspaces, and recent sign-in activity." actions={
        <Button variant="ghost" size="sm" onClick={refresh} disabled={refreshing}>
          <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />{refreshing ? "Refreshing…" : "Refresh"}
        </Button>
      } />

      <div className="metric-strip">
        {metrics.map((metric) => (
          <Link key={metric.label} to={metric.href} className="group">
            <p className="text-xs text-muted-foreground">{metric.label}</p>
            <div className="mt-2 flex items-center gap-3">
              <span className="text-[1.75rem] font-medium tracking-tight tabular-nums">{metric.value.toLocaleString()}</span>
              <ArrowRight className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
            </div>
          </Link>
        ))}
      </div>

      <div className="overview-layout">
        <div className="overview-main">
          <section className="overview-section">
            <div className="section-heading">
              <div><h2 className="section-title">Sign-in activity</h2><p className="mt-1 text-xs text-muted-foreground">Successful and failed sign-ins</p></div>
              <span className="text-xs text-muted-foreground">Last 30 days</span>
            </div>
            {summaryQuery.isError ? <ErrorState message="Couldn’t load sign-in activity." onRetry={() => void summaryQuery.refetch()} /> : summary ? (
              <SignInsChart items={summary.items} days={summary.days} />
            ) : <div className="h-48 animate-pulse bg-muted" />}
          </section>

          <section className="overview-section">
            <div className="section-heading">
              <h2 className="section-title">Recent activity</h2>
              <Link to="/activity" className="text-link">Open activity log <ArrowRight className="size-3.5" /></Link>
            </div>
            {activityQuery.isError ? <ErrorState message="Couldn’t load recent activity." onRetry={() => void activityQuery.refetch()} /> : !activity ? (
              <div role="status" aria-label="Loading recent activity" className="h-32 animate-pulse bg-muted" />
            ) : activity.length === 0 ? <p className="py-6 text-sm text-muted-foreground">No activity recorded yet.</p> : (
              <div className="divide-y divide-border/60">{activity.map((entry) => <ActivityEntry key={entry.id} entry={entry} />)}</div>
            )}
          </section>

          <section className="overview-section">
            <div className="section-heading">
              <h2 className="section-title">Top workspaces</h2>
              <Link to="/workspaces" className="text-link">All workspaces <ArrowRight className="size-3.5" /></Link>
            </div>
            {data.top_workspaces.length ? <div className="divide-y divide-border/60">
              {data.top_workspaces.map((workspace, index) => (
                <Link key={workspace.id} to={`/workspaces/${workspace.id}`} className="group flex items-center gap-4 py-3">
                  <span className="w-5 font-mono text-[10px] text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
                  <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium group-hover:text-primary">{workspace.name}</p><p className="mt-0.5 truncate text-xs text-muted-foreground">{workspace.slug}</p></div>
                  <span className="text-xs text-muted-foreground tabular-nums">{workspace.member_count} members</span>
                  <ArrowRight className="size-3.5 text-muted-foreground" />
                </Link>
              ))}
            </div> : <p className="py-6 text-sm text-muted-foreground">No workspaces yet.</p>}
          </section>
        </div>

        <aside className="overview-rail" aria-label="Security and recent users">
          <section className="overview-section">
            {summaryQuery.isError ? <p className="text-sm text-muted-foreground">Security signals are unavailable.</p> : summary ? <SecuritySignals items={summary.items} /> : <div className="h-60 animate-pulse bg-muted" />}
          </section>
          {summary && !summaryQuery.isError && <section className="overview-section">
            <div className="section-heading"><h2 className="section-title">Activity mix</h2><span className="text-xs text-muted-foreground">30 days</span></div>
            <ActivityMixChart items={summary.items} days={summary.days} />
          </section>}
          <section className="overview-section">
            <div className="section-heading"><h2 className="section-title">Recent users</h2><Link to="/users" className="text-link">View all</Link></div>
            <div className="space-y-4">
              {data.recent_users.map((user) => (
                <Link key={user.id} to={`/users/${user.id}`} className="group block">
                  <div className="flex items-center justify-between gap-2"><p className="truncate text-sm font-medium group-hover:text-primary">{user.name}</p><StatusBadge active={user.is_active} /></div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">{user.email}</p>
                </Link>
              ))}
              {!data.recent_users.length && <p className="text-sm text-muted-foreground">No users yet.</p>}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function ActivityEntry({ entry }: { entry: ActivityLog }) {
  return (
    <div className="flex items-start gap-4 py-3">
      <span className="w-14 shrink-0 pt-0.5 font-mono text-[10px] text-muted-foreground" title={new Date(entry.created_at).toLocaleString()}>{timeAgo(entry.created_at)}</span>
      <div className="min-w-0 flex-1 text-sm">
        <p className="break-words"><span className="font-medium">{entry.actor_name ?? entry.actor_email ?? "System"}</span><span className="ml-2 text-muted-foreground">{entry.action.replace(/_/g, " ")}</span></p>
        {entry.detail && Object.entries(entry.detail).length > 0 && <p className="mt-1 break-words text-xs text-muted-foreground">{Object.entries(entry.detail).map(([key, value]) => `${key}: ${String(value)}`).join(" · ")}</p>}
      </div>
    </div>
  );
}

const SIGNALS = [
  { action: "login_impossible_travel", label: "Impossible travel", high: true },
  { action: "credential_stuffing_suspected", label: "Credential stuffing", high: true },
  { action: "login_new_country", label: "New countries", high: false },
  { action: "login_new_device", label: "New devices", high: false },
];

function SecuritySignals({ items }: { items: ActivityDailyCount[] }) {
  const counts = new Map<string, number>();
  for (const item of items) counts.set(item.action, (counts.get(item.action) ?? 0) + item.count);
  const highRisk = SIGNALS.filter((signal) => signal.high).reduce((sum, signal) => sum + (counts.get(signal.action) ?? 0), 0);
  const Icon = highRisk ? TriangleAlert : ShieldCheck;
  return (
    <>
      <div className="section-heading"><h2 className="section-title">Security signals</h2><span className="text-xs text-muted-foreground">30 days</span></div>
      <div className={`mb-4 flex items-start gap-2.5 border-l-2 py-1 pl-3 ${highRisk ? "border-destructive text-destructive" : "border-emerald-600 text-emerald-800 dark:text-emerald-400"}`}>
        <Icon className="mt-0.5 size-4 shrink-0" /><p className="text-xs leading-relaxed">{highRisk ? `${highRisk.toLocaleString()} high-risk events to review` : "No high-risk events recorded"}</p>
      </div>
      <div className="divide-y divide-border/60">
        {SIGNALS.map((signal) => <Link key={signal.action} to={`/activity?action=${signal.action}`} className="flex items-center justify-between gap-3 py-2.5 text-xs hover:text-primary">
          <span>{signal.label}</span><span className={`font-mono tabular-nums ${signal.high && counts.get(signal.action) ? "text-destructive" : "text-muted-foreground"}`}>{(counts.get(signal.action) ?? 0).toLocaleString()}</span>
        </Link>)}
      </div>
    </>
  );
}

function timeAgo(date: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(date).getTime()) / 60000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div role="alert" className="border-l-2 border-destructive/50 py-3 pl-4"><p className="text-sm font-medium">Unable to load data</p><p className="mt-1 text-xs text-muted-foreground">{message}</p><Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>Try again</Button></div>;
}
