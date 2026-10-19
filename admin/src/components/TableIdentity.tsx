import type { LucideIcon } from "lucide-react";

const TONES = {
  rose: "bg-rose-100 text-rose-700 dark:bg-rose-400/15 dark:text-rose-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-400/15 dark:text-amber-300",
  teal: "bg-teal-100 text-teal-800 dark:bg-teal-400/15 dark:text-teal-300",
  blue: "bg-blue-100 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300",
  violet: "bg-violet-100 text-violet-700 dark:bg-violet-400/15 dark:text-violet-300",
};

export function TableIdentity({ name, detail, Icon, tone, seed = name }: {
  name: string;
  detail?: string;
  Icon?: LucideIcon;
  tone?: keyof typeof TONES;
  seed?: string;
}) {
  const colors = Object.values(TONES);
  const hash = Array.from(seed).reduce((value, character) => (value * 31 + character.charCodeAt(0)) >>> 0, 0);
  const color = tone ? TONES[tone] : colors[hash % colors.length];
  const words = name.trim().split(/\s+/);
  const initials = `${words[0]?.charAt(0) ?? ""}${words.length > 1 ? words.at(-1)?.charAt(0) ?? "" : ""}`.toUpperCase();

  return (
    <div className="flex items-center gap-3">
      <span aria-hidden="true" className={`flex size-10 shrink-0 items-center justify-center rounded-xl text-xs font-semibold ring-1 ring-inset ring-black/5 dark:ring-white/5 ${color}`}>
        {Icon ? <Icon className="size-[18px]" strokeWidth={1.75} /> : initials || "?"}
      </span>
      <div className="min-w-0">
        <div className="font-semibold text-foreground">{name}</div>
        {detail && <div className="mt-0.5 text-xs text-muted-foreground">{detail}</div>}
      </div>
    </div>
  );
}
