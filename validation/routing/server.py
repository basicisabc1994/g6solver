# SPDX-License-Identifier: Apache-2.0
"""The MCP server a routing trial's session gets: one of G6's own servers, built exactly as G6 builds it, with the
task's withheld tools removed before it is served.

A missing-tool task measures what an agent does when the skill names a tool the person's server does not list (an
older ``g6-mcp``, or a tool the host did not allow). Removing the tool here leaves the product's server untouched:

    python -P validation/routing/server.py <server id>      # G6_ROUTING_WITHHOLD=tool,tool
"""
from __future__ import annotations

import os
import sys
from collections.abc import Collection
from typing import Any

WITHHOLD_ENV = "G6_ROUTING_WITHHOLD"


def build(server_id: str, withhold: Collection[str] = ()) -> Any:
    """The known server ``server_id`` without the tools named in ``withhold`` that it registers. A name it does not
    register is left alone: that tool is absent from this server already."""
    from g6.mcp import registrations

    server = registrations._load(registrations.SERVERS[server_id])
    for name in sorted(set(withhold) & set(registrations._tools(server))):
        remove = getattr(server, "remove_tool", None) or server._tool_manager.remove_tool
        remove(name)
    left = set(withhold) & set(registrations._tools(server))
    if left:
        raise RuntimeError(f"could not withhold {sorted(left)} from the server {server_id}")
    return server


def main() -> None:
    withhold = [name.strip() for name in os.environ.get(WITHHOLD_ENV, "").split(",") if name.strip()]
    build(sys.argv[1], withhold).run()


if __name__ == "__main__":
    main()
