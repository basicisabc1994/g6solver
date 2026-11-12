# SPDX-License-Identifier: Apache-2.0
"""The MCP server a routing trial's session gets: one of G6's own servers, built exactly as G6 builds it, with the
task's withheld tools removed before it is served.

A missing-tool task measures what an agent does when the skill names a tool the person's server does not list (an
older ``g6-mcp``, or a tool the host did not allow). Removing the tool here leaves the product's server untouched:

    python -P validation/routing/server.py <server id>      # G6_ROUTING_WITHHOLD=tool,tool

Which G6 serves: the runner binds its tasks to the ``g6`` package it imported and hands this process that package's
digest (``G6_ROUTING_G6``). This process is started by the client and imports whatever ``g6`` its interpreter finds;
when that package's digest is another, it serves nothing and exits. Either way it writes which package it found to
``routing-served-<server id>.json`` in its G6 home, which the runner copies into the session's record.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any

WITHHOLD_ENV = "G6_ROUTING_WITHHOLD"
#: The digest of the ``g6`` package the runner bound its tasks to (``validation/freeze.py``'s ``tree_digest``).
BOUND_ENV = "G6_ROUTING_G6"
#: The file, in the session's G6 home, that says which ``g6`` package this process found.
SERVED = "routing-served-{server}.json"


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


def _freeze() -> Any:
    name, path = "g6_validation_freeze", Path(__file__).resolve().parent.parent / "freeze.py"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def served_package() -> str:
    """The digest of the ``g6`` package this process imports."""
    import g6

    return _freeze().tree_digest(Path(g6.__file__).resolve().parent)


def admit(server_id: str, environ: Any = None) -> tuple[str, bool]:
    """(the digest of this process's ``g6`` package, whether it may serve). It may serve when no digest was handed
    to it (a server started by hand) or the one handed is its own. What it found is written to the G6 home it was
    given, when it was given one, whether it serves or not."""
    environ = os.environ if environ is None else environ
    found, bound = served_package(), environ.get(BOUND_ENV, "").strip()
    serves = not bound or found == bound
    home = environ.get("G6_HOME", "").strip()
    if home and Path(home).is_dir() and server_id.replace("_", "").replace("-", "").isalnum():
        Path(home, SERVED.format(server=server_id)).write_text(
            json.dumps({"server": server_id, "g6": found, "serves": serves}), encoding="utf-8")
    return found, serves


def main() -> None:
    withhold = [name.strip() for name in os.environ.get(WITHHOLD_ENV, "").split(",") if name.strip()]
    found, serves = admit(sys.argv[1])
    if not serves:
        raise SystemExit(f"the g6 package this interpreter imports has digest {found[:12]}, and the routing tasks "
                         f"were bound to {os.environ[BOUND_ENV][:12]}: nothing is served")
    build(sys.argv[1], withhold).run()


if __name__ == "__main__":
    main()
