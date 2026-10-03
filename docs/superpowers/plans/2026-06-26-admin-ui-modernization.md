# Admin UI Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin the Duar admin SPA onto shadcn/ui with a light/dark theme system and a theme-invariant red brand rail, reading as an intentional security console — all ~19 pages.

**Architecture:** shadcn/ui primitives (Radix + existing Tailwind 4) in `src/components/ui/`. A native theme provider toggles `.dark` on `<html>`; semantic CSS-var tokens flip the canvas while the sidebar stays Duar red in both themes. Shared components (`Layout`, `DataTable`, badges, modals, `SearchInput`) keep their **public APIs** and are re-skinned once — that propagates to most pages. Pages are then converted by a fixed token-map recipe.

**Tech Stack:** React 19, Vite 7, Tailwind 4 (`@tailwindcss/vite`, CSS-first), shadcn/ui (new-york), lucide-react, sonner, @fontsource IBM Plex.

## Global Constraints

- **Scope:** `admin/` only. No `service/` changes. **Never `git add`** `service/src/services/role_service.py` or `service/tests/test_register_actions.py` (user WIP).
- **No behavioral changes:** pure presentation. Keep every shared component's public API identical so call sites don't change.
- **Brand red hue fixed:** `#f43737`. Derive shades for states only; never re-hue.
- **Sidebar is theme-invariant** (red in both light and dark).
- **Fonts self-hosted** via `@fontsource` (no external CDN).
- **No new test framework.** This is a presentation refactor with no unit-testable logic. The per-task verification gate is: `npm run build` (tsc -b + vite) **and** `npm run lint` both green. Visual screenshots (both themes) at wave checkpoints. (Adding Vitest here would be YAGNI.)
- **Commit after each task.**

### Token map (the page-conversion recipe — used by every page task)

Replace hardcoded literals with semantic tokens so both themes work:

| Current literal | Replace with |
|---|---|
| `bg-zinc-950` (page bg) | remove (body handles it) |
| `bg-zinc-900` (cards) | `bg-card` |
| `bg-zinc-900/50`, `bg-zinc-900/40` | `bg-muted/50` |
| `bg-zinc-800` (inputs, chips) | `bg-muted` |
| `bg-zinc-800/40`, `hover:bg-zinc-800/40` | `hover:bg-muted/50` |
| `bg-zinc-700` (avatars/dots) | `bg-muted` |
| `border-zinc-800`, `border-zinc-700` | `border-border` |
| `divide-zinc-800`, `divide-zinc-800/50` | `divide-border` |
| `text-zinc-100`, `text-zinc-200` | `text-foreground` |
| `text-zinc-300` | `text-foreground` (secondary → `text-muted-foreground`) |
| `text-zinc-400`, `text-zinc-500`, `text-zinc-600` | `text-muted-foreground` |
| `placeholder:text-zinc-500` | `placeholder:text-muted-foreground` |
| `focus:ring-zinc-600` | `focus:ring-ring` |
| primary button `bg-zinc-100 text-zinc-900 hover:bg-white` | `<Button>` (brand-red primary) |
| raw `<input>` / `<button>` | `<Input>` / `<Button>` where sensible |
| status text colors (`text-emerald-400`, `text-red-400`…) | keep via `StatusBadge`/`RoleBadge`/`VisibilityBadge`, or add `text-emerald-700 dark:text-emerald-400` light variants |

---

## Task 1: Foundation — shadcn setup, tokens, fonts, theme

**Files:**
- Create: `admin/components.json`, `admin/src/lib/utils.ts`, `admin/src/lib/theme.tsx`
- Modify: `admin/package.json` (deps via installs), `admin/vite.config.ts`, `admin/tsconfig.json`, `admin/tsconfig.app.json`, `admin/eslint.config.js`, `admin/index.html`, `admin/src/app.css`, `admin/src/main.tsx`, `admin/src/App.tsx`

**Interfaces:**
- Produces: `cn(...)` from `@/lib/utils`; `<ThemeProvider>` + `useTheme(): { theme, toggle }` from `@/lib/theme`; semantic Tailwind utilities (`bg-card`, `text-muted-foreground`, `border-border`, `bg-primary`, `bg-sidebar`, `bg-sidebar-active`, `bg-sidebar-hover`, `text-sidebar-muted`, `font-mono`); shadcn primitives under `@/components/ui/*`.

- [ ] **Step 1: Install deps** (network required)

```bash
cd admin
npm i lucide-react class-variance-authority clsx tailwind-merge
npm i tw-animate-css @fontsource/ibm-plex-sans @fontsource/ibm-plex-mono
```

- [ ] **Step 2: Add the `@/` path alias**

`vite.config.ts` — add import + `resolve.alias`:

```ts
import path from "path";
// ...
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { /* unchanged */ },
});
```

`tsconfig.json` — add `compilerOptions` (currently only has `files`/`references`):

```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ],
  "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["./src/*"] } }
}
```

`tsconfig.app.json` — add to `compilerOptions`: `"baseUrl": ".", "paths": { "@/*": ["./src/*"] }`.

- [ ] **Step 3: Create `components.json`**

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": { "config": "", "css": "src/app.css", "baseColor": "zinc", "cssVariables": true, "prefix": "" },
  "iconLibrary": "lucide",
  "aliases": { "components": "@/components", "utils": "@/lib/utils", "ui": "@/components/ui", "lib": "@/lib", "hooks": "@/hooks" }
}
```

- [ ] **Step 4: Create `src/lib/utils.ts`**

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 5: Write `src/app.css` with tokens + fonts**

Replace the single `@import "tailwindcss";` line with the full token sheet (fonts first — `@import` must precede other rules):

```css
@import "@fontsource/ibm-plex-sans/400.css";
@import "@fontsource/ibm-plex-sans/500.css";
@import "@fontsource/ibm-plex-sans/600.css";
@import "@fontsource/ibm-plex-mono/400.css";
@import "@fontsource/ibm-plex-mono/500.css";
@import "tailwindcss";
@import "tw-animate-css";

@custom-variant dark (&:is(.dark *));

:root {
  --radius: 0.375rem;
  --background: #ffffff; --foreground: #18181b;
  --card: #ffffff; --card-foreground: #18181b;
  --popover: #ffffff; --popover-foreground: #18181b;
  --primary: #f43737; --primary-foreground: #ffffff;
  --secondary: #f4f4f5; --secondary-foreground: #18181b;
  --muted: #f4f4f5; --muted-foreground: #71717a;
  --accent: #f4f4f5; --accent-foreground: #18181b;
  --destructive: #e01e1e; --destructive-foreground: #ffffff;
  --border: #e4e4e7; --input: #e4e4e7; --ring: #f43737;
  --sidebar: #f43737; --sidebar-foreground: #ffffff;
  --sidebar-active: #bd1414; --sidebar-hover: #e01e1e; --sidebar-muted: #ffd1d1;
}

.dark {
  --background: #0a0a0b; --foreground: #fafafa;
  --card: #131316; --card-foreground: #fafafa;
  --popover: #131316; --popover-foreground: #fafafa;
  --primary: #f43737; --primary-foreground: #ffffff;
  --secondary: #1f1f23; --secondary-foreground: #fafafa;
  --muted: #1f1f23; --muted-foreground: #a1a1aa;
  --accent: #27272a; --accent-foreground: #fafafa;
  --destructive: #f43737; --destructive-foreground: #ffffff;
  --border: #27272a; --input: #27272a; --ring: #f43737;
  --sidebar: #f43737; --sidebar-foreground: #ffffff;
  --sidebar-active: #bd1414; --sidebar-hover: #e01e1e; --sidebar-muted: #ffd1d1;
}

@theme inline {
  --color-background: var(--background); --color-foreground: var(--foreground);
  --color-card: var(--card); --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover); --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary); --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary); --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted); --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent); --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive); --color-destructive-foreground: var(--destructive-foreground);
  --color-border: var(--border); --color-input: var(--input); --color-ring: var(--ring);
  --color-sidebar: var(--sidebar); --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-active: var(--sidebar-active); --color-sidebar-hover: var(--sidebar-hover);
  --color-sidebar-muted: var(--sidebar-muted);
  --font-sans: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
  --radius-sm: calc(var(--radius) - 2px); --radius-md: var(--radius); --radius-lg: calc(var(--radius) + 2px);
}

@layer base {
  * { border-color: var(--color-border); }
  body { background-color: var(--color-background); color: var(--color-foreground); font-family: var(--font-sans); }
}
```

- [ ] **Step 6: Pre-paint theme script + clean body in `index.html`**

In `<head>` (before the module script), add:

```html
<script>
  (function () {
    try {
      var s = localStorage.getItem("theme");
      var dark = s ? s === "dark" : !window.matchMedia("(prefers-color-scheme: light)").matches;
      document.documentElement.classList.toggle("dark", dark);
      document.documentElement.style.colorScheme = dark ? "dark" : "light";
    } catch (e) {}
  })();
</script>
```

Change `<body class="bg-zinc-950 text-zinc-100">` → `<body>` (base layer styles it now).

- [ ] **Step 7: Create `src/lib/theme.tsx`**

```tsx
import { createContext, useContext, useEffect, useState } from "react";

type Theme = "light" | "dark";
const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({
  theme: "dark",
  toggle: () => {},
});

function read(): Theme {
  const saved = localStorage.getItem("theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(read);
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.style.colorScheme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  return (
    <ThemeContext.Provider value={{ theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }}>
      {children}
    </ThemeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export const useTheme = () => useContext(ThemeContext);
```

- [ ] **Step 8: Wrap app + wire Toaster to theme**

`main.tsx`: import `ThemeProvider` from `./lib/theme`, wrap `<App />` (inside `BrowserRouter`).
`App.tsx`: replace `<Toaster theme="dark" .../>` with theme-aware:

```tsx
import { useTheme } from "./lib/theme";
// inside App, above return: const { theme } = useTheme();
<Toaster theme={theme} position="bottom-right" richColors />
```

- [ ] **Step 9: Silence react-refresh on generated ui files**

`eslint.config.js` — append to the config array:

```js
  {
    files: ['src/components/ui/**'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
```

- [ ] **Step 10: Add shadcn primitives** (network required)

```bash
cd admin
npx shadcn@latest add button input label textarea select checkbox switch \
  dialog alert-dialog dropdown-menu tabs tooltip table badge card skeleton separator
```

If a prompt asks to overwrite `app.css` or `components.json`, choose **No** (we own them).

- [ ] **Step 11: Verify**

```bash
cd admin && npm run build && npm run lint
```

Expected: both exit 0. Common fixes: `verbatimModuleSyntax` may require `import { type X }` in a generated file; `noUnusedParameters` may flag a generated stub (prefix `_`).

- [ ] **Step 12: Commit**

```bash
git add admin/components.json admin/src/lib admin/src/components/ui admin/vite.config.ts \
  admin/tsconfig.json admin/tsconfig.app.json admin/eslint.config.js admin/index.html \
  admin/src/app.css admin/src/main.tsx admin/src/App.tsx admin/package.json admin/package-lock.json
git commit -m "feat(admin): shadcn foundation, IBM Plex, light/dark theme tokens"
```

---

## Task 2: Shared layer — Layout (brand rail + theme toggle)

**Files:** Modify `admin/src/components/Layout.tsx`
**Interfaces:** Consumes `useTheme`, `bg-sidebar*` tokens, lucide icons. Public API (`Layout({children})`) unchanged.

- [ ] **Step 1: Rewrite `Layout.tsx`** — swap hardcoded reds for `bg-sidebar*` tokens, hand-coded SVG paths for lucide, add a Sun/Moon toggle in the footer.

```tsx
import { NavLink } from "react-router-dom";
import {
  Activity, AppWindow, Boxes, Building2, Cpu, LayoutDashboard, LogOut,
  Moon, Network, Server, Settings, ShieldCheck, Sun, Users, Zap,
} from "lucide-react";
import { adminLogout } from "../api/client";
import { useAdmin } from "./AuthGuard";
import { useTheme } from "../lib/theme";

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
  { to: "/system", label: "System", Icon: Cpu },
  { to: "/settings", label: "Settings", Icon: Settings },
] as const;

export function Layout({ children }: { children: React.ReactNode }) {
  const admin = useAdmin();
  const { theme, toggle } = useTheme();
  const handleLogout = async () => { await adminLogout(); window.location.href = "/"; };

  return (
    <div className="flex h-screen">
      <aside className="w-56 shrink-0 flex flex-col bg-sidebar text-sidebar-foreground">
        <div className="h-14 flex items-center gap-2.5 px-4 border-b border-white/15">
          <img src="/logo.png" alt="Duar" className="h-8 w-auto shrink-0" />
          <span className="text-sm font-bold tracking-wider uppercase whitespace-nowrap">Duar</span>
        </div>
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive ? "bg-sidebar-active text-white" : "text-white/85 hover:text-white hover:bg-sidebar-hover"
                }`}>
              <Icon className="w-4 h-4 shrink-0" strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-3 py-3 border-t border-white/15 space-y-2">
          <button onClick={toggle}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-white/85 hover:text-white hover:bg-sidebar-hover transition-colors">
            {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <div className="flex items-center justify-between px-1">
            <div className="min-w-0">
              <p className="truncate text-sm">{admin.name}</p>
              <p className="truncate text-xs text-sidebar-muted">{admin.email}</p>
            </div>
            <button onClick={handleLogout} title="Sign out"
              className="shrink-0 rounded p-1 text-sidebar-muted hover:bg-sidebar-hover hover:text-white">
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
```

- [ ] **Step 2: Verify** `npm run build && npm run lint` → exit 0.
- [ ] **Step 3: Commit** `git add admin/src/components/Layout.tsx && git commit -m "feat(admin): token-driven brand rail + theme toggle + lucide nav"`

---

## Task 3: Shared layer — badges, DataTable, SearchInput

**Files:** Modify `Badge.tsx`, `DataTable.tsx`, `SearchInput.tsx`. Keep all public APIs.

- [ ] **Step 1: `Badge.tsx`** — keep `RoleBadge`/`StatusBadge({active})`/`VisibilityBadge` signatures; make colors legible in both themes (add `*-700` light text on `/10` bg, `dark:*-400` on `/15`).

```tsx
const ROLE_COLORS: Record<string, string> = {
  owner: "bg-amber-500/10 text-amber-700 dark:text-amber-400 ring-amber-500/20",
  admin: "bg-purple-500/10 text-purple-700 dark:text-purple-400 ring-purple-500/20",
  editor: "bg-blue-500/10 text-blue-700 dark:text-blue-400 ring-blue-500/20",
  viewer: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 ring-zinc-500/20",
};
export function RoleBadge({ role }: { role: string }) {
  const color = ROLE_COLORS[role] ?? ROLE_COLORS.viewer;
  return <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ring-1 ring-inset ${color}`}>{role}</span>;
}
export function StatusBadge({ active }: { active: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ring-1 ring-inset ${
      active ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 ring-emerald-500/20"
             : "bg-red-500/10 text-red-700 dark:text-red-400 ring-red-500/20"}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${active ? "bg-emerald-500" : "bg-red-500"}`} />
      {active ? "Active" : "Inactive"}
    </span>
  );
}
export function VisibilityBadge({ visibility }: { visibility: string }) {
  const isWorkspace = visibility === "workspace";
  return <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ring-1 ring-inset ${
    isWorkspace ? "bg-blue-500/10 text-blue-700 dark:text-blue-400 ring-blue-500/20"
                : "bg-orange-500/10 text-orange-700 dark:text-orange-400 ring-orange-500/20"}`}>{visibility}</span>;
}
```

- [ ] **Step 2: `DataTable.tsx`** — keep the generic `Column<T>`/`Props<T>` API; apply the token map: header `bg-muted/50 text-muted-foreground`, container `border-border`, rows `divide-border`, hover `hover:bg-muted/50`, empty `text-muted-foreground`. (No tanstack-table.)

- [ ] **Step 3: `SearchInput.tsx`** — keep `{value,onChange,placeholder}` + 300ms debounce; render shadcn `<Input>` wrapped with a lucide `<Search>` icon:

```tsx
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";

export function SearchInput({ value, onChange, placeholder = "Search..." }: {
  value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => { const t = setTimeout(() => onChange(local), 300); return () => clearTimeout(t); }, [local]);
  return (
    <div className="relative w-64">
      <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
      <Input value={local} onChange={(e) => setLocal(e.target.value)} placeholder={placeholder} className="pl-8 h-9" />
    </div>
  );
}
```

- [ ] **Step 4: Verify** `npm run build && npm run lint` → exit 0.
- [ ] **Step 5: Commit** `git add admin/src/components/Badge.tsx admin/src/components/DataTable.tsx admin/src/components/SearchInput.tsx && git commit -m "feat(admin): theme-aware badges, table, search"`

---

## Task 4: Shared layer — Modal→Dialog, ConfirmModal→AlertDialog

**Files:** Modify `Modal.tsx`, `ConfirmModal.tsx`, `CsvImportModal.tsx`. Keep public APIs.

- [ ] **Step 1: `Modal.tsx`** — adapter over shadcn `Dialog`, same `{open,onClose,title,children}` API:

```tsx
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function Modal({ open, onClose, title, children }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: `ConfirmModal.tsx`** — rebuild on shadcn `AlertDialog`, identical Props (incl. `confirmInput`/`danger`/`isPending`). Cancel = `AlertDialogCancel`; confirm = `<Button variant={danger ? "destructive" : "default"} disabled={disabled}>`; keep the type-to-confirm `<Input>`. Use the token map for the message text (`text-muted-foreground`).

- [ ] **Step 3: `CsvImportModal.tsx`** — it composes `Modal`, so it inherits the new look. Apply the token map to its inner markup (inputs → `<Input>`, buttons → `<Button>`, `text-zinc-*` → tokens).

- [ ] **Step 4: Verify** `npm run build && npm run lint` → exit 0.
- [ ] **Step 5: Commit** `git add admin/src/components/Modal.tsx admin/src/components/ConfirmModal.tsx admin/src/components/CsvImportModal.tsx && git commit -m "feat(admin): Dialog/AlertDialog-based modals"`

---

## Task 5: Shared layer — ErrorBoundary, AuthGuard loading, Login

**Files:** Modify `ErrorBoundary.tsx`, `AuthGuard.tsx`, `pages/Login.tsx`. Apply token map.

- [ ] **Step 1:** `AuthGuard.tsx` loading state — `bg-zinc-950` → `bg-background`, spinner `border-zinc-600/300` → `border-border border-t-primary`.
- [ ] **Step 2:** `ErrorBoundary.tsx` — re-skin error fallback with `bg-card border-border`, destructive accents via `text-destructive`.
- [ ] **Step 3:** `Login.tsx` — wrap the form in shadcn `Card`; inputs → `<Input>`, submit → `<Button>` (brand red); token map for the rest; keep auth logic untouched.
- [ ] **Step 4: Verify** `npm run build && npm run lint` → exit 0.
- [ ] **Step 5: Commit** `git add admin/src/components/ErrorBoundary.tsx admin/src/components/AuthGuard.tsx admin/src/pages/Login.tsx && git commit -m "feat(admin): re-skin error/loading/login"`

### ✅ CHECKPOINT — screenshot both themes before mass page conversion
Run `make admin` (:9004). Screenshot Dashboard (still partly un-converted is fine) in **light and dark**. Confirm: red rail identical in both, canvas flips, fonts are Plex, toggle persists across reload. **Show the user. Get a thumbs-up before Tasks 6–8.**

---

## Task 6: List pages (apply the token-map recipe per page)

**Files (one commit per page or per small batch):** `pages/Users.tsx`, `Workspaces.tsx`, `Organizations.tsx`, `ClientApps.tsx`, `ServiceApps.tsx`, `Realms.tsx`, `Permissions.tsx`, `ServiceActions.tsx`, `Activity.tsx`

**Per-page procedure:**
- [ ] Read the page. Apply the **Token map** (Global Constraints) to every hardcoded `zinc-*`/literal.
- [ ] Swap raw `<button>` primary actions → `<Button>`; raw `<input>` → `<Input>`/`SearchInput`; inline dropdowns → shadcn `DropdownMenu`; tab strips → shadcn `Tabs`.
- [ ] Wrap identifiers (IDs, slugs, `service_name`, `client_id`) in `font-mono`; add copy-to-clipboard (lucide `Copy` + `navigator.clipboard.writeText`, `toast.success("Copied")`) on keys/IDs.
- [ ] `npm run build && npm run lint` → exit 0. Commit: `git add admin/src/pages/<Page>.tsx && git commit -m "feat(admin): re-skin <Page>"`.

These 9 pages are independent — parallelizable across subagents (one page each).

---

## Task 7: Detail pages (same recipe)

**Files:** `pages/UserDetail.tsx`, `WorkspaceDetail.tsx`, `OrganizationDetail.tsx`, `ClientAppDetail.tsx`, `ServiceAppDetail.tsx`, `RealmDetail.tsx`

- [ ] Same per-page procedure as Task 6. Detail pages lean on `Tabs`, definition lists (token map), `font-mono` + copy on IDs/keys, and `ConfirmModal` for destructive actions (already re-skinned).
- [ ] Build + lint + commit per page. Independent — parallelizable.

---

## Task 8: Special pages — Dashboard, SystemHealth, Settings

**Files:** `pages/Dashboard.tsx`, `pages/SystemHealth.tsx`, `pages/Settings.tsx`

- [ ] **Dashboard:** stat cards → `bg-card border-border` (or shadcn `Card`); lists `divide-border`, hover `hover:bg-muted/50`; the avatar `bg-zinc-700` → `bg-muted`; keep `tabular-nums`. (`timeAgo` uses `Date.now()` — leave it; this is real runtime browser code, not the workflow sandbox.)
- [ ] **SystemHealth:** status indicators via the fixed status palette / `StatusBadge`; `bg-emerald/red` dots get light-mode variants.
- [ ] **Settings:** toggles → shadcn `Switch`; inputs → `<Input>`; section cards → `Card`.
- [ ] Build + lint + commit per page.

---

## Task 9: Final verification

- [ ] `cd admin && npm run build` → exit 0.
- [ ] `cd admin && npm run lint` → exit 0.
- [ ] `grep -rn "zinc-\|bg-zinc\|text-zinc" admin/src/pages admin/src/components --include=*.tsx | grep -v "/ui/"` → only intentional neutrals remain (review each hit).
- [ ] `make admin` — screenshot Dashboard + one list + one detail page in **both** themes; verify red rail constant, canvas flips, mono on IDs, no contrast failures.
- [ ] Final commit if any cleanup: `git commit -m "chore(admin): final theme/contrast cleanup"`.

## Self-review notes (coverage)

- shadcn foundation, fonts, tokens, theme, both-theme canvas, red-invariant rail → Task 1–2. ✓
- Component mapping (Modal→Dialog, ConfirmModal→AlertDialog, DataTable re-skin, lucide nav, StatusBadge) → Tasks 2–5. ✓
- All ~19 pages → Tasks 6–8. ✓
- Mono IDs + copy-to-clipboard → Tasks 6–7 procedure. ✓
- Verification gate (build+lint+visual, no new test fw) → every task + Task 9. ✓
- Guardrails (no `service/`, don't touch role_service WIP, command palette deferred) → Global Constraints. ✓
