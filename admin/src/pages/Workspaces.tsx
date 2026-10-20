import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Building2, Download, Plus } from "lucide-react";
import { toast } from "sonner";
import { getWorkspaces, createWorkspace, exportWorkspaces } from "../api/client";
import { DataTable } from "../components/DataTable";
import { TableIdentity } from "../components/TableIdentity";
import { PageHeader } from "../components/PageHeader";
import { WorkspacePreview } from "../components/WorkspacePreview";
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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const previewRef = useRef<HTMLElement>(null);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [exporting, setExporting] = useState(false);
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
      render: (w: Workspace) => <TableIdentity name={w.name} detail={w.slug} Icon={Building2} tone="amber" />,
    },
    {
      key: "members",
      header: "Members",
      render: (w: Workspace) => <span className="table-count">{w.member_count}</span>,
      className: "w-28",
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

  const selected = data?.items.find((workspace) => workspace.id === selectedId) ?? data?.items[0];

  return (
    <div>
      <PageHeader title="Workspaces" actions={<>
          <Button variant="outline" disabled={exporting} onClick={async () => {
            setExporting(true);
            try { await exportWorkspaces(); } catch (error) { toast.error(error instanceof Error ? error.message : "Export failed"); }
            finally { setExporting(false); }
          }}>
            <Download className="size-4" />{exporting ? "Exporting…" : "Export CSV"}
          </Button>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="size-4" />Create workspace
          </Button>
      </>} />
      <div className="directory-layout">
        <div className="min-w-0">
          <div className="collection-toolbar">
            <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search workspaces…" />
            <span role="status" className="px-1 text-xs text-muted-foreground">{data ? `${data.total.toLocaleString()} ${search ? "matches" : "workspaces"}` : error ? "Count unavailable" : "Loading workspaces…"}</span>
          </div>

          {isLoading ? (
            <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />
          ) : (
            <>
              <DataTable
                columns={columns}
                data={data?.items ?? []}
                getRowKey={(w) => w.id}
                isRowSelected={(w) => selected?.id === w.id}
                rowActionLabel="Preview workspace"
                onRowClick={(w) => {
                  setSelectedId(w.id);
                  if (window.matchMedia("(max-width: 1279px)").matches) {
                    requestAnimationFrame(() => previewRef.current?.scrollIntoView({ block: "nearest", behavior: "instant" }));
                  }
                }}
                emptyMessage="No workspaces found"
                error={error}
              />
              {data && data.total > data.page_size && (
                <div className="collection-footer">
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
        </div>
        {selected && !error && <aside ref={previewRef} className="directory-preview" aria-label="Workspace preview"><WorkspacePreview key={selected.id} workspace={selected} /></aside>}
      </div>

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
