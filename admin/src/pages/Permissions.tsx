import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, SlidersHorizontal } from "lucide-react";
import { adminListPermissions, getAllWorkspaces } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { PermissionDetails } from "../components/PermissionDetails";
import { VisibilityBadge } from "../components/Badge";
import { SearchInput } from "../components/SearchInput";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const PAGE_SIZE = 10;

export function Permissions() {
  const [params, setParams] = useSearchParams();
  const workspace = params.get("workspace") ?? "";
  const service = params.get("service") ?? "";
  const resource = params.get("resource") ?? "";
  const owner = params.get("owner") ?? "";
  const requestedPage = Number(params.get("page") ?? 1);
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const sortBy = params.get("sort") === "shares" ? "shares" : undefined;
  const sortOrder = params.get("order") === "asc" ? "asc" : "desc";
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const selectedRow = useRef<HTMLTableRowElement | null>(null);
  const filterCount = [workspace, service, owner].filter(Boolean).length;

  const { data: workspaces = [], isError: workspaceError, refetch: retryWorkspaces } = useQuery({
    queryKey: ["all-workspaces"], queryFn: getAllWorkspaces,
  });
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["admin-permissions", page, PAGE_SIZE, workspace, service, resource, owner, sortBy, sortOrder],
    queryFn: () => adminListPermissions({
      page, pageSize: PAGE_SIZE, workspaceId: workspace || undefined, serviceName: service || undefined,
      resourceId: resource || undefined, owner: owner || undefined, sortBy, sortOrder,
    }),
  });

  function changeFilter(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value); else next.delete(key);
      next.delete("page");
      return next;
    }, { replace: key !== "workspace" });
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
      for (const key of ["workspace", "service", "resource", "owner", "page"]) next.delete(key);
      return next;
    });
  }

  function toggleSort() {
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("sort", "shares");
      next.set("order", sortBy === "shares" && sortOrder === "desc" ? "asc" : "desc");
      next.delete("page");
      return next;
    });
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div>
      <PageHeader title="Permissions" actions={<Button variant="outline" className="xl:hidden" aria-expanded={filtersOpen} aria-controls="permission-filters" onClick={() => setFiltersOpen(!filtersOpen)}><SlidersHorizontal className="size-4" />Filters{filterCount > 0 && <span className="font-mono text-xs">{filterCount}</span>}</Button>} />
      <div className="permission-registry">
        <aside id="permission-filters" aria-label="Resource filters" data-open={filtersOpen} className="permission-filters">
          <div className="mb-4 flex items-center justify-between gap-2">
            <h2 className="section-title">Filter resources</h2>
            {(filterCount > 0 || resource) && <button type="button" onClick={clearFilters} className="text-xs text-primary hover:underline">Reset</button>}
          </div>
          <div className="space-y-5">
            <div className="space-y-2">
              <label htmlFor="permission-workspace" className="text-xs text-muted-foreground">Workspace</label>
              <select id="permission-workspace" value={workspace} onChange={(event) => changeFilter("workspace", event.target.value)} className="h-10 w-full min-w-0 rounded-md border border-input bg-card px-3 text-sm">
                <option value="">All workspaces</option>
                {workspace && !workspaces.some((item) => item.id === workspace) && <option value={workspace}>{workspace}</option>}
                {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              {workspaceError && <button onClick={() => void retryWorkspaces()} className="text-xs text-destructive">Retry loading workspaces</button>}
            </div>
            <div className="space-y-2"><p className="text-xs text-muted-foreground">Service</p><SearchInput value={service} onChange={(value) => changeFilter("service", value)} placeholder="Exact service name…" className="sm:w-full" /></div>
            <div className="space-y-2"><p className="text-xs text-muted-foreground">Owner</p><SearchInput value={owner} onChange={(value) => changeFilter("owner", value)} placeholder="Owner email…" className="sm:w-full" /></div>
          </div>
        </aside>

        <section aria-label="Resource permissions" className="min-w-0">
          <div className="action-registry-toolbar">
            <h2 className="hidden text-sm font-medium xl:block">{filterCount || resource ? "Filtered resources" : "All resources"}</h2>
            <SearchInput value={resource} onChange={(value) => changeFilter("resource", value)} placeholder="Search resource ID prefix…" className="sm:w-80 xl:ml-auto" />
          </div>

          {isLoading ? <div aria-label="Loading permissions" className="h-64 animate-pulse rounded-lg bg-muted/50" /> : isError ? (
            <div role="alert" className="space-y-3 rounded-lg border border-border px-6 py-10 text-center">
              <p className="text-sm font-medium">Unable to load permissions</p><p className="text-xs text-muted-foreground">{error.message}</p>
              <Button variant="outline" size="sm" onClick={() => void refetch()}>Try again</Button>
            </div>
          ) : data?.items.length ? (
            <div className="table-surface">
              <table className="admin-table permission-table">
                <caption className="sr-only">Resource ownership, visibility, and sharing</caption>
                <thead><tr>
                  <th scope="col">Resource</th>
                  <th scope="col" className="hidden w-44 md:table-cell">Owner</th>
                  <th scope="col" className="hidden w-28 sm:table-cell">Visibility</th>
                  <th scope="col" className="w-20" aria-sort={sortBy === "shares" ? sortOrder === "asc" ? "ascending" : "descending" : "none"}>
                    <button onClick={toggleSort} className="uppercase tracking-[inherit] hover:text-primary">Shares{sortBy === "shares" ? sortOrder === "asc" ? " ↑" : " ↓" : ""}</button>
                  </th>
                  <th scope="col" className="w-10"><span className="sr-only">Open resource access</span></th>
                </tr></thead>
                <tbody>{data.items.map((permission) => (
                  <tr key={permission.id} tabIndex={0} className="table-row-link" data-selected={selectedId === permission.id || undefined}
                    onClick={(event) => { selectedRow.current = event.currentTarget; setSelectedId(permission.id); }}
                    onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); selectedRow.current = event.currentTarget; setSelectedId(permission.id); } }}>
                    <td>
                      <p className="mb-1 text-xs text-muted-foreground [overflow-wrap:anywhere]">{permission.service_name} <span aria-hidden="true">/</span> {permission.resource_type}</p>
                      <p className="break-all font-mono text-xs font-medium">{String(permission.resource_id)}</p>
                      <p className="mt-1 break-all text-xs text-muted-foreground md:hidden">{permission.owner_email ?? "No owner email"}</p>
                      <div className="mt-2 sm:hidden"><VisibilityBadge visibility={permission.visibility} /></div>
                    </td>
                    <td className="hidden break-all text-xs text-muted-foreground md:table-cell">{permission.owner_email ?? "No owner email"}</td>
                    <td className="hidden sm:table-cell"><VisibilityBadge visibility={permission.visibility} /></td>
                    <td><span className="table-count">{permission.share_count}</span></td>
                    <td aria-hidden="true"><ChevronRight className="size-4 text-muted-foreground" /></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : (
            <div role="status" className="table-surface space-y-3 px-6 py-14 text-center">
              <p className="text-sm font-medium">{page > totalPages ? "No resources on this page" : "No resources found"}</p>
              <p className="text-sm text-muted-foreground">{page > totalPages ? "Return to the first page to see available resources." : filterCount || resource ? "Try another search or adjust your filters." : "No resource permissions have been registered."}</p>
              {page > totalPages ? <Button variant="outline" size="sm" onClick={() => changePage(1)}>Back to first page</Button> : (filterCount > 0 || resource) && <Button variant="outline" size="sm" onClick={clearFilters}>Clear filters</Button>}
            </div>
          )}

          {data && !isError && <div className="collection-footer">
            <p role="status">{data.items.length > 0 ? `${(data.page - 1) * data.page_size + 1}–${Math.min(data.page * data.page_size, data.total)} of ${data.total.toLocaleString()} resources` : "0 resources shown"}</p>
            {totalPages > 1 && page <= totalPages && <nav aria-label="Resource pagination" className="flex items-center gap-2">
              <Button variant="outline" size="sm" aria-label="Previous page" disabled={page <= 1} onClick={() => changePage(Math.min(page - 1, totalPages))}><ChevronLeft className="size-3.5" /></Button>
              <span className="tabular-nums">{Math.min(data.page, totalPages)} / {totalPages}</span>
              <Button variant="outline" size="sm" aria-label="Next page" disabled={page >= totalPages} onClick={() => changePage(page + 1)}><ChevronRight className="size-3.5" /></Button>
            </nav>}
          </div>}
        </section>
      </div>

      <Dialog open={selectedId !== null} onOpenChange={(open) => { if (!open) setSelectedId(null); }}>
        <DialogContent className="top-0 right-0 bottom-0 left-auto flex h-dvh max-h-dvh w-full max-w-full translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-y-0 border-r-0 p-0 sm:max-w-xl data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right data-[state=open]:zoom-in-100 data-[state=closed]:zoom-out-100"
          onCloseAutoFocus={(event) => { event.preventDefault(); selectedRow.current?.focus(); }}>
          <DialogHeader className="shrink-0 border-b border-border p-6 pr-12 text-left">
            <DialogTitle>Resource access</DialogTitle>
            <DialogDescription>Ownership, visibility, and individual shares.</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-6">{selectedId && <PermissionDetails key={selectedId} permissionId={selectedId} />}</div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
