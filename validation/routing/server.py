# SPDX-License-Identifier: Apache-2.0
"""The MCP server a routing trial's session gets: one of G6's own servers, built exactly as G6 builds it, with the
task's withheld tools removed before it is served.

A missing-tool task measures what an agent does when the skill names a tool the person's server does not list (an
older ``g6-mcp``, or a tool the host did not allow). Removing the tool here leaves the product's server untouched:

    python -P validation/routing/server.py <server id>      # G6_ROUTING_WITHHOLD=tool,tool
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import sys
from collections.abc import Collection
from typing import Any

WITHHOLD_ENV = "G6_ROUTING_WITHHOLD"


def build(server_id: str, withhold: Collection[str] = ()) -> Any:
    """The known server ``server_id`` without the tools named in ``withhold`` that it registers. A name it does not
    register is left alone: that tool is absent from this server already."""
    from g6.mcp import registrations

    server = _own(registrations.SERVERS[server_id])
    for name in sorted(set(withhold) & set(registrations._tools(server))):
        remove = getattr(server, "remove_tool", None) or getattr(server._tool_manager, "remove_tool", None)
        if remove is not None:
            remove(name)
        else:
            server._tool_manager._tools.pop(name)
    left = set(withhold) & set(registrations._tools(server))
    if left:
        raise RuntimeError(f"could not withhold {sorted(left)} from the server {server_id}")
    return server


def _own(target: str) -> Any:
    """A server object of this process's own to withhold from. A factory builds one; a server that is a module's
    attribute is made again from a private copy of that module, so the product's object keeps every tool."""
    module_name, _, attribute = target.partition(":")
    found = getattr(importlib.import_module(module_name), attribute)
    if inspect.isfunction(found) or inspect.isclass(found):
        return found()
    origin = importlib.util.find_spec(module_name).origin
    spec = importlib.util.spec_from_file_location(module_name + "__routing_trial", origin)
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    return getattr(copy, attribute)


def main() -> None:
    withhold = [name.strip() for name in os.environ.get(WITHHOLD_ENV, "").split(",") if name.strip()]
    build(sys.argv[1], withhold).run()


if __name__ == "__main__":
    main()
