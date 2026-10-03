import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { deleteServiceAction, getServiceActions } from "../api/client";
import { ConfirmModal } from "../components/ConfirmModal";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import type { ServiceAction } from "../types/api";

export function ServiceActions() {
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<ServiceAction | null>(null);

  const { data: actions = [], isLoading, isError, error } = useQuery({
    queryKey: ["service-actions"],
    queryFn: () => getServiceActions(),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteServiceAction(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["service-actions"] });
      setDeleteTarget(null);
      toast.success("Action deleted");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  if (isLoading) return <div className="animate-pulse h-64 bg-muted/50 rounded-lg" />;

  if (isError) {
    return (
      <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-6 text-center">
        <div className="text-sm text-red-600 dark:text-red-400">Failed to load service actions</div>
        <div className="text-xs text-red-500/70 mt-1">{(error as Error).message}</div>
      </div>
    );
  }

  // Group by service_name
  const grouped = actions.reduce<Record<string, ServiceAction[]>>((acc, a) => {
    (acc[a.service_name] ??= []).push(a);
    return acc;
  }, {});

  const serviceNames = Object.keys(grouped).sort();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Service Actions</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Actions registered by services for RBAC. {actions.length} total across {serviceNames.length} service{serviceNames.length !== 1 ? "s" : ""}.
        </p>
      </div>

      {serviceNames.length === 0 && (
        <div className="rounded-lg border border-dashed border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
          No service actions registered yet
        </div>
      )}

      {serviceNames.map((svc) => (
        <div key={svc} className="rounded-lg border border-border bg-card">
          <div className="px-4 py-3 border-b border-border">
            <h2 className="text-sm font-medium font-mono text-foreground">{svc}</h2>
            <div className="text-xs text-muted-foreground">{grouped[svc].length} actions</div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground border-b border-border">
                <th className="px-4 py-2 font-medium">Action</th>
                <th className="px-4 py-2 font-medium">Description</th>
                <th className="px-4 py-2 font-medium">Registered</th>
                <th className="px-4 py-2 font-medium w-16" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {grouped[svc].map((a) => (
                <tr key={a.id} className="hover:bg-muted/50 group">
                  <td className="px-4 py-2 font-mono text-xs text-foreground">{a.action}</td>
                  <td className="px-4 py-2 text-muted-foreground">{a.description || "-"}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {new Date(a.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDeleteTarget(a)}
                      className="text-destructive hover:text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && remove.mutate(deleteTarget.id)}
        title="Delete Service Action"
        message={`Delete "${deleteTarget?.action}" from ${deleteTarget?.service_name}? This will also remove it from any roles using it.`}
        confirmLabel="Delete"
        danger
        isPending={remove.isPending}
      />
    </div>
  );
}
