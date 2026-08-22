import { NavLink } from "react-router-dom";
import {
  Activity,
  AppWindow,
  BarChart3,
  Boxes,
  Building2,
  Cpu,
  Globe,
  LayoutDashboard,
  LogOut,
  Moon,
  Network,
  Server,
  Settings,
  ShieldCheck,
  Sun,
  Users,
  Zap,
} from "lucide-react";
import { adminLogout } from "../api/client";
import { useAdmin } from "./AuthGuard";
import { useTheme } from "../lib/theme";
import { BASE_PATH } from "../lib/base";

const NAV = [
  { to: "/", label: "Dashboard", Icon: LayoutDashboard },
  { to: "/users", label: "Users", Icon: Users },
  { to: "/workspaces", label: "Workspaces", Icon: Building2 },
  { to: "/organizations", label: "Organizations", Icon: Network },
  { to: "/permissions", label: "Permissions", Icon: ShieldCheck },
  { to: "/service-actions", label: "Actions", Icon: Zap },
  { to: "/client-apps", label: "Login Apps", Icon: AppWindow },
  { to: "/service-apps", label: "Services", Icon: Server },
  { to: "/realms", label: "Realms", Icon: Boxes },
  { to: "/activity", label: "Activity", Icon: Activity },
  { to: "/insights", label: "Insights", Icon: Globe },
  { to: "/usage", label: "Usage", Icon: BarChart3 },
  { to: "/system", label: "System", Icon: Cpu },
  { to: "/settings", label: "Settings", Icon: Settings },
] as const;

export function Layout({ children }: { children: React.ReactNode }) {
  const admin = useAdmin();
  const { theme, toggle } = useTheme();
  const handleLogout = async () => {
    await adminLogout();
    window.location.href = BASE_PATH;
  };

  return (
    <div className="flex h-screen">
      <aside className="w-56 shrink-0 flex flex-col bg-sidebar text-sidebar-foreground">
        <div className="h-14 flex items-center gap-2.5 px-4 border-b border-white/15">
          <img src="logo.png" alt="Duar" className="h-8 w-auto shrink-0" />
          <span className="text-sm font-bold tracking-wider uppercase whitespace-nowrap">
            Duar
          </span>
        </div>
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? "bg-sidebar-active text-white"
                    : "text-white/85 hover:text-white hover:bg-sidebar-hover"
                }`
              }
            >
              <Icon className="w-4 h-4 shrink-0" strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-3 py-3 border-t border-white/15 space-y-2">
          <button
            onClick={toggle}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-white/85 hover:text-white hover:bg-sidebar-hover transition-colors"
          >
            {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <div className="flex items-center justify-between px-1">
            <div className="min-w-0">
              <p className="truncate text-sm">{admin.name}</p>
              <p className="truncate text-xs text-sidebar-muted">{admin.email}</p>
            </div>
            <button
              onClick={handleLogout}
              title="Sign out"
              className="shrink-0 rounded p-1 text-sidebar-muted hover:bg-sidebar-hover hover:text-white"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-auto bg-background">
        <div className="max-w-6xl mx-auto px-6 py-6">{children}</div>
      </main>
    </div>
  );
}
