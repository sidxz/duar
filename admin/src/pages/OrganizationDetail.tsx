import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy } from "lucide-react";

import {
  addOrgDomain,
  deleteOrganization,
  getOrganization,
  getOrgUsers,
  removeOrgDomain,
  updateOrganization,
} from "../api/client";
import type { OrgDomain } from "../types/api";
import { ConfirmModal } from "../components/ConfirmModal";
import { StatusBadge } from "../components/Badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

// Named export (codebase convention). Only the OrgDomain type is imported; the
// org object is inferred from getOrganization's return, so the page's
// `OrganizationDetail` name doesn't collide with the `OrganizationDetail` type.
export function OrganizationDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [newDomain, setNewDomain] = useState("");
  const [includeSub, setIncludeSub] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deleteSlug, setDeleteSlug] = useState("");
  const [usersPage, setUsersPage] = useState(1);

  const {
    data: org,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["organization", id],
    queryFn: () => getOrganization(id!),
    enabled: !!id,
  });

  // Clamp the requested page to the org's user count (already known from the org
  // query) so a shrinking list can never strand the admin on an empty, out-of-range
  // page with the pager hidden. Page size matches getOrgUsers' default.
  const USERS_PER_PAGE = 50;
  const usersLastPage = Math.max(
    1,
    Math.ceil((org?.user_count ?? 0) / USERS_PER_PAGE),
  );
  const usersPageClamped = Math.min(usersPage, usersLastPage);

  const { data: usersData } = useQuery({
    queryKey: ["organization-users", id, usersPageClamped],
    queryFn: () => getOrgUsers(id!, usersPageClamped),
    enabled: !!id,
    placeholderData: keepPreviousData,
  });
  const users = usersData?.items ?? [];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["organization", id] });
    queryClient.invalidateQueries({ queryKey: ["organization-users", id] });
    queryClient.invalidateQueries({ queryKey: ["organizations"] });
  };

  const toggleEnabled = useMutation({
    mutationFn: () => updateOrganization(id!, { enabled: !org!.enabled }),
    onSuccess: () => {
      invalidate();
      toast.success("Updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const addDomain = useMutation({
    mutationFn: () =>
      addOrgDomain(id!, {
        domain: newDomain.trim(),
        include_subdomains: includeSub,
      }),
    onSuccess: () => {
      invalidate();
      setNewDomain("");
      setIncludeSub(false);
      toast.success("Domain added");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeDomain = useMutation({
    mutationFn: (domainId: string) => removeOrgDomain(id!, domainId),
    onSuccess: () => {
      invalidate();
      toast.success("Domain removed");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteOrganization(id!),
    onSuccess: () => {
      setShowDelete(false);
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
      // Deleting an org also CASCADE-removes it from workspace allow-lists, so
      // invalidate those so any open Access tab reseeds without the dead id.
      queryClient.invalidateQueries({ queryKey: ["workspace-allowed-orgs"] });
      toast.success("Organization deleted");
      navigate("/organizations");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  if (isLoading) {
    return <div className="h-64 bg-muted/50 rounded-lg animate-pulse" />;
  }

  if (isError || !org) {
    return (
      <div className="space-y-3 max-w-3xl">
        <Link
          to="/organizations"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Organizations
        </Link>
        <div className="border border-border rounded-lg p-6 text-center">
          <p className="text-sm text-foreground">
            {(error as Error)?.message ?? "Organization not found."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/organizations" className="hover:text-foreground">
          Organizations
        </Link>
        <span>/</span>
        <span className="text-foreground">{org.name}</span>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold text-foreground">
            {org.is_public && <span className="mr-1">🌐</span>}
            {org.name}
          </h1>
          <div className="flex items-center gap-1.5">
            <code className="text-xs text-muted-foreground font-mono">
              {org.slug}
            </code>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => {
                navigator.clipboard.writeText(org.id);
                toast.success("Copied");
              }}
              aria-label="Copy organization ID"
            >
              <Copy className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge active={org.enabled} />
          <Button
            size="sm"
            onClick={() => toggleEnabled.mutate()}
            disabled={toggleEnabled.isPending}
          >
            {org.enabled
              ? org.is_public
                ? "Disable public sign-in"
                : "Disable"
              : org.is_public
                ? "Enable public sign-in"
                : "Enable"}
          </Button>
        </div>
      </div>

      {org.is_public && (
        <p className="text-sm text-muted-foreground border border-border rounded-md p-3">
          This is the public catch-all organization for users whose email domain
          is not claimed by any other org. Its <b>Enabled</b> state is the global
          public-sign-in switch. It has no domains and cannot be deleted.
        </p>
      )}

      {/* Domains (real orgs only) */}
      {!org.is_public && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">Email domains</h2>
          {org.domains.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No domains yet. Users from this org can't be resolved until you add
              one.
            </p>
          ) : (
            <ul className="space-y-1">
              {org.domains.map((d: OrgDomain) => (
                <li
                  key={d.id}
                  className="flex items-center justify-between border border-border rounded-md px-3 py-2"
                >
                  <span className="text-sm">
                    <code className="font-mono text-foreground">{d.domain}</code>
                    {d.include_subdomains && (
                      <span className="ml-2 text-xs text-emerald-700 dark:text-emerald-400">
                        +subdomains
                      </span>
                    )}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeDomain.mutate(d.id)}
                    className="text-destructive hover:text-destructive"
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Label htmlFor="add-domain" className="text-xs text-muted-foreground">
                Add domain
              </Label>
              <Input
                id="add-domain"
                value={newDomain}
                onChange={(e) => setNewDomain(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && newDomain.trim() && !addDomain.isPending)
                    addDomain.mutate();
                }}
                placeholder="tamu.edu"
                className="mt-1"
              />
            </div>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground pb-2">
              <input
                type="checkbox"
                checked={includeSub}
                onChange={(e) => setIncludeSub(e.target.checked)}
              />
              include subdomains
            </label>
            <Button
              variant="outline"
              size="sm"
              onClick={() => addDomain.mutate()}
              disabled={!newDomain.trim() || addDomain.isPending}
            >
              Add
            </Button>
          </div>
        </section>
      )}

      {/* Users in org */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">
          Users in this organization ({org.user_count})
        </h2>
        {users.length === 0 ? (
          <p className="text-sm text-muted-foreground">No users.</p>
        ) : (
          <>
            <ul className="divide-y divide-border border border-border rounded-md">
              {users.map((u) => (
                <li
                  key={u.id}
                  className="px-3 py-2 flex items-center justify-between"
                >
                  <span className="text-sm font-mono text-foreground">
                    {u.email}
                  </span>
                  <span className="text-xs text-muted-foreground">{u.name}</span>
                </li>
              ))}
            </ul>
            {usersData && usersData.total > usersData.page_size && (
              <div className="flex items-center justify-between text-xs text-muted-foreground pt-1">
                <span>{usersData.total} total</span>
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={usersPageClamped <= 1}
                    onClick={() => setUsersPage(usersPageClamped - 1)}
                  >
                    Prev
                  </Button>
                  <span className="px-2 py-1">
                    {usersPageClamped} / {usersLastPage}
                  </span>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={usersPageClamped >= usersLastPage}
                    onClick={() => setUsersPage(usersPageClamped + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </section>

      {/* Danger zone (real orgs only) */}
      {!org.is_public && (
        <section className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <h2 className="text-sm font-semibold text-destructive">Danger zone</h2>
          <p className="text-sm text-muted-foreground">
            Deleting an org un-assigns its users (they fall back to the public org
            on next sign-in). Deletion is blocked while the org is in <b>any</b>{" "}
            workspace's allowed-organizations list; remove it from those
            workspaces' access settings first.
          </p>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => {
              setDeleteSlug("");
              setShowDelete(true);
            }}
          >
            Delete organization
          </Button>
        </section>
      )}

      <ConfirmModal
        open={showDelete}
        onClose={() => setShowDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete Organization"
        message={`This permanently deletes "${org.name}". Users on its domains fall back to the public org at next sign-in. Type the slug to confirm.`}
        confirmLabel="Delete Organization"
        danger
        isPending={remove.isPending}
        confirmInput={org.slug}
        confirmInputValue={deleteSlug}
        onConfirmInputChange={setDeleteSlug}
      />
    </div>
  );
}
