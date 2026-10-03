const ROLE_COLORS: Record<string, string> = {
  owner: "bg-purple-500/20 text-purple-400",
  admin: "bg-red-500/20 text-red-400",
  editor: "bg-blue-500/20 text-blue-400",
  viewer: "bg-zinc-500/20 text-zinc-400",
};

export function RoleBadge({ role }: { role: string }) {
  const color = ROLE_COLORS[role] ?? ROLE_COLORS.viewer;
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${color}`}>
      {role}
    </span>
  );
}
