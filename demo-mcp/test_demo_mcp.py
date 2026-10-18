"""demo-mcp wiring tests — no live Duar required.

Proves the demo server is assembled correctly: both tools registered, the
resource-scoped tool bound via @duar_resource, and the binding's extractor pulls the
document id from the call arguments. The full allow/deny e2e needs a running Duar +
server + a real user token; see README.md ("End-to-end run").
"""

from __future__ import annotations

import server
from duar_auth.mcp import _RESOURCE_BINDINGS


async def test_both_tools_are_registered():
    tools = await server.mcp.list_tools(run_middleware=False)
    names = {t.name for t in tools}
    assert {"list_reports", "read_document"} <= names


def test_read_document_is_resource_scoped():
    binding = _RESOURCE_BINDINGS.get("read_document")
    assert binding is not None, "read_document should be bound by @duar_resource"
    resource_type, extractor, action = binding
    assert resource_type == "document"
    assert extractor({"doc_id": "abc-123"}) == "abc-123"


def test_list_reports_is_rbac_only():
    # No @duar_resource on the RBAC-only tool -> no entity-ACL check.
    assert "list_reports" not in _RESOURCE_BINDINGS
