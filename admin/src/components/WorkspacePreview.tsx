import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { getWorkspace, getWorkspaceMembers } from "../api/client";
import { RoleBadge } from "./Badge";
import { Button } from "./ui/button";
import type { Workspace } from "../types/api";

export function WorkspacePreview({ workspace }: { workspace: Workspace }) {
  const detail = useQuery({ queryKey: ["workspace", workspace.id], queryFn: () => getWorkspace(workspace.id) });
  const members = useQuery({ queryKey: ["workspace-members", workspace.id], queryFn: () => getWorkspaceMembers(workspace.id) });

  return (
    <>
      <p className="section-kicker">Workspace preview</p>
      <h2 className="mt-3 break-words text-xl font-semibold tracking-tight">{workspace.name}</h2>
      <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{workspace.slug}</p>
      {workspace.description && <p className="mt-4 text-sm leading-relaxed text-muted-foreground">{workspace.description}</p>}
      <Button asChild className="mt-5 w-full" variant="outline"><Link to={`/workspaces/${workspace.id}`}>Open workspace <ArrowRight className="ml-auto size-4" /></Link></Button>
      <dl className="my-6 grid grid-cols-2 border-y border-border py-4">
        <div><dt className="text-xs text-muted-foreground">Members</dt><dd className="mt-1 text-2xl font-medium tabular-nums">{workspace.member_count}</dd></div>
        <div className="border-l border-border pl-5"><dt className="text-xs text-muted-foreground">Groups</dt><dd className="mt-1 text-2xl font-medium tabular-nums">{detail.data?.group_count ?? "N/A"}</dd></div>
      </dl>
      {detail.isError && <p className="mb-4 text-xs text-muted-foreground">Couldn’t load the group count.</p>}
      <div className="section-heading"><h3 className="section-title">Members</h3><Link className="text-link" to={`/workspaces/${workspace.id}`}>Manage</Link></div>
      {members.isError ? <div className="text-sm text-muted-foreground"><p>Couldn’t load members.</p><button className="mt-2 text-link" onClick={() => void members.refetch()}>Try again</button></div> : members.isLoading ? <div role="status" aria-label="Loading members" className="h-32 animate-pulse rounded bg-muted" /> : members.data?.length ? (
        <div className="space-y-4">
          {members.data.slice(0, 5).map((member) => <Link to={`/users/${member.user_id}`} key={member.user_id} className="group flex items-center justify-between gap-2">
            <div className="min-w-0"><p className="truncate text-sm font-medium group-hover:text-primary">{member.name}</p><p className="mt-0.5 truncate text-xs text-muted-foreground">{member.email}</p></div><RoleBadge role={member.role} />
          </Link>)}
          {members.data.length > 5 && <Link to={`/workspaces/${workspace.id}`} className="text-link">View all {members.data.length} members <ArrowRight className="size-3.5" /></Link>}
        </div>
      ) : <p className="text-sm text-muted-foreground">No members yet.</p>}
      <p className="mt-6 border-t border-border pt-4 text-xs text-muted-foreground">Created {new Date(workspace.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</p>
    </>
  );
}
