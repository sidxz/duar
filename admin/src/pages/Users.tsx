import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { bulkUserStatus, exportUsers, getUsers } from "../api/client";
import { StatusBadge } from "../components/Badge";
import { ConfirmModal } from "../components/ConfirmModal";
import { CsvImportModal } from "../components/CsvImportModal";
import { DataTable } from "../components/DataTable";
import { SearchInput } from "../components/SearchInput";
import { Button } from "@/components/ui/button";
import type { User } from "../types/api";

export function Users() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<"activate" | "deactivate" | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["users", page, search],
    queryFn: () => getUsers(page, 20, search || undefined),
  });

  const bulkMutation = useMutation({
    mutationFn: ({ ids, active }: { ids: string[]; active: boolean }) =>
      bulkUserStatus(ids, active),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
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
    if (selectedIds.size === data.items.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(data.items.map((u) => u.id)));
    }
  };

  const columns = [
    {
      key: "select",
      header: (
        <input
          type="checkbox"
          checked={!!data && data.items.length > 0 && selectedIds.size === data.items.length}
          onChange={toggleAll}
          className="rounded border-border bg-background"
          onClick={(e) => e.stopPropagation()}
        />
      ) as unknown as string,
      render: (u: User) => (
        <input
          type="checkbox"
          checked={selectedIds.has(u.id)}
          onChange={() => toggleSelect(u.id)}
          onClick={(e) => e.stopPropagation()}
          className="rounded border-border bg-background"
        />
      ),
      className: "w-10",
    },
    {
      key: "name",
      header: "User",
      render: (u: User) => (
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-xs font-medium text-muted-foreground shrink-0">
            {u.name.charAt(0).toUpperCase()}
          </div>
          <div>
            <div className="font-medium text-sm text-foreground">{u.name}</div>
            <div className="text-xs text-muted-foreground font-mono">{u.email}</div>
          </div>
        </div>
      ),
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
      render: (u: User) => <span className="text-muted-foreground tabular-nums">{u.workspace_count}</span>,
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
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Users</h1>
        <div className="flex items-center gap-3">
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search users..." />
          <Button variant="outline" size="sm" onClick={() => exportUsers()}>
            Export CSV
          </Button>
          <Button size="sm" onClick={() => setShowImport(true)}>
            + Import CSV
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={data?.items ?? []}
            onRowClick={(u) => navigate(`/users/${u.id}`)}
            emptyMessage="No users found"
            error={error}
          />
          {data && data.total > data.page_size && (
            <Pagination page={data.page} total={data.total} pageSize={data.page_size} onChange={setPage} />
          )}
        </>
      )}

      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-card border border-border rounded-lg px-4 py-2.5 flex items-center gap-3 shadow-xl z-50">
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
        message={`Are you sure you want to ${bulkAction} ${selectedIds.size} user(s)?`}
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
    <div className="flex items-center justify-between text-xs text-muted-foreground">
      <span>{total} total</span>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>Prev</Button>
        <span className="px-2 py-1">{page} / {totalPages}</span>
        <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>Next</Button>
      </div>
    </div>
  );
}
