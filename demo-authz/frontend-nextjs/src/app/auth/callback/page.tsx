"use client";

import { useRouter } from "next/navigation";
import { AuthzCallback } from "@duar-auth/nextjs";
import { RoleBadge } from "@/components/RoleBadge";

export default function AuthCallbackPage() {
  const router = useRouter();

  return (
    <AuthzCallback
      onSuccess={() => router.replace("/notes")}
      loadingComponent={
        <div className="flex h-screen items-center justify-center bg-zinc-950">
          <div className="text-center">
            <div className="mx-auto mb-3 h-6 w-6 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
            <p className="text-sm text-zinc-500">Signing you in...</p>
          </div>
        </div>
      }
      errorComponent={(error) => (
        <div className="flex h-screen items-center justify-center bg-zinc-950">
          <div className="max-w-sm text-center">
            <p className="mb-4 text-sm text-red-400">{error.message}</p>
            <a
              href="/"
              className="text-sm text-zinc-400 underline hover:text-zinc-200"
            >
              Back to login
            </a>
          </div>
        </div>
      )}
      workspaceSelector={({ workspaces, onSelect, isLoading }) => (
        <div className="flex h-screen items-center justify-center bg-zinc-950">
          <div className="w-full max-w-sm">
            <h2 className="mb-4 text-center text-lg font-semibold text-zinc-100">
              Select Workspace
            </h2>
            <div className="space-y-2">
              {workspaces.map((ws) => (
                <button
                  key={ws.id}
                  onClick={() => onSelect(ws.id)}
                  disabled={isLoading}
                  className="w-full rounded-lg border border-zinc-800 bg-zinc-900 p-4 text-left transition hover:border-zinc-700 disabled:opacity-50"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-medium text-zinc-100">{ws.name}</div>
                      <div className="text-xs text-zinc-500">{ws.slug}</div>
                    </div>
                    <RoleBadge role={ws.role} />
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    />
  );
}
