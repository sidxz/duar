import { useEffect, useState } from "react";
import { getAuthProviders } from "../api/client";
import { clientLog } from "../lib/logger";

const PROVIDER_META: Record<string, { label: string; icon: string }> = {
  google: {
    label: "Google",
    icon: "M12.545 10.239v3.821h5.445c-.712 2.315-2.647 3.972-5.445 3.972a6.033 6.033 0 110-12.064c1.498 0 2.866.549 3.921 1.453l2.814-2.814A9.969 9.969 0 0012.545 2C7.021 2 2.543 6.477 2.543 12s4.478 10 10.002 10c8.396 0 10.249-7.85 9.426-11.748l-9.426-.013z",
  },
  github: {
    label: "GitHub",
    icon: "M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z",
  },
  entra_id: {
    label: "Microsoft",
    icon: "M3 3h8v8H3V3zm10 0h8v8h-8V3zM3 13h8v8H3v-8zm10 0h8v8h-8v-8z",
  },
};

const KNOWN_ERRORS: Record<string, string> = {
  not_admin: "Your account does not have admin access.",
  email_not_verified:
    "Your identity provider did not confirm your email. Verify it and try again.",
  email_conflict:
    "An account with this email already exists under a different sign-in provider. Sign in with your original provider.",
  no_email_claim:
    "Your identity provider returned no email address. Add the 'email' optional claim to the application registration.",
};

function readLoginError(): string | null {
  const code = new URLSearchParams(window.location.search).get("error");
  return code ? (KNOWN_ERRORS[code] ?? null) : null;
}

export function Login() {
  const [providers, setProviders] = useState<string[]>([]);
  // Derived once at mount from the URL — no setState-in-effect.
  const [error] = useState(readLoginError);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get("error");
    if (code && KNOWN_ERRORS[code]) {
      clientLog("client.login.failed", "warning", { reason: code });
      window.history.replaceState({}, "", "/login");
    }
    getAuthProviders()
      .then((r) => setProviders(r.providers))
      .catch(() => {});
  }, []);

  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-border bg-card overflow-hidden">
        <img src="/splash.png" alt="Duar" className="w-full object-cover" />
        <div className="px-8 pb-8 space-y-6">
          <div className="text-center">
            <p className="mt-1 text-sm text-muted-foreground">Sign in to access the admin panel</p>
          </div>

          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-700 dark:text-red-300">
              {error}
            </div>
          )}

          <div className="space-y-3">
            {providers.map((p) => {
              const meta = PROVIDER_META[p] ?? { label: p, icon: "" };
              return (
                <a
                  key={p}
                  href={`${import.meta.env.VITE_API_URL || "http://localhost:9003"}/auth/admin/login/${p}`}
                  className="flex w-full items-center justify-center gap-2.5 rounded-md border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-muted"
                >
                  {meta.icon && (
                    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="currentColor">
                      <path d={meta.icon} />
                    </svg>
                  )}
                  Continue with {meta.label}
                </a>
              );
            })}
            {providers.length === 0 && (
              <p className="text-center text-sm text-muted-foreground">
                No OAuth providers configured. Check your .env file.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
