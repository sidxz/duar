import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { getSystemHealth } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { Button } from "../components/ui/button";

export function SystemHealth() {
  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["system-health"], queryFn: getSystemHealth, refetchInterval: 30000,
  });
  if (isLoading) return <div className="h-64 animate-pulse bg-muted" />;
  if (error) return <div role="alert" className="space-y-3"><p className="text-sm text-destructive">Failed to load: {error.message}</p><Button variant="outline" size="sm" onClick={() => void refetch()}>Try again</Button></div>;
  if (!data) return null;

  const days = Math.floor(data.uptime_seconds / 86400);
  const hours = Math.floor(data.uptime_seconds % 86400 / 3600);
  const minutes = Math.floor(data.uptime_seconds % 3600 / 60);
  const uptime = `${days ? `${days}d ` : ""}${hours ? `${hours}h ` : ""}${minutes}m`;
  const healthy = data.status === "healthy";

  return (
    <div className="max-w-5xl">
      <PageHeader title="System Health" description="Service availability and dependency checks." actions={<Button variant="ghost" size="sm" onClick={() => void refetch()} disabled={isFetching}><RefreshCw className={`size-3.5 ${isFetching ? "animate-spin" : ""}`} />Refresh</Button>} />
      <div className="flex flex-wrap items-center justify-between gap-6 border-y border-border py-6">
        <div className="flex items-center gap-3">
          <span className={`size-2.5 rounded-full ${healthy ? "bg-emerald-600" : "bg-amber-500"}`} />
          <div><p className="text-lg font-medium capitalize">{data.status}</p><p className="mt-1 text-xs text-muted-foreground">{healthy ? "All dependency checks are passing." : "Review the dependency checks below."}</p></div>
        </div>
        <dl className="flex gap-8 text-sm"><div><dt className="text-xs text-muted-foreground">Uptime</dt><dd className="mt-1 font-mono">{uptime}</dd></div><div><dt className="text-xs text-muted-foreground">Version</dt><dd className="mt-1 font-mono">{data.version}</dd></div></dl>
      </div>
      <section className="record-section">
        <div><h2 className="section-title">Dependencies</h2><p className="mt-2 text-xs leading-relaxed text-muted-foreground">Connection status and response time for each service.</p></div>
        <div className="min-w-0 divide-y divide-border">
          {Object.entries(data.checks).map(([name, check]) => (
            <div key={name} className="py-4 first:pt-0">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-sm font-medium capitalize">{name}</h3>
                <div className="flex items-center gap-6 text-xs"><span className={check.status === "ok" ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}>{check.status === "ok" ? "Connected" : "Error"}</span><span className="w-16 text-right font-mono tabular-nums text-muted-foreground">{check.latency_ms} ms</span></div>
              </div>
              {check.error && <p className="mt-2 break-words text-xs text-destructive">{check.error}</p>}
            </div>
          ))}
        </div>
      </section>
      <p className="mt-5 text-xs text-muted-foreground">Updated {new Date(dataUpdatedAt).toLocaleTimeString()} · Refreshes every 30 seconds</p>
    </div>
  );
}
