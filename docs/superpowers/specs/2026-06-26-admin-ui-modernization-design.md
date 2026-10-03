# Admin UI Modernization — Design

**Date:** 2026-06-26
**Scope:** `admin/` only. Pure presentation. No backend/API/auth changes.
**Status:** Approved (design), pending spec review.

## Goal

Modernize the Duar admin SPA so it reads as an intentional, professional
**security console** — not a templated dashboard. Adopt a real component
foundation, add a light/dark theme system, and keep the Duar red as the
constant brand. Convert all ~19 pages (full sweep).

## Decisions (from brainstorming)

- **Foundation:** shadcn/ui, heavily re-themed. Radix primitives + the existing
  Tailwind 4. We own the component code (copy-paste), no new UI runtime.
- **Direction:** keep the bold red sidebar as the dominant, **theme-invariant**
  brand surface. The red hue (`#f43737`) does not change. Add **both light and
  dark** themes for the canvas, with a toggle.
- **Sweep:** all pages, but leveraged through the shared component layer (most
  pages compose a few shared components — re-skinning those modernizes the bulk
  for free, then per-page polish).
- **Command palette (⌘K):** deferred (YAGNI). Easy to add later.
- **Typeface:** IBM Plex Sans + IBM Plex Mono, self-hosted (no external CDN).

## The non-generic identity

The distinctive look is **a constant red rail against a flipping (light/dark)
canvas**. The library is just primitives; the identity comes from:

- **Red rail** — Duar red, identical in both themes; the brand anchor.
- **Type** — IBM Plex Sans/Mono (enterprise/security signal, not Inter).
- **Mono on every identifier** — IDs, slugs, `service_name`, `client_id`,
  JWTs/keys, with copy-to-clipboard on keys & IDs.
- **Density** — compact controls, `tabular-nums` tables. Admin is data-dense.
- **Fixed status semantics** — one `StatusBadge`, one palette, everywhere.

## Stack changes

New deps (all standard shadcn): `lucide-react`, `class-variance-authority`,
`clsx`, `tailwind-merge`, `tw-animate-css`, the per-component Radix packages,
and `@fontsource/ibm-plex-sans` + `@fontsource/ibm-plex-mono`. **Keep `sonner`**
(shadcn's toast already is sonner). No `@tanstack/react-table` — current tables
are simple; YAGNI.

- Primitives land in `src/components/ui/`.
- `cn()` helper in `src/lib/utils.ts`.
- shadcn init in Tailwind-v4 mode (writes `@theme inline` + CSS-var tokens into
  `app.css`, uses `tw-animate-css`). React 19 supported.

## Design tokens

Defined as CSS variables in `app.css`: `:root` (light) and `.dark`. Final hex
values tuned against screenshots during implementation; starting point:

**Brand red ramp** (hue fixed; shades derived for states)
`50 #fff1f1 · 100 #ffdfdf · 200 #ffc5c5 · 300 #ff9b9b · 400 #fb6a6a ·`
`500 #f43737 (brand) · 600 #e01e1e · 700 #bd1414 · 800 #9c1414 · 900 #821818`

**Sidebar (theme-invariant — mirrors today's Layout exactly)**
`--sidebar #f43737 · --sidebar-active #bd1414 (red-700) · --sidebar-hover #e01e1e (red-600) · --sidebar-fg #ffffff · --sidebar-muted #ffd1d1`

**Canvas — light**
`--background #ffffff · --foreground #18181b · --card #ffffff · --muted #f4f4f5 ·`
`--muted-foreground #71717a · --border #e4e4e7 · --input #e4e4e7 ·`
`--primary #f43737 · --primary-foreground #ffffff · --ring #f43737 ·`
`--destructive #e01e1e`

**Canvas — dark**
`--background #0a0a0b · --foreground #fafafa · --card #131316 · --muted #1f1f23 ·`
`--muted-foreground #a1a1aa · --border #27272a · --input #27272a ·`
`--primary #f43737 · --primary-foreground #ffffff · --ring #f43737 ·`
`--destructive #f43737`

**Status palette** (StatusBadge): active/healthy → green · inactive/revoked →
zinc · error/expired → red · warning → amber · pending/info → blue.

**Type / radius:** `--font-sans` IBM Plex Sans, `--font-mono` IBM Plex Mono,
`--radius 0.375rem`.

## Theme system (native — no theme library)

- `src/lib/theme.ts`: `useTheme()` → `{ theme, setTheme, toggle }`. Reads
  `localStorage.theme` else `prefers-color-scheme`; effect toggles `.dark` on
  `<html>` and sets `style.colorScheme`.
- `index.html`: ~5-line inline script before first paint to set the class (no
  theme flash). No `next-themes` (this is Vite, not Next).
- Toggle control (Sun/Moon) in the sidebar footer next to logout.

## Component foundation

`shadcn add`: button, input, label, textarea, select, checkbox, switch, dialog,
alert-dialog, dropdown-menu, tabs, tooltip, table, badge, card, skeleton,
separator, sonner. (Command/popover/avatar added only if a page needs them.)

**Existing → new mapping**

| Current | Becomes |
|---|---|
| `components/Modal.tsx` | shadcn **Dialog** |
| `components/ConfirmModal.tsx` | shadcn **AlertDialog** (keep the "type the name to confirm" destructive variant) |
| `components/Badge.tsx` (`StatusBadge`) | shadcn **Badge** + `StatusBadge` wrapper (fixed palette) |
| `components/DataTable.tsx` | re-skin onto shadcn **Table** primitives; keep existing logic |
| `components/SearchInput.tsx` | shadcn **Input** + lucide `Search` |
| `components/CsvImportModal.tsx` | Dialog-based |
| `components/ErrorBoundary.tsx` | re-skin (Card/Alert) |
| `components/Layout.tsx` | red-rail tokens + **lucide** nav icons + theme toggle |
| `pages/Login.tsx` | re-skin (Card + brand) |

Nav icons move from hand-coded SVG `d=""` paths to lucide-react.

## Migration waves (full sweep)

0. **Foundation** — deps, shadcn init, tokens + fonts in `app.css`, theme hook,
   `index.html` script, `utils.ts`, core primitives.
1. **Shared layer** — Layout, DataTable, Badge/StatusBadge, Modal→Dialog,
   ConfirmModal→AlertDialog, SearchInput, CsvImportModal, ErrorBoundary, Login.
   → **Checkpoint: screenshot both themes, show user before mass conversion.**
2. **List pages** — Users, Workspaces, Organizations, ClientApps, ServiceApps,
   Realms, Permissions, ServiceActions, Activity.
3. **Detail pages** — UserDetail, WorkspaceDetail, OrganizationDetail,
   ClientAppDetail, ServiceAppDetail, RealmDetail.
4. **Specials** — Dashboard, SystemHealth, Settings.
5. **Verify** — `npm run build` (tsc + vite) + `npm run lint` green; screenshot
   Dashboard + a list + a detail page in both themes.

## Verification

- `cd admin && npm run build` and `npm run lint` must pass.
- `make admin` (:9004) — visual check of both themes.

## Out of scope / guardrails

- No `service/` changes. **Do not touch** `service/src/services/role_service.py`
  (user's uncommitted WIP) or `service/tests/test_register_actions.py`.
- No new features, no auth/flow changes, no API changes.
- Command palette deferred.
