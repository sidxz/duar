import { NavLink, Outlet } from "react-router-dom";
import { Plug } from "lucide-react";

const SUBNAV = [
  { to: "/mcp", label: "Dashboard", end: true },
  { to: "/mcp/apps", label: "MCP Apps", end: false },
] as const;

/**
 * Sub-branded shell for the Duar MCP console. Renders inside the global admin
 * Layout (keeps the main sidebar) but carries its own name, accent, and sub-nav
 * so the feature reads as a product, not an admin tab.
 */
export function McpLayout() {
  return (
    <div className="space-y-6">
      <header className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-mcp-accent text-mcp-accent-foreground shrink-0">
          <Plug className="size-5" strokeWidth={2} />
        </div>
        <div>
          <h1 className="text-xl font-semibold leading-tight">Duar MCP</h1>
          <p className="text-xs text-muted-foreground">
            AI agents call your apps' MCP tools as the signed-in user — gated tools need an agent grant.
          </p>
        </div>
      </header>

      <nav className="flex gap-1 border-b border-border">
        {SUBNAV.map(({ to, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "border-mcp-accent text-foreground font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <Outlet />
    </div>
  );
}
