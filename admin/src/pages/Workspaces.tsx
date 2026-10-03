import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { getWorkspaces, createWorkspace, exportWorkspaces } from "../api/client";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { SearchInput } from "../components/SearchInput";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Workspace } from "../types/api";

export function Workspaces() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "", description: "" });

  const { data, isLoading, error } = useQuery({
    queryKey: ["workspaces", page, search],
    queryFn: () => getWorkspaces(page, 20, search || undefined),
  });

  const create = useMutation({
    mutationFn: () =>
      createWorkspace({
        name: form.name,
        slug: form.slug,
        description: form.description || undefined,
      }),
    onSuccess: (ws) => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setShowCreate(false);
      setForm({ name: "", slug: "", description: "" });
      navigate(`/workspaces/${ws.id}`);
    },
  });

  const autoSlug = (name: string) =>
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");

  const columns = [
    {
      key: "name",
      header: "Workspace",
      render: (w: Workspace) => (
        <div>
          <div className="font-medium text-sm">{w.name}</div>
          <div className="text-xs text-muted-foreground font-mono">{w.slug}</div>
        </div>
      ),
    },
    {
      key: "members",
      header: "Members",
      render: (w: Workspace) => <span className="text-muted-foreground tabular-nums">{w.member_count}</span>,
      className: "w-28",
    },
    {
      key: "description",
      header: "Description",
      render: (w: Workspace) => (
        <span className="text-muted-foreground text-sm truncate block max-w-xs">
          {w.description || "--"}
        </span>
      ),
    },
    {
      key: "created",
      header: "Created",
      render: (w: Workspace) => (
        <span className="text-muted-foreground text-xs">{new Date(w.created_at).toLocaleDateString()}</span>
      ),
      className: "w-28",
    },
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Workspaces</h1>
        <div className="flex items-center gap-3">
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search workspaces..." />
          <Button variant="outline" size="sm" onClick={() => exportWorkspaces()}>
            Export CSV
          </Button>
          <Button size="sm" onClick={() => setShowCreate(true)}>
            + Create Workspace
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
            onRowClick={(w) => navigate(`/workspaces/${w.id}`)}
            emptyMessage="No workspaces found"
            error={error}
          />
          {data && data.total > data.page_size && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{data.total} total</span>
              <div className="flex gap-1">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</Button>
                <span className="px-2 py-1">{data.page} / {Math.ceil(data.total / data.page_size)}</span>
                <Button variant="outline" size="sm" disabled={page >= Math.ceil(data.total / data.page_size)} onClick={() => setPage(page + 1)}>Next</Button>
              </div>
            </div>
          )}
        </>
      )}

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="Create Workspace">
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="ws-name">Name</Label>
            <Input
              id="ws-name"
              value={form.name}
              onChange={(e) => {
                const name = e.target.value;
                setForm((f) => ({
                  ...f,
                  name,
                  slug: f.slug === autoSlug(f.name) ? autoSlug(name) : f.slug,
                }));
              }}
              placeholder="My Workspace"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ws-slug">Slug</Label>
            <Input
              id="ws-slug"
              value={form.slug}
              onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
              placeholder="my-workspace"
              className="font-mono"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ws-description">Description (optional)</Label>
            <Input
              id="ws-description"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </div>
          {create.isError && (
            <div className="text-xs text-destructive">{(create.error as Error).message}</div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowCreate(false)}>Cancel</Button>
            <Button
              size="sm"
              onClick={() => create.mutate()}
              disabled={!form.name || !form.slug || create.isPending}
            >
              {create.isPending ? "Creating..." : "Create"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
