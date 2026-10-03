import { useQuery } from "@tanstack/react-query";
import { getSystemHealth } from "../api/client";

export function SystemHealth() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["system-health"],
    queryFn: getSystemHealth,
    refetchInterval: 30000,
  });

  if (isLoading) return <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />;
  if (error)
    return (
      <div className="text-sm text-red-700 dark:text-red-400">
        Failed to load: {(error as Error).message}
      </div>
    );
  if (!data) return null;

  const formatUptime = (seconds: number) => {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  };

  const healthy = data.status === "healthy";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-foreground">System Health</h1>
        <div
          className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-medium ring-1 ${
            healthy
              ? "bg-emerald-500/10 text-emerald-700 ring-emerald-500/20 dark:text-emerald-400"
              : "bg-amber-500/10 text-amber-700 ring-amber-500/20 dark:text-amber-400"
          }`}
        >
          <div className={`w-2 h-2 rounded-full ${healthy ? "bg-emerald-500" : "bg-amber-500"}`} />
          <span>{data.status.charAt(0).toUpperCase() + data.status.slice(1)}</span>
        </div>
      </div>

      {/* Overview */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-5">
          <div className="text-xs text-muted-foreground mb-1">Uptime</div>
          <div className="text-lg font-semibold font-mono tabular-nums text-foreground">
            {formatUptime(data.uptime_seconds)}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-5">
          <div className="text-xs text-muted-foreground mb-1">Version</div>
          <div className="text-lg font-semibold font-mono text-foreground">{data.version}</div>
        </div>
      </div>

      {/* Dependency checks */}
      <div>
        <h2 className="text-sm font-medium text-muted-foreground mb-3">Dependencies</h2>
        <div className="grid grid-cols-2 gap-4">
          {Object.entries(data.checks).map(([name, check]) => {
            const ok = check.status === "ok";
            return (
              <div key={name} className="rounded-lg border border-border bg-card p-5">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-medium capitalize text-foreground">{name}</span>
                  <div
                    className={`flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${
                      ok
                        ? "bg-emerald-500/10 text-emerald-700 ring-emerald-500/20 dark:text-emerald-400"
                        : "bg-red-500/10 text-red-700 ring-red-500/20 dark:text-red-400"
                    }`}
                  >
                    <div className={`w-2 h-2 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`} />
                    <span>{ok ? "Connected" : "Error"}</span>
                  </div>
                </div>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs">
                    <span className="text-muted-foreground">Latency</span>
                    <span className="text-foreground font-mono tabular-nums">{check.latency_ms}ms</span>
                  </div>
                  {check.error && (
                    <div className="text-xs text-red-700 dark:text-red-400 mt-2 break-all">
                      {check.error}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="text-xs text-muted-foreground">Auto-refreshes every 30 seconds</div>
    </div>
  );
}
