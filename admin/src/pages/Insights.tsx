import { useQuery } from "@tanstack/react-query";
import { getActivityInsights } from "../api/client";
import { BarList } from "../components/charts";
import { WorldMap } from "../components/WorldMap";

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

export function Insights() {
  const { data, isLoading } = useQuery({
    queryKey: ["insights"],
    queryFn: () => getActivityInsights(30),
  });

  if (isLoading || !data) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-6 w-32 bg-muted rounded" />
        <div className="h-80 bg-muted rounded-lg" />
        <div className="grid grid-cols-2 gap-6">
          <div className="h-48 bg-muted rounded-lg" />
          <div className="h-48 bg-muted rounded-lg" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Insights</h1>

      <Card title="Sign-in locations" hint="Last 30 days">
        {data.countries.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
            No sign-ins from resolvable public addresses yet
            {data.unresolved > 0 && ` (${data.unresolved} from private/local addresses)`}
          </div>
        ) : (
          <div className="grid grid-cols-[2fr_1fr] gap-6 items-start">
            <WorldMap countries={data.countries} />
            <div>
              <BarList rows={data.countries.map((c) => [c.name, c.count])} labelWidth={140} />
              {data.unresolved > 0 && (
                <div className="text-xs text-muted-foreground mt-3 px-1">
                  {data.unresolved.toLocaleString()} sign-in{data.unresolved === 1 ? "" : "s"} from
                  private or unresolvable addresses
                </div>
              )}
            </div>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-6">
        <Card title="Browsers" hint="Last 30 days">
          {data.browsers.length === 0 ? (
            <div className="h-32 flex items-center justify-center text-sm text-muted-foreground">No sign-ins yet</div>
          ) : (
            <BarList rows={data.browsers.map((b) => [b.name, b.count])} />
          )}
        </Card>
        <Card title="Operating systems" hint="Last 30 days">
          {data.os.length === 0 ? (
            <div className="h-32 flex items-center justify-center text-sm text-muted-foreground">No sign-ins yet</div>
          ) : (
            <BarList rows={data.os.map((o) => [o.name, o.count])} />
          )}
        </Card>
      </div>

      <p className="text-xs text-muted-foreground">
        Derived from the IP address and user-agent captured at sign-in. No data is collected from
        client applications.
      </p>
    </div>
  );
}
