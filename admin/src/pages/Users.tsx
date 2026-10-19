import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Download, Upload } from "lucide-react";
import { bulkUserStatus, exportUsers, getUsers } from "../api/client";
import { StatusBadge } from "../components/Badge";
import { ConfirmModal } from "../components/ConfirmModal";
import { CsvImportModal } from "../components/CsvImportModal";
import { DataTable } from "../components/DataTable";
import { TableIdentity } from "../components/TableIdentity";
import { PageHeader } from "../components/PageHeader";
import { SearchInput } from "../components/SearchInput";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { User } from "../types/api";

export function Users() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"activate" | "deactivate" | null>(null);
  const [exporting, setExporting] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["users", page, search],
    queryFn: () => getUsers(page, 20, search || undefined),
  });

  const bulkMutation = useMutation({
    mutationFn: ({ ids, active }: { ids: string[]; active: boolean }) =>
      bulkUserStatus(ids, active),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      toast.success(`${result.affected} users updated`);
      setSelectedIds(new Set());
      setBulkAction(null);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (!data) return;
    if (data.items.every((user) => selectedIds.has(user.id))) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(data.items.map((u) => u.id)));
    }
  };

  const columns = [
    {
      key: "select",
      header: (
        <Checkbox
          aria-label="Select all users on this page"
          checked={data?.items.length && data.items.every((user) => selectedIds.has(user.id)) ? true : selectedIds.size > 0 ? "indeterminate" : false}
          onCheckedChange={toggleAll}
        />
      ),
      render: (u: User) => (
        <Checkbox
          aria-label={`Select ${u.name}`}
          checked={selectedIds.has(u.id)}
          onCheckedChange={() => toggleSelect(u.id)}
        />
      ),
      className: "w-10",
    },
    {
      key: "name",
      header: "User",
      render: (u: User) => <TableIdentity name={u.name} detail={u.email} seed={u.id} />,
    },
    {
      key: "status",
      header: "Status",
      render: (u: User) => <StatusBadge active={u.is_active} />,
      className: "w-28",
    },
    {
      key: "workspaces",
      header: "Workspaces",
      render: (u: User) => <span className="table-count">{u.workspace_count}</span>,
      className: "w-28",
    },
    {
      key: "created",
      header: "Joined",
      render: (u: User) => (
        <span className="text-muted-foreground text-xs">{new Date(u.created_at).toLocaleDateString()}</span>
      ),
      className: "w-28",
    },
  ];

  return (
    <div>
      <PageHeader title="Users" count={data?.total} actions={<>
          <Button variant="outline" disabled={exporting} onClick={async () => {
            setExporting(true);
            try { await exportUsers(); } catch (error) { toast.error(error instanceof Error ? error.message : "Export failed"); }
            finally { setExporting(false); }
          }}>
            <Download className="size-4" />{exporting ? "Exporting…" : "Export CSV"}
          </Button>
          <Button onClick={() => setShowImport(true)}>
            <Upload className="size-4" />Import CSV
          </Button>
      </>} />

      <div className="collection-toolbar">
        <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); setSelectedIds(new Set()); }} placeholder="Search users by name or email…" />
        <span role="status" className="text-xs text-muted-foreground">{data ? `${data.total.toLocaleString()} ${search ? "matches" : "people"}` : error ? "Count unavailable" : "Loading…"}</span>
      </div>

      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={data?.items ?? []}
            getRowKey={(u) => u.id}
            isRowSelected={(u) => selectedIds.has(u.id)}
            onRowClick={(u) => navigate(`/users/${u.id}`)}
            emptyMessage={search ? "No users match your search. Try a different name or email." : "No users found. Import users from a CSV file."}
            error={error}
          />
          {data && data.total > data.page_size && (
            <Pagination page={data.page} total={data.total} pageSize={data.page_size} onChange={(next) => { setPage(next); setSelectedIds(new Set()); }} />
          )}
        </>
      )}

      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <div className="sticky bottom-4 z-10 mx-auto flex w-fit max-w-full flex-wrap items-center justify-center gap-3 rounded-xl border border-border bg-card px-4 py-3 shadow-xl">
          <span className="text-sm text-foreground">{selectedIds.size} selected</span>
          <button
            onClick={() => setBulkAction("activate")}
            className="px-3 py-1.5 rounded text-xs font-medium bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 ring-1 ring-emerald-500/20 hover:bg-emerald-500/20"
          >
            Activate
          </button>
          <Button variant="destructive" size="sm" onClick={() => setBulkAction("deactivate")}>
            Deactivate
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
            Clear
          </Button>
        </div>
      )}

      <ConfirmModal
        open={bulkAction !== null}
        onClose={() => setBulkAction(null)}
        onConfirm={() =>
          bulkMutation.mutate({
            ids: Array.from(selectedIds),
            active: bulkAction === "activate",
          })
        }
        title={bulkAction === "activate" ? "Activate Users" : "Deactivate Users"}
        message={`${bulkAction === "activate" ? "Activate" : "Deactivate"} ${selectedIds.size} ${selectedIds.size === 1 ? "user" : "users"}?`}
        confirmLabel={bulkAction === "activate" ? "Activate" : "Deactivate"}
        danger={bulkAction === "deactivate"}
        isPending={bulkMutation.isPending}
      />

      <CsvImportModal
        open={showImport}
        onClose={() => setShowImport(false)}
        onComplete={() => queryClient.invalidateQueries({ queryKey: ["users"] })}
      />
    </div>
  );
}

function Pagination({ page, total, pageSize, onChange }: { page: number; total: number; pageSize: number; onChange: (p: number) => void }) {
  const totalPages = Math.ceil(total / pageSize);
  return (
    <div className="collection-footer">
      <span>{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total.toLocaleString()}</span>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>Prev</Button>
        <span className="px-2 py-1">{page} / {totalPages}</span>
        <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>Next</Button>
      </div>
    </div>
  );
}
