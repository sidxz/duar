import { PageHeader } from "../components/PageHeader";
import { useQuery } from "@tanstack/react-query";
import { getActivityInsights } from "../api/client";
import { BarList } from "../components/charts";
import { WorldMap } from "../components/WorldMap";

function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 border-t border-border pt-5">
      <div className="section-heading">
        <h2 className="section-title">{title}</h2>
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
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div className="h-48 bg-muted rounded-lg" />
          <div className="h-48 bg-muted rounded-lg" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 [&>.page-header]:pb-0">
      <PageHeader title="Insights" description="Sign-in locations, browsers, and operating systems." />

      <Card title="Sign-in locations" hint="Last 30 days">
        {data.countries.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
            No sign-in locations available
            {data.unresolved > 0 && ` (${data.unresolved} without a known location)`}
          </div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-[2fr_1fr] gap-6 items-start">
            <WorldMap countries={data.countries} />
            <div>
              <BarList rows={data.countries.map((c) => [c.name, c.count])} labelWidth={140} />
              {data.unresolved > 0 && (
                <div className="text-xs text-muted-foreground mt-3 px-1">
                  {data.unresolved.toLocaleString()} sign-in{data.unresolved === 1 ? "" : "s"} from
                  IP addresses with no known location
                </div>
              )}
            </div>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
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
        Locations and device details come from the IP address and browser information recorded at sign-in.
        Client apps don’t send additional data for these charts.
      </p>
    </div>
  );
}
