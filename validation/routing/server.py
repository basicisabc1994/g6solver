# SPDX-License-Identifier: Apache-2.0
"""The MCP server a routing trial's session gets: one of G6's own servers, built exactly as G6 builds it, with the
task's withheld tools removed before it is served.

A missing-tool task measures what an agent does when the skill names a tool the person's server does not list (an
older ``g6-mcp``, or a tool the host did not allow). Removing the tool here leaves the product's server untouched:

    python -P validation/routing/server.py <server id>      # G6_ROUTING_WITHHOLD=tool,tool

Before each moment session the runner also builds it in its own process, in that session's G6 home, to ask whether a
review's reply would come back there (``probe``); the home is made again before the session starts.

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


#: The task a probe's review states: no session's own, so the probe's arguments are no session's.
PROBE_TASK = "routing trial probe: does a review's reply come back from this G6 home"
#: A client of the trials -> (the name it gives the server, the provider G6 reads from that name).
PROBE_CLIENTS = {"claude_code": ("claude-code", "anthropic:claude_code"), "codex": ("codex", "openai:codex")}


def probe(server_id: str, repo: str, client: str, seconds: float = 240.0) -> dict[str, Any]:
    """Ask this process's G6 home (``G6_HOME``) whether a review's reply comes back to ``client``: G6's own server,
    built as a trial session's is, takes one ``g6_review_patch`` on ``repo`` from that client over an in-memory MCP
    connection and its job is polled once it ends. The caller allows the review (``G6_MCP_SENSITIVE``) and switches
    model calls off. ``reply`` is ``returned``, ``withheld`` (G6's consent check withheld the poll's reply),
    ``unfinished`` or ``error``; ``named`` is every source the withheld reply names, ``unshared`` those this home's
    decisions do not share with the provider (the poll's own arguments aside: they are its literal input), and
    ``review_input`` the source of the review call's own arguments."""
    import asyncio
    import json
    import re
    import time

    from fastmcp import Client
    from mcp.types import Implementation

    from g6.core import cloud_consent as cloud

    name, provider = PROBE_CLIENTS[client]
    args = {"repo": str(repo), "task": PROBE_TASK}
    found: dict[str, Any] = {"reply": "error", "provider": provider, "code": "", "named": [], "unshared": [],
                             "review_input": cloud.input_source(json.dumps(args, sort_keys=True))}

    async def ask() -> tuple[dict[str, Any], str]:
        async with Client(build(server_id), client_info=Implementation(name=name, version="routing-probe")) as mcp:
            job = (await mcp.call_tool("g6_review_patch", args)).data
            ident, ends = str(job.get("job_id", "")), time.monotonic() + seconds
            reply = job
            while ident and time.monotonic() < ends:
                reply = (await mcp.call_tool("metaharness_job", {"job_id": ident})).data
                if reply.get("status") not in ("pending", "running"):
                    break
                await asyncio.sleep(0.2)
            return reply, ident

    try:
        reply, ident = asyncio.run(ask())
    except Exception as exc:  # noqa: BLE001 - whatever stops the probe, the session does not start on it
        return {**found, "why": type(exc).__name__}
    inner = reply.get("result") if isinstance(reply.get("result"), dict) else reply
    found["code"] = str((inner.get("warning_card") or {}).get("code", "")) if isinstance(inner, dict) else ""
    if reply.get("status") in ("pending", "running") or not ident:
        return {**found, "reply": "unfinished" if ident else "error"}
    if not (isinstance(inner, dict) and inner.get("cloud_consent_code") == cloud.REQUIRED):
        return {**found, "reply": "returned"}
    said = re.search(r"Withheld source identifiers: (.*?)\. Provider:", str(inner.get("error", "")))
    named = [part.strip() for part in said.group(1).split(",")] if said else []
    own = cloud.input_source(json.dumps({"job_id": ident}, sort_keys=True))
    return {**found, "reply": "withheld", "named": named,
            "unshared": [source for source in named if source != own and not cloud.is_granted(source, provider)]}


def main() -> None:
    withhold = [name.strip() for name in os.environ.get(WITHHOLD_ENV, "").split(",") if name.strip()]
    found, serves = admit(sys.argv[1])
    if not serves:
        raise SystemExit(f"the g6 package this interpreter imports has digest {found[:12]}, and the routing tasks "
                         f"were bound to {os.environ[BOUND_ENV][:12]}: nothing is served")
    build(sys.argv[1], withhold).run()


if __name__ == "__main__":
    main()
