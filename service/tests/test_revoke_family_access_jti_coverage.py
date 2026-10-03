"""Regression test: ``revoke_token_family`` must blacklist every access_jti
ever issued in the family, not only the access_jti bound to the currently-live
(unconsumed) refresh record.

Vulnerability: V16 plumbs ``access_jti`` into the stored refresh record so
``revoke_token_family`` can blacklist the paired access token on reuse
detection. But ``consume_refresh_token`` uses ``getdel`` to atomically delete
the ``rt:{jti}`` record on rotation — so once a refresh step is rotated past,
its paired ``access_jti`` is unrecoverable. When the family is later revoked,
``revoke_token_family`` only finds the *currently-live* refresh record and
blacklists only that access_jti. Every pre-rotation access token still within
its ~15-min TTL survives the "revocation."

Concrete attack: attacker exfiltrates ``(RT_1, A_1)`` via XSS at T0. At T1 the
attacker rotates ``RT_1 → (RT_2, A_2)`` — ``rt:J_1`` is ``getdel``'d, so A_1's
jti is erased from Redis. At T2 the legitimate user replays ``RT_1``; reuse
detection fires and ``revoke_token_family`` runs. Only A_2 gets blacklisted.
The attacker continues using A_1 (still in their possession from T0) against
the relying party until A_1 naturally expires.

Fix: track every minted ``access_jti`` in a per-family Redis set, and blacklist
all members on family revocation.
"""

from __future__ import annotations

import uuid

import pytest

from src.services import token_service


# ---------------------------------------------------------------------------
# Minimal in-memory Redis double
#
# Only implements the surface used by token_service. Kept deliberately small —
# no TTL enforcement, no eviction — so tests pin behavior without depending on
# a real Redis container.
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, store: "FakeRedis") -> None:
        self._store = store
        self._ops: list[tuple] = []

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value))
        return self

    def sadd(self, key, *values):
        self._ops.append(("sadd", key, values))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def delete(self, *keys):
        self._ops.append(("delete", keys))
        return self

    async def execute(self):
        results: list = []
        for op in self._ops:
            if op[0] == "set":
                self._store._kv[op[1]] = op[2]
                results.append(True)
            elif op[0] == "sadd":
                s = self._store._kv.setdefault(op[1], set())
                assert isinstance(s, set)
                before = len(s)
                s.update(op[2])
                results.append(len(s) - before)
            elif op[0] == "expire":
                results.append(True)
            elif op[0] == "delete":
                n = 0
                for k in op[1]:
                    if k in self._store._kv:
                        del self._store._kv[k]
                        n += 1
                results.append(n)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self._kv: dict = {}

    def pipeline(self):
        return _FakePipeline(self)

    async def get(self, key):
        v = self._kv.get(key)
        return v if isinstance(v, str) else None

    async def getdel(self, key):
        v = self._kv.get(key)
        if isinstance(v, str):
            del self._kv[key]
            return v
        return None

    async def set(self, key, value, ex=None):
        self._kv[key] = value

    async def sadd(self, key, *values):
        s = self._kv.setdefault(key, set())
        assert isinstance(s, set)
        before = len(s)
        s.update(values)
        return len(s) - before

    async def smembers(self, key):
        v = self._kv.get(key)
        return v if isinstance(v, set) else set()

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                n += 1
        return n

    async def exists(self, key):
        return 1 if key in self._kv else 0


@pytest.fixture
def fake_redis(monkeypatch):
    """Swap token_service's module-level _redis for a fresh in-memory fake."""
    fake = FakeRedis()
    monkeypatch.setattr(token_service, "_redis", fake)
    return fake


@pytest.mark.asyncio
async def test_revoke_family_blacklists_pre_rotation_access_jti(fake_redis):
    """After family revocation, the PRE-rotation access_jti must be denylisted.

    This is the exact window V16's CHANGELOG claims to close: "the attacker's
    minted access token stayed valid for its full TTL (up to 15 min) after the
    family was killed." Without tracking every access_jti in the family, the
    pre-rotation jti is unrecoverable by the time revocation runs.
    """
    family_id = "fam-revoke-pre-rotation"
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    # Step 1 — issue initial refresh token RT_1 bound to access A_1.
    await token_service.store_refresh_token(
        jti="J1",
        user_id=user_id,
        family_id=family_id,
        workspace_id=workspace_id,
        access_jti="A1",
    )

    # Step 2 — attacker rotates RT_1. consume_refresh_token getdel's rt:J1 so
    # A1's jti is now unrecoverable from the refresh record.
    consumed = await token_service.consume_refresh_token("J1")
    assert consumed is not None
    await token_service.store_refresh_token(
        jti="J2",
        user_id=user_id,
        family_id=family_id,
        workspace_id=workspace_id,
        access_jti="A2",
    )

    # Step 3 — legitimate user replays RT_1 → reuse → revoke_token_family.
    await token_service.revoke_token_family(family_id)

    # Both A1 (pre-rotation) and A2 (current) MUST be blacklisted. Without the
    # fix, A1 survives until its natural TTL, keeping the attacker's captured
    # token usable.
    assert await token_service.is_access_token_blacklisted("A1"), (
        "Pre-rotation access_jti A1 was not blacklisted. The attacker's "
        "stolen access token remains valid until its natural TTL expiry — "
        "V16's revocation guarantee does not hold for rotated-past tokens."
    )
    assert await token_service.is_access_token_blacklisted("A2"), (
        "Current access_jti A2 was not blacklisted."
    )


@pytest.mark.asyncio
async def test_revoke_family_blacklists_all_access_jtis_across_long_chain(
    fake_redis,
):
    """Across a multi-step rotation chain, every access_jti must survive into
    the family's blacklist even after the refresh records it was bound to have
    been consumed."""
    family_id = "fam-long-chain"
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    steps = [(f"J{i}", f"A{i}") for i in range(1, 6)]

    # Walk the rotation chain: each step stores a new refresh record and
    # consumes the previous one.
    for i, (jti, ajti) in enumerate(steps):
        await token_service.store_refresh_token(
            jti=jti,
            user_id=user_id,
            family_id=family_id,
            workspace_id=workspace_id,
            access_jti=ajti,
        )
        if i > 0:
            prev_jti = steps[i - 1][0]
            await token_service.consume_refresh_token(prev_jti)

    # After the chain, only J5's refresh record is live in Redis. A1..A4 are
    # the ones that would be missed without the fix.
    await token_service.revoke_token_family(family_id)

    not_blacklisted: list[str] = []
    for _, ajti in steps:
        if not await token_service.is_access_token_blacklisted(ajti):
            not_blacklisted.append(ajti)

    assert not_blacklisted == [], (
        f"These pre-rotation access_jtis survived family revocation: "
        f"{not_blacklisted}. Attacker holding any of them has the remainder of "
        f"the access-token TTL after the family was 'killed'."
    )
