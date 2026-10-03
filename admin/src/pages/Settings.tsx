import { useQuery } from "@tanstack/react-query";
import { getSystemSettings } from "../api/client";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

export function Settings() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["system-settings"],
    queryFn: getSystemSettings,
  });

  if (isLoading) return <Skeleton className="h-64 rounded-lg" />;
  if (error)
    return (
      <div className="text-sm text-red-700 dark:text-red-400">
        Failed to load: {(error as Error).message}
      </div>
    );
  if (!data) return null;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Settings</h1>

      {/* Service */}
      <Section title="Service">
        <KV label="Base URL" value={data.service.base_url} mono />
        <KV label="Frontend URL" value={data.service.frontend_url} mono />
        <KV label="Admin URL" value={data.service.admin_url} mono />
      </Section>

      {/* OAuth Providers */}
      <Section title="OAuth Providers">
        {data.oauth_providers.map((p) => (
          <KV
            key={p.name}
            label={p.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
            value={p.configured ? "Configured" : "Not configured"}
            valueClass={
              p.configured ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground"
            }
          />
        ))}
      </Section>

      {/* JWT */}
      <Section title="JWT">
        <KV label="Algorithm" value={data.jwt.algorithm} />
        <KV label="Access Token TTL" value={`${data.jwt.access_token_expire_minutes} minutes`} />
        <KV label="Refresh Token TTL" value={`${data.jwt.refresh_token_expire_days} days`} />
        <KV label="Denylist Entries" value={String(data.jwt.denylist_count)} />
        {data.jwt.public_key_preview && (
          <div className="px-4 py-2.5 flex gap-3">
            <span className="text-xs text-muted-foreground w-40 shrink-0">Public Key</span>
            <span className="text-xs text-muted-foreground font-mono truncate">
              {data.jwt.public_key_preview}
            </span>
          </div>
        )}
      </Section>

      {/* Security */}
      <Section title="Security">
        <KV
          label="Cookie Secure"
          value={data.security.cookie_secure ? "Enabled" : "Disabled"}
          valueClass={
            data.security.cookie_secure
              ? "text-emerald-700 dark:text-emerald-400"
              : "text-amber-700 dark:text-amber-400"
          }
        />
        <KV
          label="Session Secret"
          value={data.security.session_secret_configured ? "Configured" : "Using default (insecure)"}
          valueClass={
            data.security.session_secret_configured
              ? "text-emerald-700 dark:text-emerald-400"
              : "text-red-700 dark:text-red-400"
          }
        />
        <KV label="Allowed Hosts" value={data.security.allowed_hosts.join(", ")} mono />
        <KV label="CORS Origins" value={data.security.cors_origins.join(", ")} mono />
        <KV label="Admin Emails" value={data.security.admin_emails.join(", ") || "None"} mono />
      </Section>

      {/* Rate Limits */}
      <Section title="Rate Limits">
        {data.rate_limits.map((rl) => (
          <KV key={rl.endpoint} label={rl.endpoint} value={rl.limit} />
        ))}
      </Section>

      {/* Service Keys */}
      <Section title="Service API Keys">
        {data.service_keys.length === 0 ? (
          <div className="px-4 py-3 text-xs text-muted-foreground">No service keys configured</div>
        ) : (
          data.service_keys.map((sk) => (
            <KV key={sk.name} label={sk.name} value={sk.preview} mono />
          ))
        )}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 className="text-sm font-medium text-muted-foreground mb-2">{title}</h2>
      <div className="rounded-lg border border-border bg-card divide-y divide-border">
        {children}
      </div>
    </div>
  );
}

function KV({
  label,
  value,
  valueClass,
  mono,
}: {
  label: string;
  value: string;
  valueClass?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center px-4 py-2.5">
      <span className="text-xs text-muted-foreground w-40 shrink-0">{label}</span>
      <span
        className={cn("text-xs truncate", mono && "font-mono", valueClass ?? "text-foreground")}
      >
        {value}
      </span>
    </div>
  );
}
