"""The three authorization tiers, exercised the way an app backend uses them:
through the Python SDK, wired into a small FastAPI app, against the live service.

    workspace role (in the token)  ->  create a note needs "editor"
    custom role / action (RBAC)    ->  exporting needs the "notes:export" action
    per-resource ACL               ->  each note is private to its owner until shared
"""

import asyncio
import uuid

import jwt
import pytest
from duar_auth import Duar
from duar_auth.auth import RequestAuth
from duar_auth.types import AuthenticatedUser, DuarError
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from conftest import BASE, IDP_AUDIENCE, IDP_ISSUER, IDP_PUBLIC_KEY, bearer, idp_token, service_headers


def authz_mode_duar(name, key, **extra):
    return Duar(
        base_url=BASE,
        service_name=name,
        service_key=key,
        idp_public_key=IDP_PUBLIC_KEY,
        idp_audience=IDP_AUDIENCE,
        idp_issuer=IDP_ISSUER,
        **extra,
    )


@pytest.fixture(scope="module")
def notes_duar(world):
    return authz_mode_duar(
        world.notes,
        world.notes_key,
        idp_provider="test_oidc",
        auto_resolve=True,
        actions=[{"action": "notes:export", "description": "Export every note"}],
    )


@pytest.fixture(scope="module")
def notes(notes_duar):
    """A notes backend protected by the SDK in AuthZ mode."""
    duar = notes_duar
    app = FastAPI(lifespan=duar.lifespan)
    duar.protect(app)

    @app.get("/me")
    def me(user: AuthenticatedUser = Depends(duar.require_user)):
        return {"email": user.email, "role": user.workspace_role, "workspace": str(user.workspace_id)}

    @app.post("/notes", status_code=201)
    async def create(auth: RequestAuth = Depends(duar.get_auth)):
        if not auth.has_role("editor"):
            raise HTTPException(403, "editors only")
        note_id = uuid.uuid4()
        await auth.register_resource("note", note_id, visibility="private")
        return {"id": str(note_id)}

    async def require(auth, note_id, action):
        if not await auth.can("note", note_id, action):
            raise HTTPException(403, "no access")

    @app.get("/notes/{note_id}")
    async def read(note_id: uuid.UUID, auth: RequestAuth = Depends(duar.get_auth)):
        await require(auth, note_id, "view")
        return {"id": str(note_id)}

    @app.patch("/notes/{note_id}")
    async def edit(note_id: uuid.UUID, auth: RequestAuth = Depends(duar.get_auth)):
        await require(auth, note_id, "edit")
        return {"id": str(note_id)}

    @app.post("/notes/{note_id}/share/{user_id}")
    async def share(note_id: uuid.UUID, user_id: uuid.UUID, auth: RequestAuth = Depends(duar.get_auth)):
        await auth.share("note", note_id, "user", user_id, "view")
        return {"ok": True}

    @app.get("/notes")
    async def listing(auth: RequestAuth = Depends(duar.get_auth)):
        ids, everything = await auth.accessible("note", "view")
        return {"ids": [str(i) for i in ids], "everything": everything}

    @app.get("/export")
    def export(user: AuthenticatedUser = Depends(duar.require_action("notes:export"))):
        return {"exported_by": user.email}

    with TestClient(app) as client:  # runs the SDK's startup against the live service
        yield client


@pytest.fixture(scope="module")
def tokens(world, notes_duar):
    """Dual-token headers for a user: their IdP token plus the authz token the
    app's backend mints for them through the SDK."""

    def headers(email):
        identity = idp_token(email)
        resolved = asyncio.run(notes_duar.authz.resolve(identity, "test_oidc", world.workspace_id))
        return {"Authorization": f"Bearer {identity}", "X-Authz-Token": resolved["authz_token"]}

    return headers


@pytest.fixture(scope="module")
def carol(tokens):
    return tokens("carol@duar.test")


@pytest.fixture(scope="module")
def dave(tokens):
    return tokens("dave@duar.test")


# ── Getting in ───────────────────────────────────────────────────────────────


def test_a_dual_token_request_reaches_the_app_as_the_right_user(notes, world, carol):
    me = notes.get("/me", headers=carol)
    assert me.status_code == 200, me.text
    assert me.json() == {"email": "carol@duar.test", "role": "editor", "workspace": world.workspace_id}


def test_the_identity_token_alone_is_not_enough(notes, world, carol):
    identity_only = {"Authorization": carol["Authorization"]}
    assert notes.get("/me", headers=identity_only).status_code == 401
    # ...unless the app opted in to minting the authz token itself for a named workspace.
    resolved = notes.get("/me", headers={**identity_only, "X-Workspace-Id": world.workspace_id})
    assert resolved.status_code == 200 and resolved.json()["email"] == "carol@duar.test"


def test_one_users_authz_token_does_not_work_with_anothers_identity(notes, carol, dave):
    mixed = {"Authorization": dave["Authorization"], "X-Authz-Token": carol["X-Authz-Token"]}
    assert notes.get("/me", headers=mixed).status_code == 401


def test_a_tampered_authz_token_is_refused(notes, carol):
    head, payload, signature = carol["X-Authz-Token"].split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    assert notes.get("/me", headers={**carol, "X-Authz-Token": f"{head}.{payload}.{flipped}"}).status_code == 401


def test_an_identity_token_for_another_client_is_refused_by_the_app_and_by_duar(api, notes, world, carol):
    foreign = idp_token("carol@duar.test", audience="some-other-client")
    assert notes.get("/me", headers={**carol, "Authorization": f"Bearer {foreign}"}).status_code == 401
    body = {"idp_token": foreign, "provider": "test_oidc", "workspace_id": world.workspace_id}
    assert api.post(f"{BASE}/authz/resolve", json=body, headers=service_headers(world.notes_key)).status_code == 400
    body["idp_token"] = idp_token("carol@duar.test")
    assert api.post(f"{BASE}/authz/resolve", json=body, headers=service_headers(world.notes_key)).status_code == 200


def test_an_unverified_email_cannot_get_an_authz_token(api, world):
    body = {
        "idp_token": idp_token("carol@duar.test", email_verified=False),
        "provider": "test_oidc",
        "workspace_id": world.workspace_id,
    }
    assert api.post(f"{BASE}/authz/resolve", json=body, headers=service_headers(world.notes_key)).status_code == 400


def test_an_authz_token_is_bound_to_the_app_it_was_minted_for(world, carol):
    claims = jwt.decode(carol["X-Authz-Token"], options={"verify_signature": False})
    assert (claims["svc"], claims["aud"], claims["wrole"]) == (world.notes, "duar:authz", "editor")
    # A second app, with its own key, receives the token minted for notes.
    other = authz_mode_duar(world.billing, world.billing_key)
    app = FastAPI(lifespan=other.lifespan)
    other.protect(app)

    @app.get("/me")
    def me(user: AuthenticatedUser = Depends(other.require_user)):
        return {"email": user.email}

    with TestClient(app) as client:
        assert client.get("/me", headers=carol).status_code == 403


def test_a_service_key_is_required_to_mint(api, world):
    body = {"idp_token": idp_token("carol@duar.test"), "provider": "test_oidc", "workspace_id": world.workspace_id}
    assert api.post(f"{BASE}/authz/resolve", json=body).status_code == 401
    assert api.post(f"{BASE}/authz/resolve", json=body, headers=service_headers("sk_wrong")).status_code == 401


# ── Tier 1: workspace role ───────────────────────────────────────────────────


def test_workspace_role_gates_creating(notes, carol, dave):
    assert notes.post("/notes", headers=carol).status_code == 201  # editor
    assert notes.post("/notes", headers=dave).status_code == 403  # viewer


# ── Tier 3: per-resource ACL ─────────────────────────────────────────────────


def test_a_private_note_is_its_owners_until_shared(notes, world, carol, dave):
    note = notes.post("/notes", headers=carol).json()["id"]
    assert notes.get(f"/notes/{note}", headers=carol).status_code == 200
    assert notes.patch(f"/notes/{note}", headers=carol).status_code == 200
    assert notes.get(f"/notes/{note}", headers=dave).status_code == 403
    assert note not in notes.get("/notes", headers=dave).json()["ids"]

    shared = notes.post(f"/notes/{note}/share/{world.users['dave@duar.test']}", headers=carol)
    assert shared.status_code == 200, shared.text
    assert notes.get(f"/notes/{note}", headers=dave).status_code == 200
    assert notes.patch(f"/notes/{note}", headers=dave).status_code == 403  # shared to view, not edit
    assert note in notes.get("/notes", headers=dave).json()["ids"]


def test_only_someone_who_can_edit_a_note_can_share_it(notes, world, carol, dave):
    note = notes.post("/notes", headers=carol).json()["id"]
    # TestClient re-raises what the route raises: the SDK's error for Duar's refusal.
    with pytest.raises(DuarError) as refused:
        notes.post(f"/notes/{note}/share/{world.users['dave@duar.test']}", headers=dave)
    assert refused.value.status_code in (403, 404)
    assert notes.get(f"/notes/{note}", headers=dave).status_code == 403


def test_a_resource_cannot_be_shared_outside_its_workspace(notes, world, carol):
    note = notes.post("/notes", headers=carol).json()["id"]
    outsider = world.users["frank@duar.test"]  # a real user, in another workspace
    with pytest.raises(DuarError) as refused:
        notes.post(f"/notes/{note}/share/{outsider}", headers=carol)
    assert 400 <= refused.value.status_code < 500


def test_one_apps_key_cannot_touch_another_apps_resources(api, notes, world, carol):
    note = notes.post("/notes", headers=carol).json()["id"]
    path = f"{BASE}/permissions/resource/{world.notes}/note/{note}"
    assert api.get(path, headers=service_headers(world.notes_key)).status_code == 200
    assert api.get(path, headers=service_headers(world.billing_key)).status_code == 403
    assert api.delete(path, headers=service_headers(world.billing_key)).status_code == 403
    assert api.delete(path, headers=service_headers(world.notes_key)).status_code in (200, 204)
    assert api.get(path, headers=service_headers(world.notes_key)).status_code == 404


# ── Tier 2: custom roles (RBAC) ──────────────────────────────────────────────


def test_an_action_is_denied_until_a_role_grants_it(admin, notes, world, tokens, carol, dave):
    assert notes.get("/export", headers=carol).status_code == 403

    registered = [a for a in admin.get("/admin/service-actions") if a["service_name"] == world.notes]
    assert [a["action"] for a in registered] == ["notes:export"], "the SDK registers its actions at startup"
    role = admin.post(f"/admin/workspaces/{world.workspace_id}/roles", {"name": "Exporters"})
    admin.post(f"/admin/roles/{role['id']}/actions", {"service_action_ids": [registered[0]["id"]]})
    admin.post(f"/admin/roles/{role['id']}/members/{world.users['carol@duar.test']}")

    assert notes.get("/export", headers=carol).json() == {"exported_by": "carol@duar.test"}
    assert notes.get("/export", headers=dave).status_code == 403
    # A token minted from now on carries the action; no call back to Duar needed to read it.
    fresh = jwt.decode(tokens("carol@duar.test")["X-Authz-Token"], options={"verify_signature": False})
    assert fresh["actions"] == ["notes:export"]

    admin.call("DELETE", f"/admin/roles/{role['id']}/members/{world.users['carol@duar.test']}", 204)
    assert notes.get("/export", headers=carol).status_code == 403


# ── Proxy mode ───────────────────────────────────────────────────────────────


def test_proxy_mode_app_accepts_duar_access_tokens_only(world, alice, carol):
    duar = Duar(base_url=BASE, service_name=world.notes, service_key=world.notes_key, mode="proxy")
    app = FastAPI(lifespan=duar.lifespan)
    duar.protect(app)

    @app.get("/me")
    def me(user: AuthenticatedUser = Depends(duar.require_user)):
        return {"email": user.email, "role": user.workspace_role}

    with TestClient(app) as client:
        assert client.get("/me", headers=bearer(alice.access_token)).json() == {
            "email": "alice@duar.test",
            "role": "admin",
        }
        assert client.get("/me").status_code == 401
        assert client.get("/me", headers=bearer(alice.refresh_token)).status_code == 401
        assert client.get("/me", headers=bearer(carol["X-Authz-Token"])).status_code == 401


# ── Realms: app-to-app calls with no user ────────────────────────────────────


def test_realm_members_trust_each_others_machine_tokens_and_nobody_elses(world, notes_duar):
    billing = authz_mode_duar(world.billing, world.billing_key)
    ledger = authz_mode_duar(world.ledger, world.ledger_key)

    async def scenario():
        for duar in (billing, ledger, notes_duar):
            await duar.fetch_duar_public_key()
            await duar.fetch_whoami()
        token = await billing.mint_m2m_token()
        system = ledger.verify_m2m_token(token)
        with pytest.raises(DuarError) as wrong_realm:
            notes_duar.verify_m2m_token(token)
        with pytest.raises(DuarError) as standalone:
            await notes_duar.mint_m2m_token()
        return system, wrong_realm.value, standalone.value

    system, wrong_realm, standalone = asyncio.run(scenario())
    assert (billing.effective_scope, ledger.effective_scope) == (world.realm, world.realm)
    assert (system.caller, system.svc) == (world.billing, world.realm)
    assert wrong_realm.status_code == 403
    assert standalone.status_code == 403
