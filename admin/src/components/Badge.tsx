const ROLE_COLORS: Record<string, string> = {
  owner: "bg-amber-500/10 text-amber-700 dark:text-amber-400 ring-amber-500/20",
  admin: "bg-purple-500/10 text-purple-700 dark:text-purple-400 ring-purple-500/20",
  editor: "bg-blue-500/10 text-blue-700 dark:text-blue-400 ring-blue-500/20",
  viewer: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 ring-zinc-500/20",
};

export function RoleBadge({ role }: { role: string }) {
  const color = ROLE_COLORS[role] ?? ROLE_COLORS.viewer;
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium ring-1 ring-inset ${color}`}>
      {role}
    </span>
  );
}

export function StatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap px-2.5 py-1 rounded-full text-xs font-medium ring-1 ring-inset ${
        active
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 ring-emerald-500/20"
          : "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 ring-zinc-500/20"
      }`}
    >
      <span className={`size-1.5 rounded-full ${active ? "bg-emerald-500 shadow-[0_0_0_2px_rgb(16_185_129/12%)]" : "bg-zinc-400"}`} />
      {active ? "Active" : "Inactive"}
    </span>
  );
}

export function VisibilityBadge({ visibility }: { visibility: string }) {
  const isWorkspace = visibility === "workspace";
  return (
    <span
      className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium ring-1 ring-inset ${
        isWorkspace
          ? "bg-blue-500/10 text-blue-700 dark:text-blue-400 ring-blue-500/20"
          : "bg-orange-500/10 text-orange-700 dark:text-orange-400 ring-orange-500/20"
      }`}
    >
      {visibility}
    </span>
  );
}
