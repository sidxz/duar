"""register_resource must validate the owner belongs to the workspace, mirroring
share_resource's grantee validation (defense-in-depth / data integrity)."""

import uuid

import pytest

from src.services.permission_service import register_resource


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return _Result(self._results.pop(0))

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_register_resource_rejects_non_member_owner():
    # First (and only) query — workspace-membership lookup — returns None.
    session = _FakeSession(results=[None])
    with pytest.raises(ValueError, match="member"):
        await register_resource(
            session,
            service_name="notes",
            resource_type="doc",
            resource_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
        )
