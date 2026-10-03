import { useAuth } from "@duar-auth/react";

export function Login() {
  const { login } = useAuth();

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      <div className="w-full max-w-sm space-y-6 text-center">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Team Notes</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Duar SDK Demo
          </p>
        </div>

        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-6">
          <p className="mb-4 text-sm text-zinc-400">
            Sign in to manage workspace notes. This app demonstrates
            authentication, workspace roles, RBAC actions, and entity-level
            permissions.
          </p>
          <button
            onClick={() => login("google")}
            className="w-full rounded bg-white px-4 py-2.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 transition"
          >
            Sign in with Google
          </button>
        </div>

        <div className="space-y-2 text-xs text-zinc-600">
            <p>
            Powered by <a href="https://docs.duar.io/" target="_blank" rel="noopener noreferrer" className="text-zinc-500 hover:text-zinc-400 underline">Duar</a>
            </p>
          <div className="flex justify-center gap-4">
            
          </div>
        </div>
      </div>
    </div>
  );
}
