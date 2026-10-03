"use client";

import { AuthzGuard } from "@duar-auth/nextjs";
import { RedirectToLogin } from "@/components/RedirectToLogin";
import { AppShell } from "@/components/AppShell";

export default function NotesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthzGuard
      fallback={<RedirectToLogin />}
      loading={
        <div className="flex h-screen items-center justify-center bg-zinc-950">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
        </div>
      }
    >
      <AppShell>{children}</AppShell>
    </AuthzGuard>
  );
}
