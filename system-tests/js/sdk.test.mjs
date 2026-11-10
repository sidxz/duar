// The JS SDKs, as an app would install them, against the live service.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { after, before, test } from 'node:test'

import { DuarAuth, DuarAuthz, MemoryStore } from '@duar-auth/js'
import { M2mTokenClient, PermissionClient, RoleClient, fetchWhoami, verifyM2mToken, verifyToken } from '@duar-auth/js/server'
import { createDuarProxy } from '@duar-auth/nextjs/proxy'

import { BASE, JWKS_URL, idpToken, installBrowserGlobals, signInThroughDex, tamper, world } from './helpers.mjs'

const window = installBrowserGlobals()
const store = new MemoryStore()
const auth = new DuarAuth({
  duarUrl: BASE,
  clientId: world.client_app_id,
  redirectUri: world.appCallback,
  storage: store,
  autoRefresh: false,
})
after(() => auth.destroy())

// Every later test needs a signed-in user, so sign-in runs first and for real:
// the SDK builds the login URL and holds the PKCE verifier and state itself.
before(async () => {
  await auth.login('dex')
  const landed = new URL(await signInThroughDex(window.location.href, 'alice@duar.test', world.appCallback))
  window.location.search = landed.search
  auth.verifyCallbackState()
  const code = landed.searchParams.get('code')
  const workspaces = await auth.getWorkspaces(code)
  assert.ok(workspaces.some((w) => w.id === world.workspace_id && w.role === 'admin'))
  await auth.selectWorkspace(code, world.workspace_id)
})

test('every published entry point loads from the installed packages', async () => {
  const require = createRequire(import.meta.url)
  const anywhere = ['@duar-auth/js', '@duar-auth/js/server', '@duar-auth/react', '@duar-auth/nextjs/proxy']
  // These import next/*, which Node resolves through require only; inside a
  // Next.js app they are bundled.
  const nextOnly = [
    '@duar-auth/nextjs',
    '@duar-auth/nextjs/middleware',
    '@duar-auth/nextjs/authz-middleware',
    '@duar-auth/nextjs/server',
  ]
  for (const entry of anywhere) {
    assert.ok(Object.keys(await import(entry)).length > 0, `${entry} (import)`)
  }
  for (const entry of [...anywhere, ...nextOnly]) {
    assert.ok(Object.keys(require(entry)).length > 0, `${entry} (require)`)
  }
})

test('browser client: the signed-in user, an API call, refresh, sign-out', async () => {
  assert.equal(auth.isAuthenticated, true)
  const user = auth.getUser()
  assert.deepEqual(
    [user.email, user.workspaceId, user.workspaceRole],
    ['alice@duar.test', world.workspace_id, 'admin'],
  )
  assert.equal((await auth.fetchJson(`${BASE}/users/me`)).email, 'alice@duar.test')

  const first = auth.getToken()
  assert.equal(await auth.refresh(), true)
  assert.notEqual(auth.getToken(), first)
  assert.equal((await auth.fetchJson(`${BASE}/users/me`)).email, 'alice@duar.test')
})

test('server: a Duar token verifies against the live key set, and only as what it is', async () => {
  const token = auth.getToken()
  const payload = await verifyToken(token, { jwksUrl: JWKS_URL, issuer: BASE })
  assert.deepEqual([payload.email, payload.wrole, payload.aud], ['alice@duar.test', 'admin', 'duar:access'])

  await assert.rejects(verifyToken(tamper(token), { jwksUrl: JWKS_URL, issuer: BASE }))
  await assert.rejects(verifyToken(token, { jwksUrl: JWKS_URL, issuer: 'https://someone-else.example' }))
  await assert.rejects(verifyToken(token, { jwksUrl: JWKS_URL, issuer: BASE, audience: 'duar:authz' }))
  await assert.rejects(verifyToken(store.getRefreshToken(), { jwksUrl: JWKS_URL, issuer: BASE }))
})

test('server: permission and role clients round-trip with a service key and the user token', async () => {
  const token = auth.getToken()
  const permissions = new PermissionClient(BASE, world.notes, world.notes_key)
  const roles = new RoleClient(BASE, world.notes, world.notes_key)
  const resourceId = crypto.randomUUID()
  const alice = world.users['alice@duar.test']

  await permissions.registerResource({
    service_name: world.notes,
    resource_type: 'js-note',
    resource_id: resourceId,
    workspace_id: world.workspace_id,
    owner_id: alice,
    visibility: 'private',
  })
  assert.equal(await permissions.can(token, 'js-note', resourceId, 'edit'), true)
  assert.equal(await permissions.can(token, 'js-note', crypto.randomUUID(), 'edit'), false)
  // alice is a workspace admin: Duar answers "everything" instead of listing ids.
  const listed = await permissions.accessible(token, 'js-note', 'view', world.workspace_id)
  assert.equal(listed.has_full_access, true)

  // No role grants alice this action, so the answer from Duar is a plain no.
  assert.equal(await roles.checkAction(token, 'notes:export', world.workspace_id), false)

  await permissions.deregisterResource('js-note', resourceId)
  assert.equal(await permissions.can(token, 'js-note', resourceId, 'edit'), false)

  const wrongKey = new PermissionClient(BASE, world.notes, 'sk_not_a_key')
  await assert.rejects(wrongKey.can(token, 'js-note', resourceId, 'edit'))
})

test('server: realm members mint and verify machine tokens; a standalone app cannot mint', async () => {
  const who = await fetchWhoami({ duarUrl: BASE, serviceKey: world.billing_key })
  assert.deepEqual([who.service_name, who.effective_scope], [world.billing, world.realm])

  const token = await new M2mTokenClient(BASE, world.billing_key).getToken()
  const system = await verifyM2mToken(token, { jwksUrl: JWKS_URL, effectiveScope: world.realm, serviceName: world.ledger })
  assert.deepEqual([system.caller, system.svc, system.can('anything')], [world.billing, world.realm, true])

  await assert.rejects(verifyM2mToken(token, { jwksUrl: JWKS_URL, effectiveScope: 'another-realm' }))
  await assert.rejects(verifyM2mToken(auth.getToken(), { jwksUrl: JWKS_URL, effectiveScope: world.realm }))
  await assert.rejects(new M2mTokenClient(BASE, world.notes_key).getToken())
})

test('AuthZ-mode browser client, through the app-side proxy helper', async () => {
  // The app's own backend: @duar-auth/nextjs/proxy mounted at /api/duar, holding
  // the service key. Requests the browser would send there are handed to it.
  const mount = `${new URL(world.appCallback).origin}/api/duar`
  const proxy = createDuarProxy({ duarUrl: BASE, serviceKey: world.notes_key })
  const realFetch = globalThis.fetch
  globalThis.fetch = (input, init) => {
    const url = String(input instanceof Request ? input.url : input)
    if (!url.startsWith(`${mount}/`)) return realFetch(input, init)
    const request = new Request(url, init)
    const path = new URL(url).pathname.slice(new URL(mount).pathname.length + 1).split('/')
    return proxy[request.method](request, { params: { path } })
  }

  const authz = new DuarAuthz({ duarUrl: mount, mintEndpoint: `${mount}/authz/resolve`, autoRefresh: false })
  try {
    const identity = idpToken('carol@duar.test')
    const found = await authz.resolve(identity, 'test_oidc')
    assert.equal(found.user.email, 'carol@duar.test')
    assert.ok(found.workspaces.some((w) => w.id === world.workspace_id && w.role === 'editor'))

    await authz.selectWorkspace(identity, 'test_oidc', world.workspace_id)
    const user = authz.getUser()
    assert.deepEqual([user.email, user.workspaceRole], ['carol@duar.test', 'editor'])

    // What the app's API would receive, checked the way its backend would.
    const headers = authz.getHeaders()
    assert.equal(headers.Authorization, `Bearer ${identity}`)
    const claims = await verifyToken(headers['X-Authz-Token'], { jwksUrl: JWKS_URL, issuer: BASE, audience: 'duar:authz' })
    assert.deepEqual([claims.svc, claims.wid, claims.idp_sub], [world.notes, world.workspace_id, 'idp|carol@duar.test'])

    const members = await authz.listMembers()
    assert.ok(members.some((m) => m.email === 'dave@duar.test'))
    assert.equal((await authz.getProfile()).email, 'carol@duar.test')

    // The proxy forwards its allowlist and nothing else.
    const blocked = await fetch(`${mount}/admin/users`)
    assert.equal(blocked.status, 404)
  } finally {
    authz.destroy()
    globalThis.fetch = realFetch
  }
})

test('browser client: after sign-out there is no session to use', async () => {
  auth.logout()
  assert.equal(auth.isAuthenticated, false)
  assert.equal(auth.getUser(), null)
  assert.equal((await auth.fetch(`${BASE}/users/me`)).status, 401)
})
