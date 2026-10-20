import { ChevronRight, Inbox, TriangleAlert } from "lucide-react";

interface Column<T> {
  key: string;
  header: React.ReactNode;
  render: (row: T) => React.ReactNode;
  className?: string;
}

interface Props<T> {
  columns: Column<T>[];
  data: T[];
  onRowClick?: (row: T) => void;
  emptyMessage?: string;
  error?: Error | null;
  getRowKey?: (row: T) => React.Key;
  isRowSelected?: (row: T) => boolean;
  rowActionLabel?: string;
}

export function DataTable<T>({ columns, data, onRowClick, emptyMessage = "No data", error, getRowKey, isRowSelected, rowActionLabel = "Open details" }: Props<T>) {
  if (error) {
    return (
      <div role="alert" className="table-surface flex flex-col items-center gap-3 px-6 py-14 text-center text-sm">
        <span className="flex size-12 items-center justify-center rounded-2xl bg-destructive/10 text-destructive"><TriangleAlert className="size-6" /></span>
        <p className="font-semibold">Unable to load this list</p>
        <p className="text-muted-foreground">{error.message}</p>
      </div>
    );
  }
  if (data.length === 0) {
    return (
      <div role="status" className="table-surface flex flex-col items-center gap-4 px-6 py-16 text-center text-sm text-muted-foreground">
        <span className="flex size-14 -rotate-6 items-center justify-center rounded-2xl bg-primary/8 text-primary"><Inbox className="size-7 rotate-6" strokeWidth={1.5} /></span>
        <p>{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="table-surface">
      <table className="admin-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} scope="col" className={col.className}>
                {col.header}
              </th>
            ))}
            {onRowClick && <th scope="col" className="w-14"><span className="sr-only">{rowActionLabel}</span></th>}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr
              key={getRowKey ? getRowKey(row) : i}
              tabIndex={onRowClick ? 0 : undefined}
              data-selected={isRowSelected?.(row) || undefined}
              onClick={(event) => {
                if (!(event.target as HTMLElement).closest("a, button, input, select, textarea, [role='button'], [role='checkbox']")) onRowClick?.(row);
              }}
              onKeyDown={(event) => {
                if (onRowClick && event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  onRowClick(row);
                }
              }}
              className={onRowClick ? "table-row-link" : undefined}
            >
              {columns.map((col) => (
                <td key={col.key} className={col.className}>{col.render(row)}</td>
              ))}
              {onRowClick && (
                <td aria-hidden="true" className="w-14">
                  <span className="table-row-arrow"><ChevronRight className="size-4" strokeWidth={1.75} /></span>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
