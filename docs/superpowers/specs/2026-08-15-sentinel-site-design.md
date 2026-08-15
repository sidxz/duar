# Sentinel product site (sentinel-auth.com) — Design

> Approved 2026-08-15. A single-page marketing/product site for Sentinel Auth,
> built in the visual language of `sidxz/docustore-site` (its *current* white/ink
> "LlamaIndex-style" system, not the rejected cream+serif v1), re-skinned to the
> Sentinel brand. Lives in a **new repo `sidxz/sentinel-site`**; this spec sits in
> identity-service because the site repo does not exist yet.

## Decisions (from brainstorm)

- **Scope:** landing page only. Navbar/footer link *out* to the existing MkDocs
  docs (`https://docs.sentinel-auth.com/`) and GitHub. No fumadocs, no blog, no
  feature sub-pages. Docs migration is a separate future project.
- **Positioning:** "the authorization layer between your IdP and your app" — bring
  your own IdP (Google / GitHub / Entra ID / any OIDC); Sentinel adds workspaces,
  roles, per-resource permissions, SDKs. Not pitched as an Auth0/Keycloak
  replacement; pitched as what you build *after* login.
- **Identity:** white/ink monochrome base, **Sentinel red `#f43737` as the single
  rationed accent** (replaces docustore's spectrum gradient wherever it appears).
  No yellow. New minimal line-art SVG mark; existing PNG logo untouched for
  docs/admin. Wordmark "Sentinel" + " Auth" muted.
- **Type:** IBM Plex Sans (display + body) + IBM Plex Mono (eyebrows/labels/
  buttons), self-hosted woff2 via `next/font/local` — brand-consistent with the
  admin panel.
- **Hosting:** GitHub Pages project URL now (`https://sidxz.github.io/sentinel-site/`,
  env-driven `basePath`); flip to `sentinel-auth.com` later (drop the env line +
  add `public/CNAME`; user points GoDaddy apex A records at GitHub Pages).
  `docs.sentinel-auth.com` already serves the docs from this repo.
- **Screenshots:** none captured now. An Admin-panel section ships with a
  screenshot-frame *placeholder*; user supplies PNGs later.
- **Approach:** copy-and-strip from docustore-site — fresh Next 16 + Tailwind v4
  project, copying only the files that carry the aesthetic (token skeleton,
  `cta.ts`, navbar/footer/logo shape, `deploy.yml`, exploded-stack + topology
  SVG mechanics). No shadcn/base-ui, no next-sitemap, no analytics.
- **Dark mode:** off (one theme, like docustore).

## Outbound links (single source for the plan)

- Docs home `https://docs.sentinel-auth.com/` · Getting started `https://docs.sentinel-auth.com/getting-started/` · Security `https://docs.sentinel-auth.com/security/`
- Guides: how-it-works, workspaces, authorization, service-apps, admin-panel under `https://docs.sentinel-auth.com/guide/<slug>/`
- Tutorials `https://docs.sentinel-auth.com/tutorial/react/`, `https://docs.sentinel-auth.com/tutorial/nextjs/`; SDK docs `https://docs.sentinel-auth.com/sdk/`, `https://docs.sentinel-auth.com/js-sdk/`
- GitHub `https://github.com/sidxz/Sentinel` (Issues `/issues`, Changelog `/blob/main/CHANGELOG.md`)
- PyPI `https://pypi.org/project/sentinel-auth-sdk/`; npm `https://www.npmjs.com/package/@sentinel-auth/{js,react,nextjs}`; image `ghcr.io/sidxz/sentinel`

## Stack & repo layout

`sidxz/sentinel-site` — pnpm, Next 16 App Router `output: "export"`, React 19,
Tailwind v4 (CSS-first, no config file), `lucide-react`, TypeScript.
Runtime deps: `next`, `react`, `react-dom`, `lucide-react`. Dev: `tailwindcss`,
`@tailwindcss/postcss`, `typescript`, `@types/*`, `eslint`, `eslint-config-next`.

```
sentinel-site/
  .github/workflows/deploy.yml     # copied from docustore; sets BASE_PATH=/sentinel-site
  AGENTS.md                        # design rules (adapted from docustore's)
  README.md                        # dev/build/deploy + domain-flip steps
  next.config.mjs                  # output: "export", basePath: process.env.BASE_PATH ?? ""
  postcss.config.mjs               # @tailwindcss/postcss
  package.json / pnpm-lock.yaml / tsconfig.json / eslint.config.mjs
  public/logos/*.svg               # vendored grayscale tech logos (Simple Icons, CC0)
  public/screenshots/.gitkeep      # user drops admin PNGs here later
  src/app/layout.tsx               # fonts, metadata (metadataBase from SITE_URL), navbar/footer
  src/app/page.tsx                 # the landing page (section order below)
  src/app/globals.css              # tokens + utilities
  src/app/icon.svg                 # favicon (dark-aware)
  src/app/not-found.tsx
  src/app/fonts/*.woff2            # Plex Sans 400/500/600 latin, Plex Mono 500 latin
  src/components/layout/{navbar,footer,logo}.tsx
  src/components/marketing/{token-flow,tier-stack,capability-demos,topology,screenshot-frame}.tsx
  src/lib/cta.ts                   # ctaPrimary / ctaOutline class strings
```

## Design tokens (`globals.css`)

Base copied verbatim from docustore's `@theme inline` / `:root` (white paper, ink
`#0b0b0d`, wash `#f5f5f5`, line `#e6e6e6`, small radii: cards `rounded-2xl`
16px, buttons `rounded-none`; container `max-w-6xl px-6`; section rhythm
`py-16`/`py-20`; eyebrows `.label-mono text-[11px] text-muted-foreground`
prefixed `/ `).

Accent tokens (replace `--color-g-*` / `--gradient-*`):

```
--color-brand:       #f43737   /* Sentinel red — the one accent */
--color-brand-dark:  #c42020
--color-brand-tint:  #ffd1d1
--color-brand-wash:  #fff1f1   /* card-top washes */
```

Utilities: `.text-brand`, `.bg-brand`, `.bg-brand-wash`, `.label-mono`.
Fonts mapped `--font-sans: var(--font-plex-sans)`, `--font-mono: var(--font-plex-mono)`.

**Where red appears (and nowhere else):** logo mark, hero eyebrow's first two
words, the three stat numbers, capability-card wash tops, one edge + shield node
in the hero SVG, one node in the topology card, CTA band 1.5px border, footer
hairline, `Check` icons in the security checklist.

## Page structure & copy (top → bottom)

All sections `mx-auto w-full max-w-6xl px-6` unless noted. Copy is final unless
marked *(alt)*.

**0. Navbar** — sticky `bg-background/85 backdrop-blur-xl`, h-16. Left: mark +
"Sentinel" + muted " Auth". Right: `Features` (→ `#features`), `Docs ↗`
(→ `https://docs.sentinel-auth.com/`), GitHub icon (→ `https://github.com/sidxz/Sentinel`),
`Get started` (ctaPrimary → `https://docs.sentinel-auth.com/getting-started/`).
No dropdown, no drawer; on mobile the links wrap.

**1. Hero** — eyebrow `OPEN SOURCE · SELF-HOSTED · BRING YOUR OWN IDP` (first
two words `text-brand`). H1 (~72px, left): **"Authorization for everything after login."** (changed from "Auth for…" 2026-08-15 — "Auth" reads as authentication)
*(alt: "The authorization layer between your IdP and your app.")*
Sub: "Keep Sign in with Google, GitHub, or Entra ID exactly as it is. Sentinel
adds workspaces, roles, and per-resource permissions — issued as one RS256 JWT
and enforced by SDKs for FastAPI, React, and Next.js. Self-hosted."
CTAs: `Get started` (ctaPrimary) · `View on GitHub ↗` (ctaOutline).
Below, `lg:grid-cols-[1.1fr_1fr]`: left `TokenFlow` SVG; right numbered list:
01 Sign in with your IdP · 02 Sentinel verifies the IdP token and mints an authz
JWT · 03 Your SDK enforces roles and permissions · 04 Manage it all in the admin
panel.

**2. Stats band** — `border-y py-12 sm:grid-cols-3`, values `text-6xl text-brand`,
labels mono 11px: **3** authorization tiers · **4** SDKs · **0** passwords stored.

**3. Capabilities** (`id="features"`) — eyebrow `/ How it works`, H2 "Everything
your IdP doesn't do." Grid `sm:grid-cols-2 lg:grid-cols-4`; each card is a `Link`
to a docs page; top third `bg-brand-wash` holding a white mock window with a mono
graphic from `capability-demos.tsx`; bottom title + description + "Learn more →".
- **Bring your own IdP** — "AuthZ mode: your app logs in, Sentinel verifies the
  IdP token and mints an authz JWT. Proxy mode if you'd rather Sentinel own the
  flow." mock: `id_token ─▶ authz_jwt · workspace_role: editor`. → `https://docs.sentinel-auth.com/guide/how-it-works/`
- **Workspaces & organizations** — "Multi-tenant by default. owner / admin /
  editor / viewer, groups, and email-domain organizations." mock: member chips.
  → `https://docs.sentinel-auth.com/guide/workspaces/`
- **Three-tier authorization** — "Workspace roles in the JWT, RBAC actions in the
  DB, Zanzibar-style entity ACLs per resource." mock: `require_action("reports:export")`
  / `can("document", id, "view")`. → `https://docs.sentinel-auth.com/guide/authorization/`
- **Service-to-service** — "Service keys, realms, and m2m calls with or without a
  user in context." mock: `sk_… → realm: platform ✓`. → `https://docs.sentinel-auth.com/guide/service-apps/`

**4. Authorization deep-dive** — eyebrow `/ Three tiers, one dependency`, H2
"Coarse to fine, without leaving the request." `lg:grid-cols-[1.05fr_1fr]`: left
`TierStack` SVG (exploded plates: *Workspace roles (JWT)* / *Custom RBAC (DB)* /
*Entity ACLs (Zanzibar)*, request arrow through, chips `editor` ·
`reports:export ✓` · `document:42 view ✓`); right the README three-tier Python
block verbatim (Tier 1 `require_user`, Tier 2 `require_action("reports:export")`,
Tier 3 `auth.can("project", id, "view")`), in a bordered mono card.

**5. SDKs** — eyebrow `/ Ship it in your stack`, H2 "Three lines to a protected
route." Static `lg:grid-cols-3` of bordered mono code cards, no tabs, no
highlighter (ink text, muted comments, one `text-brand` token per card):
- **FastAPI** — `Sentinel(...)` config, `sentinel.protect(app)`,
  `@app.get("/projects") … Depends(sentinel.require_user)` (from docs/index.md).
  Below: `pip install sentinel-auth-sdk`.
- **React** — `AuthzProvider` + `AuthzGuard fallback={<Login/>}` +
  `useAuthz().login("google")` (README JS section, trimmed to ~14 lines).
  Below: `npm i @sentinel-auth/react`.
- **Next.js** — `createSentinelAuthzMiddleware({...})` middleware.ts (docs
  tutorial/nextjs). Below: `npm i @sentinel-auth/nextjs`.

**6. Problems** — full-bleed `border-y bg-wash/60`; eyebrow `/ What you stop
building`, H2 "Six things every app re-implements. Solved once." Two-column
mono-numbered list, hairline above each title:
01 **JWT validation, per service** — JWKS fetch, audience, clock skew, key
rotation, in every backend. → The SDK does it: RS256, `kid` rotation,
`/.well-known/jwks.json`.
02 **A roles table in every app** — RBAC drifts across services. → Namespaced
actions and workspace-scoped roles in one place; `require_action`.
03 **Ad-hoc sharing logic** — "can Alice edit doc 42?" becomes columns and joins.
→ Zanzibar-style entity ACLs; `can()` / `accessible()`.
04 **Tenant isolation by convention** — the `workspace_id` filter someone forgets.
→ Workspace-scoped claims; roles and grants can't cross tenants.
05 **Token hygiene as an afterthought** — rotation, reuse detection, revocation
bolted on late. → Refresh rotation, reuse detection, Redis denylist, `jti`.
06 **No admin UI** — auth state lives in SQL consoles. → React admin: users,
workspaces, roles, grants, service apps, activity, usage.

**7. Admin panel** — eyebrow `/ Admin panel`, H2 "See everything from one place."
`lg:grid-cols-[1fr_1.2fr]`: left copy + short bullet list (Users · Workspaces ·
Roles & actions · Permissions · Service apps & realms · Activity, insights, usage);
right `ScreenshotFrame` — browser-chrome frame (three dots + mono URL bar
`admin.sentinel-auth.local`) around either `<img src>` or, when no `src`, a
dashed `bg-wash` area with mono label "SCREENSHOT — ADMIN DASHBOARD" and a
16:10 aspect box. Ships with the placeholder; user later drops PNGs into
`public/screenshots/` and passes `src`.

**8. Security posture** — eyebrow `/ Built to be audited`, H2 "Boring where it
counts." Two-column mono checklist, `Check` icons `text-brand`: RS256 JWTs with
`kid` rotation · refresh rotation with reuse detection · Redis denylist for
revocation · IdP token never persisted · service keys 256-bit, DB-managed ·
rate limiting, CORS, HSTS, CSP, trusted hosts · audit + activity trail ·
Trivy dependency and container scans in CI. Link "Security overview →" →
`https://docs.sentinel-auth.com/security/`.

**9. Under the hood** — eyebrow `/ Under the hood`, H2 "Built on proven
infrastructure." `lg:grid-cols-[1.05fr_1fr]`: left copy + `gap-px bg-border`
tile grid (FastAPI · SQLAlchemy · PostgreSQL · Redis · Authlib (text tile) ·
Python · React · TypeScript · Docker/ghcr), each tile grayscale logo + name +
mono role; right dark `bg-ink` `Topology` card: Browser/App ⇄ IdP; App → Sentinel
API → Postgres / Redis; Admin SPA; SDK nodes at the edges; one `brand` node.

**10. CTA band** — outer `bg-brand rounded-2xl p-[1.5px]`, inner
`rounded-[15px] bg-paper`: H2 "Ship auth in an afternoon." copy + `Get started`
/ `Star on GitHub ↗` + mono line `docker pull ghcr.io/sidxz/sentinel`. Beneath,
mono 11px muted: "Beta — APIs may change before 1.0."

**11. Footer** — 1px `bg-brand` hairline; mark + blurb ("Authentication proxy
and authorization microservice. Bring your own IdP."); columns **Product**
(Features, Docs, Getting started, Security) · **SDKs** (PyPI
`sentinel-auth-sdk`, `@sentinel-auth/js`, `/react`, `/nextjs`) · **Community**
(GitHub, Issues, Changelog); bottom bar `© {year} Sentinel Auth` · `MIT License`
mono 10px.

## Art (all hand-authored SVG in `.tsx`)

- `logo.tsx` — `LogoMark` 32×32: shield outline, hexagon core with keyhole,
  two short circuit stubs; 1.5px `brand` stroke. `Logo` = mark + wordmark
  ("Sentinel" 500 + " Auth" `text-ink/30`). `icon.svg` = hexagon+keyhole
  fragment, `<style>` flips stroke to `#fafafa` under `prefers-color-scheme: dark`.
- `token-flow.tsx` — static server SVG ~480×300: three IdP chips (Google /
  GitHub / Entra as mono text chips) → shield node (brand stroke) → JWT card
  with three mono claim lines (`sub`, `workspace_id`, `workspace_role: "editor"`)
  → "your app" node. Ink strokes at low alpha; the shield and the JWT edge in
  brand. No JS.
- `tier-stack.tsx` — client component; SSR renders the exploded final state,
  on mount collapses plates, `IntersectionObserver` (threshold 0.35, fires once)
  releases them with staggered transitions (`cubic-bezier(0.22,1,0.36,1)`,
  700ms, 60ms stagger). `prefers-reduced-motion` → bail out, SSR state stands.
  Mechanic copied from docustore's `pipeline-stack.tsx`.
- `capability-demos.tsx` — four small mock-window graphics (mono text + chips),
  server-rendered.
- `topology.tsx` — dark card 560×324, local `Node` helper, edges
  `rgba(255,255,255,0.25)`, labels `0.4`, one brand-stroked node.
- `screenshot-frame.tsx` — `ScreenshotFrame({ src?, alt, label })`.
- `public/logos/` — fastapi, python, react, typescript, postgresql, redis,
  sqlalchemy, docker (Simple Icons, CC0). Rendered `<img>` at
  `opacity-55 grayscale`, hover restores color; eslint-disable
  `@next/next/no-img-element` (static export).

## Deploy & domain flip

`deploy.yml`: on push to `main` → checkout → pnpm/action-setup@v4 → setup-node 22
(pnpm cache) → `pnpm install --frozen-lockfile` → `pnpm build` with
`BASE_PATH: /sentinel-site` → `configure-pages@v5` → `upload-pages-artifact@v3`
(`path: out`) → `deploy-pages@v4`. Repo setting: Pages source = GitHub Actions.

Domain flip (documented in README): remove `BASE_PATH` from the workflow, add
`public/CNAME` = `sentinel-auth.com`, set `SITE_URL` metadata to
`https://sentinel-auth.com`; user points the apex A records (currently GoDaddy
parking) at GitHub Pages and enables "Enforce HTTPS".

## Verification

- `pnpm build` passes (typecheck, lint, static export). `out/index.html` fetches
  no external fonts/CDNs.
- Serve `out/` locally under the `/sentinel-site` prefix; screenshot desktop
  (1440w) and mobile (390w) with headless Chrome; read the PNGs and iterate.
- `prefers-reduced-motion: reduce` renders the tier stack fully exploded, no
  animation.
- Every outbound link resolves (docs paths spot-checked against
  `docs.sentinel-auth.com`).

## Out of scope / follow-ups (not built here)

- Real admin screenshots (user-provided; slot exists).
- Feature sub-pages, blog, docs migration into the site.
- OG image, sitemap/robots — add at the domain flip.
- In identity-service: add a root `LICENSE` (README/npm say MIT, no file); fix
  `mkdocs.yml site_url` → `https://docs.sentinel-auth.com/`.
