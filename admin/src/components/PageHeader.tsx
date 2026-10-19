import type { ReactNode } from "react";

export function PageHeader({ title, description, count, actions }: {
  title: string;
  description?: string;
  count?: number;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="min-w-0">
        <div className="flex items-baseline gap-3">
          <h1 className="page-title">{title}</h1>
          {count !== undefined && <span className="font-mono text-sm text-muted-foreground">{count.toLocaleString()}</span>}
        </div>
        {description && <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
