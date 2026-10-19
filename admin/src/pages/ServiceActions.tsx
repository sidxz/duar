import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, SearchX, Trash2, Zap } from "lucide-react";
import { toast } from "sonner";
import { deleteServiceAction, getServiceActions } from "../api/client";
import { ConfirmModal } from "../components/ConfirmModal";
import { PageHeader } from "../components/PageHeader";
import { SearchInput } from "../components/SearchInput";
import { Button } from "@/components/ui/button";
import type { ServiceAction } from "../types/api";

const PAGE_SIZE = 10;
const EMPTY_ACTIONS: ServiceAction[] = [];

export function ServiceActions() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const service = params.get("service") ?? "";
  const search = params.get("q") ?? "";
  const requestedPage = Number(params.get("page") ?? 1);
  const [serviceSearch, setServiceSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ServiceAction | null>(null);

  const { data: actions = EMPTY_ACTIONS, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["service-actions"],
    queryFn: () => getServiceActions(),
  });

  const services = useMemo(() => {
    const counts = new Map<string, number>();
    for (const action of actions) counts.set(action.service_name, (counts.get(action.service_name) ?? 0) + 1);
    return [...counts].sort(([a], [b]) => a.localeCompare(b));
  }, [actions]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return actions.filter((action) =>
      (!service || action.service_name === service)
      && (!query || [action.action, action.description ?? "", action.service_name].some((value) => value.toLowerCase().includes(query))),
    ).sort((a, b) => a.service_name.localeCompare(b.service_name) || a.action.localeCompare(b.action) || a.id.localeCompare(b.id));
  }, [actions, search, service]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  // A deletion or a stale bookmarked page can reduce the number of pages.
  const page = Math.min(pageCount, Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1);
  const visibleActions = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const visibleServices = services.filter(([name]) => name.toLowerCase().includes(serviceSearch.trim().toLowerCase()));

  function changeFilter(key: "service" | "q", value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value); else next.delete(key);
      next.delete("page");
      return next;
    }, { replace: key === "q" });
  }

  function changePage(nextPage: number) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (nextPage > 1) next.set("page", String(nextPage)); else next.delete("page");
      return next;
    });
  }

  function clearFilters() {
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const key of ["service", "q", "page"]) next.delete(key);
      return next;
    });
    setServiceSearch("");
  }

  const remove = useMutation({
    mutationFn: (id: string) => deleteServiceAction(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["service-actions"] });
      setDeleteTarget(null);
      toast.success("Action deleted");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  return (
    <div>
      <PageHeader title="Service Actions" />

      {isLoading ? (
        <div aria-label="Loading service actions" className="h-64 animate-pulse rounded-lg bg-muted/50" />
      ) : isError ? (
        <div role="alert" className="space-y-3 rounded-lg border border-border px-6 py-10 text-center">
          <p className="text-sm font-medium">Unable to load service actions</p>
          <p className="text-xs text-muted-foreground">{error.message}</p>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>Try again</Button>
        </div>
      ) : actions.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-border py-16 text-center">
          <Zap className="size-6 text-muted-foreground" />
          <h2 className="text-sm font-medium">No service actions registered</h2>
          <p className="max-w-sm px-4 text-sm text-muted-foreground">Services register the actions you can assign to workspace roles.</p>
        </div>
      ) : (
        <div className="action-registry">
          <aside aria-label="Filter actions by service" className="action-registry-services">
            <div className="mb-3 flex items-baseline justify-between gap-2">
              <h2 className="section-title">Services</h2>
              <span className="font-mono text-xs text-muted-foreground">{services.length}</span>
            </div>
            <SearchInput value={serviceSearch} onChange={setServiceSearch} placeholder="Find a service…" className="sm:w-full" />
            <nav aria-label="Service filters" className="mt-3">
              <button type="button" aria-pressed={!service} onClick={() => changeFilter("service", "")} className="action-service-filter">
                <span>All services</span><span className="font-mono text-xs tabular-nums">{actions.length}</span>
              </button>
              <div className="action-service-list">
                {visibleServices.map(([name, count]) => (
                  <button key={name} type="button" aria-pressed={service === name} onClick={() => changeFilter("service", name)} className="action-service-filter">
                    <span className="truncate" title={name}>{name}</span><span className="shrink-0 font-mono text-xs tabular-nums">{count}</span>
                  </button>
                ))}
                {visibleServices.length === 0 && <p role="status" className="px-3 py-4 text-xs text-muted-foreground">No services match “{serviceSearch}”.</p>}
              </div>
            </nav>
          </aside>

          <section aria-label="Action registry" className="min-w-0">
            <div className="action-registry-toolbar">
              <h2 className="hidden min-w-0 break-all text-sm font-medium xl:block">{service || "All actions"}</h2>
              <select aria-label="Filter by service" value={service} onChange={(event) => changeFilter("service", event.target.value)} className="h-10 min-w-0 rounded-md border border-input bg-card px-3 text-sm xl:hidden">
                <option value="">All services ({actions.length})</option>
                {service && !services.some(([name]) => name === service) && <option value={service}>{service} (0)</option>}
                {services.map(([name, count]) => <option key={name} value={name}>{name} ({count})</option>)}
              </select>
              <SearchInput value={search} onChange={(value) => changeFilter("q", value)} placeholder="Search actions, descriptions, services…" className="sm:w-80 xl:ml-auto" />
            </div>

            {visibleActions.length > 0 ? (
              <div className="table-surface">
                <table className="admin-table action-registry-table">
                  <caption className="sr-only">{service ? `Actions registered by ${service}` : "Actions across all services"}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Action</th>
                      {!service && <th scope="col" className="hidden w-36 md:table-cell">Service</th>}
                      <th scope="col" className="hidden w-28 sm:table-cell">Registered</th>
                      <th scope="col" className="w-12"><span className="sr-only">Manage action</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleActions.map((action) => (
                      <tr key={action.id}>
                        <td>
                          {!service && <p className="mb-1 text-xs text-muted-foreground md:hidden">{action.service_name}</p>}
                          <p className="break-all font-mono text-xs font-medium">{action.action}</p>
                          {action.description && <p className="mt-1 text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{action.description}</p>}
                          <p className="mt-1 text-xs text-muted-foreground sm:hidden">Registered {new Date(action.created_at).toLocaleDateString()}</p>
                        </td>
                        {!service && <td className="hidden text-xs text-muted-foreground [overflow-wrap:anywhere] md:table-cell">{action.service_name}</td>}
                        <td className="hidden text-xs tabular-nums text-muted-foreground sm:table-cell"><time dateTime={action.created_at}>{new Date(action.created_at).toLocaleDateString()}</time></td>
                        <td>
                          <Button variant="ghost" size="icon" aria-label={`Delete ${action.action} from ${action.service_name}`} title="Delete action" onClick={() => setDeleteTarget(action)} className="size-8 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
                            <Trash2 className="size-3.5" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="table-surface flex flex-col items-center gap-3 px-6 py-14 text-center">
                <SearchX className="size-6 text-muted-foreground" />
                <h3 className="text-sm font-medium">No matching actions</h3>
                <p className="text-sm text-muted-foreground">Try another search or choose a different service.</p>
                <Button variant="outline" size="sm" onClick={clearFilters}>Clear filters</Button>
              </div>
            )}

            <div className="collection-footer">
              <p role="status">{filtered.length > 0 ? `${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, filtered.length)} of ${filtered.length.toLocaleString()} actions` : "0 actions"}</p>
              {pageCount > 1 && (
                <nav aria-label="Action pagination" className="flex items-center gap-2">
                  <Button variant="outline" size="sm" aria-label="Previous page" disabled={page === 1} onClick={() => changePage(page - 1)}><ChevronLeft className="size-3.5" /></Button>
                  <span className="tabular-nums">{page} / {pageCount}</span>
                  <Button variant="outline" size="sm" aria-label="Next page" disabled={page === pageCount} onClick={() => changePage(page + 1)}><ChevronRight className="size-3.5" /></Button>
                </nav>
              )}
            </div>
          </section>
        </div>
      )}

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
