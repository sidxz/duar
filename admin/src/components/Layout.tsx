import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { Dialog } from "radix-ui";
import {
  Activity, AppWindow, BarChart3, Boxes, Building2, Cpu,
  Globe, LayoutDashboard, LogOut, Menu, Moon, Network, Plug, Server,
  Settings, ShieldCheck, Sun, Users, X, Zap,
} from "lucide-react";
import { toast } from "sonner";
import { adminLogout } from "../api/client";
import { useAdmin } from "./AuthGuard";
import { useTheme } from "../lib/theme";
import { BASE_PATH } from "../lib/base";
import { Button } from "./ui/button";

const NAV_GROUPS = [
  { label: "Overview", items: [
    { to: "/", label: "Dashboard", Icon: LayoutDashboard },
  ] },
  { label: "Identity", items: [
    { to: "/users", label: "Users", Icon: Users },
    { to: "/workspaces", label: "Workspaces", Icon: Building2 },
    { to: "/organizations", label: "Organizations", Icon: Network },
  ] },
  { label: "Access & integrations", items: [
    { to: "/permissions", label: "Permissions", Icon: ShieldCheck },
    { to: "/service-actions", label: "Actions", Icon: Zap },
    { to: "/client-apps", label: "Login Apps", Icon: AppWindow },
    { to: "/service-apps", label: "Services", Icon: Server },
    { to: "/mcp", label: "Duar MCP", Icon: Plug },
    { to: "/realms", label: "Realms", Icon: Boxes },
  ] },
  { label: "Monitor", items: [
    { to: "/activity", label: "Activity", Icon: Activity },
    { to: "/insights", label: "Insights", Icon: Globe },
    { to: "/usage", label: "Usage", Icon: BarChart3 },
    { to: "/system", label: "System", Icon: Cpu },
    { to: "/settings", label: "Settings", Icon: Settings },
  ] },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const admin = useAdmin();
  const { theme, toggle } = useTheme();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  useEffect(() => { mainRef.current?.scrollTo({ top: 0 }); }, [pathname]);
  const current = NAV_GROUPS.flatMap((group) => group.items).find(
    (item) => item.to === "/" ? pathname === "/" : pathname === item.to || pathname.startsWith(`${item.to}/`),
  );

  const handleLogout = async () => {
    setSigningOut(true);
    try {
      await adminLogout();
      window.location.href = BASE_PATH;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Couldn’t sign out. Try again.");
      setSigningOut(false);
    }
  };

  const sidebar = (
    <>
      <Link
        to="/"
        onClick={() => setMenuOpen(false)}
        className="flex h-16 shrink-0 items-center gap-3 border-b border-white/10 px-5 text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/40"
      >
        <img src={`${BASE_PATH}mark.png`} alt="" className="size-8 rounded-lg shadow-[0_0_0_1px_rgb(255_255_255/0.08)]" />
        <span className="text-[15px] font-semibold tracking-tight">Duar.io</span>
      </Link>
      <nav aria-label="Main navigation" className="sidebar-nav min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-sidebar-muted">{group.label}</p>
            <div className="space-y-0.5">
              {group.items.map(({ to, label, Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === "/"}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    `group flex items-center gap-3 rounded-md px-3 py-1.5 text-sm transition-colors ${isActive
                      ? "bg-sidebar-active text-white shadow-[inset_3px_0_0_var(--primary)]"
                      : "text-sidebar-muted hover:bg-sidebar-hover hover:text-white"}`
                  }
                >
                  <Icon className="size-4 shrink-0" strokeWidth={1.75} />
                  {label}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="flex shrink-0 items-center gap-3 border-t border-white/10 p-4">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{admin.name}</p>
          <p className="truncate text-xs text-sidebar-muted" title={admin.email}>{admin.email}</p>
        </div>
        <button onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} className="hidden rounded-md p-2 text-sidebar-muted hover:bg-sidebar-hover hover:text-white lg:block">
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>
        <button onClick={handleLogout} disabled={signingOut} aria-label="Sign out" title="Sign out" className="rounded-md p-2 text-sidebar-muted hover:bg-sidebar-hover hover:text-white disabled:opacity-50">
          <LogOut className="size-4" />
        </button>
      </div>
    </>
  );

  return (
    <div className="flex h-dvh overflow-hidden">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-card focus:p-3">Skip to content</a>
      <aside className="hidden w-56 shrink-0 flex-col bg-sidebar text-sidebar-foreground lg:flex">{sidebar}</aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-4 sm:px-8 lg:hidden">
          <div className="flex min-w-0 items-center gap-3">
            <Dialog.Root open={menuOpen} onOpenChange={setMenuOpen}>
              <Dialog.Trigger asChild>
                <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation"><Menu className="size-5" /></Button>
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
                <Dialog.Content aria-describedby={undefined} className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col bg-sidebar text-sidebar-foreground shadow-xl">
                  <Dialog.Title className="sr-only">Navigation</Dialog.Title>
                  <Dialog.Close className="absolute right-3 top-3 rounded-md p-2 text-white hover:bg-black/10" aria-label="Close navigation"><X className="size-4" /></Dialog.Close>
                  {sidebar}
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
            <span className="text-sm font-medium">{current?.label ?? "Duar.io"}</span>
          </div>
          <Button variant="ghost" size="icon" onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </Button>
        </header>
        <main ref={mainRef} id="main-content" tabIndex={-1} className="min-h-0 flex-1 overflow-auto bg-background outline-none">
          <div className="app-content">{children}</div>
        </main>
      </div>
    </div>
  );
}
