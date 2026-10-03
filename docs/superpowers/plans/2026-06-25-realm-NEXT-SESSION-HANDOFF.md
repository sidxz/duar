# Realms — Next-Session Handoff (Plan 4 DONE → Plans 5–6)

**Read this first in a fresh session, then resume. Do NOT re-brainstorm — the design is approved. Do NOT re-do Plans 1–4 (backend + frontend) — they are DONE and committed.**

## Resume in one line
`git checkout realm-trusted-app-group` (you're likely already on it) → read the spec + the prior plan docs (below) → the next deliverable is **Plan 5 (SDK m2m)**, then **Plan 6 (docs)**. For **each** remaining plan: `superpowers:writing-plans` to author it, then `superpowers:subagent-driven-development` to execute it. One plan per cycle; review between; **stop and report after each plan** (the user resumes in fresh sessions).

## State (as of 2026-06-26, HEAD = `7dcaca8`)
- Branch `realm-trusted-app-group`. Merge-base with `main` = `e272eb8`. The branch is **stacked on the unmerged `ratelimit-consolidation`** (branched off `551c0ce`, not `main`) — a merge/PR to `main` would also carry rate-limit. **Kept as-is, not merged.**
- **DONE (committed):** Plan 1 (scope core), Plan 2 (token flows), Plan 3 (network split), **Plan 4 backend (realm admin API)**, **Plan 4 frontend (React Realms UI)**. Backend suite **330 green**; every backend plan had a clean Opus final review. Frontend (no test harness — gated by `npm run build` + eslint) shipped in 6 commits `605d00b..7dcaca8`; final Opus review = Ready-to-merge, no Critical/Important.
- **Plan 4 was SPLIT** into backend (done) + **React UI (DONE)**. Plan + SDD ledger: `docs/superpowers/plans/2026-06-25-realm-admin-ui.md`, `.superpowers/sdd/progress.md`.
- **Plan-4 frontend gotchas (carry forward):** admin SPA's project-wide `eslint .` is PRE-broken by 2 errors + 1 warning in `AuthGuard.tsx`/`Login.tsx`/`SearchInput.tsx` (unrelated to realms) — gate new admin work on `npx eslint <changed files>` + full `npm run build`. `admin/node_modules` must be `npm install`-ed first (it wasn't present). `admin/src/types/api.ts` `ServiceApp` now has `realm_id`. **Deferred:** candidate-app `has_grants` pre-warning in the realm members dropdown — needs `ServiceAppResponse.has_grants` (backend); member-row `⚠` badge covers it post-add (`// ponytail:` comment in `RealmDetail.tsx`).
- ⚠️ The working tree holds the **user's separate uncommitted work**: `service/src/services/role_service.py` (an atomic `INSERT…ON CONFLICT` upsert refactor of `register_actions`) + untracked `service/tests/test_register_actions.py`. **NEVER commit, format, stage, or discard these.** (The user's earlier rate-limit WIP was already committed at `7114d9b` per their instruction — that one is done.)

## Authority documents (read these, don't re-derive)
- **Spec (source of truth):** `docs/superpowers/specs/2026-06-25-realm-trusted-app-group-design.md`
- **Plan docs (done, reference style + decisions):** `2026-06-25-realm-scope-core.md` (P1), `2026-06-25-realm-token-flows.md` (P2), `2026-06-25-realm-network-split.md` (P3), `2026-06-25-realm-admin-api.md` (P4 backend) — all under `docs/superpowers/plans/`.
- **Memory:** `realm-trusted-app-groups.md` (auto-loaded via MEMORY.md) — has the full per-plan decisions + gotchas.
- **SDD ledgers:** `.superpowers/sdd/progress-plan{1,2,3}-archive.md` + the current `progress.md` (= Plan-4 backend, all complete). **Archive `progress.md` before the next plan's SDD run** (e.g. → `progress-plan4backend-archive.md`).

## Remaining plans (scope only — author details in writing-plans)
**A. React Realms UI (Plan 4 frontend) — ✅ DONE (commits `605d00b..7dcaca8`).** Shipped `admin/src/pages/Realms.tsx` + `RealmDetail.tsx`, realm fns/types in `api/client.ts`+`types/api.ts`, nav+routes, and the realm line on `ServiceAppDetail.tsx`. Reference for the patterns used (kept for Plans 5–6 context; the admin SPA is at `admin/`, Vite 7 + React 19 + Tailwind 4 + React Query 5 + React Router 7, Sonner toasts):
- **API client:** `admin/src/api/client.ts` — single `request<T>` helper; `X-Requested-With: XMLHttpRequest` is baked in (CSRF). Add realm fns here. **Types:** `admin/src/types/api.ts` (add `Realm`, `RealmMember`).
- **Realms list + create:** model on `admin/src/pages/Organizations.tsx`. New `admin/src/pages/Realms.tsx`.
- **Realm detail (edit / delete-with-type-confirm / members tab):** model on `admin/src/pages/ServiceAppDetail.tsx` + `ConfirmModal` (`confirmInput` prop = type-to-confirm). New `admin/src/pages/RealmDetail.tsx`.
- **Members add/remove UI:** model on `RolesTab`/`MembersTab` in `admin/src/pages/WorkspaceDetail.tsx` (select-from-dropdown + Add + per-row Remove). Filter candidates to standalone apps (`realm_id == null`) using `getServiceApps()`; warn when a candidate/member `has_grants`.
- **ServiceAppDetail shows realm:** add a "Realm:" line in the info block (`admin/src/pages/ServiceAppDetail.tsx` ~lines 163–186); resolve `realm_id` → name from `getRealms()` client-side (the API intentionally has NO `realm_name`).
- **Routes:** flat `<Route>` in `admin/src/App.tsx`. **Nav:** `NAV` array in `admin/src/components/Layout.tsx`.
- **Query keys:** `["realms"]`, `["realm", id]`, `["realm-members", id]`. Invalidate list + detail on mutation.
- **Backend API surface it consumes (all admin-cookie + `X-Requested-With`):** `GET/POST /admin/realms`, `GET/PATCH/DELETE /admin/realms/{id}`, `GET /admin/realms/{id}/members`, `POST/DELETE /admin/realms/{id}/members/{serviceAppId}`. `RealmResponse` = `{id, slug, name, m2m_ttl_s, is_active, created_at}`. `RealmMemberResponse` = `{id, name, service_name, has_grants}`. `RealmCreateRequest` = `{name, slug, m2m_ttl_s?}` (slug letter-start, immutable after create). `RealmUpdateRequest` = `{name?, m2m_ttl_s?, is_active?}` (no slug). `ServiceAppResponse` now has `realm_id`.

**B. Plan 5 — SDKs (ALL the SDK m2m work; deferred from Plans 2 & 5).** Python `duar_auth`: `whoami` scope-discovery (cached) + broaden the authz `svc` check to `effective_scope`; `mint_m2m_token()` with ~80%-TTL auto-refresh; **accept `type=m2m` → a new `SystemAuth` context** (no user; `caller`, `actions`). JS (`@duar-auth/js`, `react`, `nextjs`): broaden user-context `svc` check to scope (via whoami); m2m **mint + accept** in the **server entry only** — never browser. This is the receiver-side acceptance that makes no-user Flow B end-to-end. SDK trees: `sdk/src/duar_auth/` (Python), `sdks/` (JS).

**C. Plan 6 — Docs.** guide/api/sdk for realms + m2m + the **internal-listener deployment posture** (TIER=public/internal, unpublished :9010). MkDocs; `make docs-serve`; CI gate is `mkdocs --strict`.

## MANDATORY conventions for every subagent dispatch (these bit us / kept us safe)
- **Backend:** format/stage ONLY changed files (`cd service && uv run ruff format <files>` + `ruff check --fix <files>`). **NEVER** `ruff format .` / whole-tree `--fix` / `make fmt` (they reformat the user's uncommitted `role_service.py`). NEVER `git add -A`/`.`.
- **Frontend:** lint/build are `npm` scripts in `admin/`; no whole-repo formatter. Stage only the admin files you touch.
- **Never touch** `service/src/services/role_service.py` or `service/tests/test_register_actions.py`.
- **Tests (backend):** pure-unit with fakes OR the behavioral **TestClient + `dependency_overrides` + monkeypatch** house style (see `tests/test_realm_routes.py`, `test_realm_admin_crud.py`, `test_realm_admin_membership.py`). Gate on the **task's own test file**; broad-suite **IdP/JWKS connection failures are a known network-sandbox artifact**, not task failures.
- **SDD:** archive the current `progress.md` before each new plan's run; **regenerate each `task-N-brief` from the correct plan file** (the brief slots are reused across plans and hold STALE content otherwise). One implementer at a time (no parallel implementers).

## Gotchas discovered (carry forward)
- **Router-level admin auth (recurring FALSE POSITIVE):** `admin_router` has `dependencies=[Depends(require_admin)]` (`admin_routes.py:92`) gating EVERY `/admin/*` route, so admin READ endpoints correctly omit a per-function `require_admin` (mutations take `admin` only for `actor_id`). Security scans/reviewers keep flagging this as "missing authorization" — it is NOT. Proven by `test_realm_endpoints_require_admin_auth` (no-cookie GET → 401). Don't add redundant per-endpoint deps.
- **Async-SQLAlchemy `created_at`:** an endpoint returning an ORM object right after `flush()` will lazy-refresh a `server_default`-only column on attribute access → `MissingGreenlet`. `Realm.created_at` uses `default=lambda: datetime.now(UTC)` (tz-aware; NOT deprecated naive `datetime.utcnow`). Keep that pattern; this codebase is tz-aware UTC everywhere.
- **FastAPI 0.138 / Starlette 1.3:** `include_router()` results are lazy `_IncludedRouter` objects with no `.path`; enumerate routes via `r.original_router.routes` (gate on `hasattr(r, "original_router")`, not the private class name).
- **Network split (P3):** the `/realm`, `/permissions`, `/authz/resolve`, roles surface is on the **internal** tier (TIER=internal, unpublished :9010); `/authz/idp/*` (browser GitHub-proxy, uses sessions) is **public**. `create_app("all")` (TIER unset, the default) = today's full app — non-breaking for dev/make-start/tests.
- **Container gotcha (from memory, relevant to P6 deploy docs):** `service/Dockerfile` does NOT `COPY uv.lock` before `uv sync` → installs latest-within-pyproject, not the pinned lock. The Dockerfile's baked `HEALTHCHECK`/`EXPOSE 9003` is overridden per-container by compose (internal uses :9010) — document for non-compose internal runs.
- **Open future-work nits (not blocking):** consider a dedicated rate-limit config knob for `/realm/m2m-token` (currently reuses `rate_limit_authz_resolve`); list-members returns `[]` (not 404) for a nonexistent realm (harmless).
