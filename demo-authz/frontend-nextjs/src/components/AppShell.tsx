"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuthz, useAuthzUser } from "@duar-auth/nextjs";
import { RoleBadge } from "./RoleBadge";

const navLinks = [
  { href: "/notes", label: "Notes" },
  { href: "/notes/export", label: "Export" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const user = useAuthzUser();
  const { logout } = useAuthz();
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-zinc-950">
      <nav className="border-b border-zinc-800 bg-zinc-900/50 px-6 py-3">
        <div className="mx-auto flex max-w-4xl items-center justify-between">
          <div className="flex items-center gap-6">
            <Link href="/notes" className="text-lg font-semibold text-zinc-100">
              Team Notes
            </Link>
            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
              authz · next.js
            </span>
            <div className="flex gap-1">
              {navLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`rounded px-3 py-1.5 text-sm transition ${
                    pathname === link.href
                      ? "bg-zinc-700 text-zinc-100"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {link.label}
                </Link>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-sm text-zinc-300">{user.name}</div>
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                <span>{user.workspaceSlug}</span>
                <RoleBadge role={user.workspaceRole} />
              </div>
            </div>
            <button
              onClick={() => {
                logout();
                window.location.href = "/";
              }}
              className="rounded bg-zinc-800 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200"
            >
              Logout
            </button>
          </div>
        </div>
      </nav>
      <main className="mx-auto max-w-4xl px-6 py-8">{children}</main>
    </div>
  );
}
