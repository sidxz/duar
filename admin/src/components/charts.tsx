import { useMemo } from "react";
import { TriangleAlert } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ActivityDailyCount } from "../types/api";

const SIGN_IN_ACTIONS = new Set(["user_login", "admin_login"]);
const FAILURE_ACTIONS = new Set(["login_failed", "admin_login_failed"]);

function lastNDays(n: number): string[] {
  const out: string[] = [];
  const now = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setUTCDate(now.getUTCDate() - i);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function fmtDay(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

const AXIS_TICK = { fontSize: 10, fill: "var(--muted-foreground)" } as const;

interface SignInTipProps {
  active?: boolean;
  label?: string;
  payload?: { dataKey?: string | number; value?: number }[];
}

function StackTooltip({
  active,
  label,
  payload,
  okLabel,
  failLabel,
}: SignInTipProps & { okLabel: string; failLabel: string }) {
  if (!active || !payload?.length) return null;
  const get = (key: string) => payload.find((p) => p.dataKey === key)?.value ?? 0;
  return (
    <div className="bg-popover text-popover-foreground border border-border rounded-md shadow-md px-2.5 py-1.5 text-xs whitespace-nowrap">
      <div className="text-muted-foreground mb-0.5">{label ? fmtDay(label) : ""}</div>
      <div className="flex items-center gap-1.5">
        <span className="w-2.5 h-0.5 rounded bg-chart-series" />
        <span className="font-semibold">{get("ok")}</span>
        <span className="text-muted-foreground">{okLabel}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="w-2.5 h-0.5 rounded bg-chart-critical" />
        <span className="font-semibold">{get("fail")}</span>
        <span className="text-muted-foreground">{failLabel}</span>
      </div>
    </div>
  );
}

/** Stacked daily bar chart (ok series + fail series) with legend, empty state,
 *  and an sr-only table carrying every value for non-pointer access. */
export function DailyStackChart({
  data,
  days,
  okLabel,
  failLabel,
  emptyText,
}: {
  data: { day: string; ok: number; fail: number }[];
  days: number;
  okLabel: string;
  failLabel: string;
  emptyText: string;
}) {
  const empty = data.every((d) => d.ok === 0 && d.fail === 0);

  return (
    <div>
      <div className="flex items-center gap-4 mb-2 justify-end text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-[2px] bg-chart-series" />
          {okLabel}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-[2px] bg-chart-critical" />
          <TriangleAlert className="w-3 h-3 text-chart-critical" />
          {failLabel}
        </span>
      </div>

      {empty ? (
        <div className="h-36 flex items-center justify-center text-sm text-muted-foreground">
          {emptyText}
        </div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={170}>
            <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -20 }} barCategoryGap="25%">
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="day"
                tickFormatter={fmtDay}
                interval={Math.max(1, Math.floor(days / 5))}
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={{ stroke: "var(--border)" }}
              />
              <YAxis allowDecimals={false} tick={AXIS_TICK} tickLine={false} axisLine={false} />
              <Tooltip
                content={<StackTooltip okLabel={okLabel.toLowerCase()} failLabel={failLabel.toLowerCase()} />}
                cursor={{ fill: "var(--muted)", opacity: 0.6 }}
                isAnimationActive={false}
              />
              {/* card-colored stroke = the surface gap between touching segments */}
              <Bar dataKey="fail" stackId="a" fill="var(--chart-critical)" stroke="var(--card)" strokeWidth={1} maxBarSize={24} />
              <Bar dataKey="ok" stackId="a" fill="var(--chart-series)" stroke="var(--card)" strokeWidth={1} radius={[3, 3, 0, 0]} maxBarSize={24} />
            </BarChart>
          </ResponsiveContainer>

          {/* table fallback: every value reachable without hover */}
          <table className="sr-only">
            <caption>{`Daily ${okLabel.toLowerCase()} and ${failLabel.toLowerCase()}, last ${days} days`}</caption>
            <thead>
              <tr><th>Day</th><th>{okLabel}</th><th>{failLabel}</th></tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.day}><td>{d.day}</td><td>{d.ok}</td><td>{d.fail}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

/** Daily sign-in volume (blue) with failed attempts (status-critical) stacked
 *  at the baseline. Delegates to DailyStackChart. */
export function SignInsChart({ items, days = 30 }: { items: ActivityDailyCount[]; days?: number }) {
  const data = useMemo(() => {
    const byDay = new Map<string, { ok: number; fail: number }>();
    for (const d of lastNDays(days)) byDay.set(d, { ok: 0, fail: 0 });
    for (const it of items) {
      const b = byDay.get(it.day);
      if (!b) continue;
      if (SIGN_IN_ACTIONS.has(it.action)) b.ok += it.count;
      else if (FAILURE_ACTIONS.has(it.action)) b.fail += it.count;
    }
    return [...byDay.entries()].map(([day, v]) => ({ day, ...v }));
  }, [items, days]);

  return (
    <DailyStackChart
      data={data}
      days={days}
      okLabel="Sign-ins"
      failLabel="Failed"
      emptyText={`No sign-in activity in the last ${days} days`}
    />
  );
}

/** Nominal single-series horizontal bars: one hue, value labeled at the tip. */
export function BarList({ rows, labelWidth = 130 }: { rows: [string, number][]; labelWidth?: number }) {
  const data = rows.map(([name, count]) => ({ name, count }));
  return (
    <ResponsiveContainer width="100%" height={rows.length * 28 + 8}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 44, bottom: 0, left: 8 }}>
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="name"
          width={labelWidth}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
        />
        <Bar dataKey="count" fill="var(--chart-series)" barSize={12} radius={[0, 3, 3, 0]} isAnimationActive={false}>
          <LabelList
            dataKey="count"
            position="right"
            formatter={(v) => Number(v).toLocaleString()}
            style={{ fontSize: 11, fill: "var(--foreground)" }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

const MIX_BUCKETS: [string, RegExp][] = [
  ["Sign-ins", /^(user_login|admin_login)$/],
  ["Auth anomalies", /^(login_failed|admin_login_failed|refresh_context_changed|refresh_reuse_detected|tokens_revoked|login_impossible_travel|login_new_country|login_new_device|credential_stuffing_suspected)$/],
  ["Users & members", /^(user_|member_|invitation_|batch_import|bulk_status_change|export_users)/],
  ["Groups & roles", /^(group_|role_|service_action_)/],
  ["Apps & realms", /^(client_app_|service_app_|realm_)/],
  ["Permissions", /^permission/],
  ["Workspaces & orgs", /^(workspace_|org_|self_serve_|export_workspaces)/],
];

/** Event volume by category — nominal bars, one series, one hue. */
export function ActivityMixChart({ items, days = 30 }: { items: ActivityDailyCount[]; days?: number }) {
  const rows = useMemo(() => {
    const totals = new Map<string, number>(MIX_BUCKETS.map(([name]) => [name, 0]));
    totals.set("Other", 0);
    for (const it of items) {
      const bucket = MIX_BUCKETS.find(([, re]) => re.test(it.action))?.[0] ?? "Other";
      totals.set(bucket, (totals.get(bucket) ?? 0) + it.count);
    }
    return [...totals.entries()]
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1]) as [string, number][];
  }, [items]);

  if (rows.length === 0) {
    return (
      <div className="h-36 flex items-center justify-center text-sm text-muted-foreground">
        No activity in the last {days} days
      </div>
    );
  }

  return <BarList rows={rows} />;
}

/** Allowed vs denied action checks per day. Fills missing days with zeros. */
export function ActionsTrendChart({
  items,
  days,
}: {
  items: { day: string; allowed: number; denied: number }[];
  days: number;
}) {
  const data = useMemo(() => {
    const byDay = new Map(lastNDays(days).map((d) => [d, { ok: 0, fail: 0 }]));
    for (const it of items) {
      const b = byDay.get(it.day);
      if (b) {
        b.ok = it.allowed;
        b.fail = it.denied;
      }
    }
    return [...byDay.entries()].map(([day, v]) => ({ day, ...v }));
  }, [items, days]);
  return (
    <DailyStackChart
      data={data}
      days={days}
      okLabel="Allowed"
      failLabel="Denied"
      emptyText={`No action checks in the last ${days} days`}
    />
  );
}
