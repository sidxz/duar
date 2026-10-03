import { useQuery } from "@tanstack/react-query";
import { exportNotes } from "../api/notes";

export function Export() {
  const {
    data,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["export"],
    queryFn: exportNotes,
  });

  return (
    <div>
      <h1 className="mb-2 text-xl font-bold text-zinc-100">Export Notes</h1>
      <p className="mb-6 text-sm text-zinc-500">
        This page requires the <code>notes:export</code> RBAC action, enforced
        via <code>require_action(role_client, "notes:export")</code>.
      </p>

      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
        </div>
      ) : error ? (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
          <p className="mb-1 text-sm font-medium text-red-400">Access Denied</p>
          <p className="text-xs text-zinc-500">
            {error instanceof Error ? error.message : "Export failed"}.
            You need the <code>notes:export</code> action assigned to your role
            via the admin panel.
          </p>
        </div>
      ) : data ? (
        <div>
          <div className="mb-4 flex items-center gap-4 text-sm text-zinc-400">
            <span>Format: {data.format.toUpperCase()}</span>
            <span>&middot;</span>
            <span>{data.count} note(s)</span>
          </div>
          <pre className="max-h-96 overflow-auto rounded-lg border border-zinc-800 bg-zinc-900 p-4 text-xs text-zinc-300">
            {JSON.stringify(data.notes, null, 2)}
          </pre>
        </div>
      ) : null}

      <div className="mt-6 rounded border border-zinc-800 bg-zinc-900/50 p-3 text-xs text-zinc-500">
        <strong>How this works:</strong> On startup, the demo backend registers{" "}
        <code>notes:export</code> as a service action via{" "}
        <code>role_client.register_actions()</code>. An admin creates a role with
        this action and assigns it to users. The SDK's{" "}
        <code>require_action()</code> dependency checks the identity service
        before allowing access.
      </div>
    </div>
  );
}
