"""demo-mcp host — a FastMCP client that drives the demo server on a user's behalf.

Stands in for a Duar-aware agent host: it carries the signed-in user's ``duar:access``
token and forwards it to the MCP server, which authorizes each tool call against Duar.

Config (env):
    MCP_URL              demo-mcp server URL (default http://127.0.0.1:9100/mcp)
    DUAR_ACCESS_TOKEN    the user's Duar access token (Bearer)
    DOC_ID               a document id to try read_document against

Run: see README.md.
"""

from __future__ import annotations

import asyncio
import os

from fastmcp import Client

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:9100/mcp")
TOKEN = os.environ.get("DUAR_ACCESS_TOKEN", "")
DOC_ID = os.environ.get("DOC_ID", "00000000-0000-4000-8000-000000000001")


async def main() -> None:
    async with Client(MCP_URL, auth=TOKEN) as client:
        tools = await client.list_tools()
        print("visible tools:", [t.name for t in tools])

        for name, args in [
            ("list_reports", {}),
            ("read_document", {"doc_id": DOC_ID}),
        ]:
            try:
                result = await client.call_tool(name, args)
                print(f"ALLOW  {name}{args} -> {result.data!r}")
            except Exception as exc:  # FastMCP surfaces a denied ToolError here
                print(f"DENY   {name}{args} -> {exc}")


if __name__ == "__main__":
    asyncio.run(main())
