# SPDX-License-Identifier: Apache-2.0
"""The installed-MCP smoke test of every operation P1 added (the outside review's gate 5).

For each component in ``EXPECTED_OPS`` it asks the server to describe it and requires a block, maturity ``beta`` and
exactly the expected ops; it runs every ``g6-example`` in the component's skill and requires a reply in one of the
three states whose code is not ``G6_E_NO_BLOCK``; and it requires every sensitive op to be refused with
``G6_E_SENSITIVE_COMPONENT_DENIED``, since the server it starts allows none. No model is called (``G6_DISABLE_LLM=1``)
and no key is read: the server runs with a fresh, empty ``G6_HOME``.

    python tools/mcp_smoke.py                                   # the g6-mcp script next to this Python
    python tools/mcp_smoke.py --server python -P -c "from g6.mcp import main; main()"

Exit status 0 when nothing is wrong, 1 with each problem printed, 2 when no server could be started.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

EXPECTED_OPS: dict[str, list[str]] = {
    "algorithm_design": ["primitives", "ontology", "search", "estimate", "fuse", "list_kernels", "run_kernel"],
    "complexity": ["atlas", "impact", "simplify"],
    "goal_engine": ["propose_goal_tree"],
    "self_maintenance": ["identify_constraint", "plan"],
    "align_prompt_library": ["put", "get", "history", "search", "render", "retire", "rollback", "catalogue",
                             "install_catalog"],
}
STATES = ("verified", "qualified-draft", "blocked-escalated")
DENIED = "G6_E_SENSITIVE_COMPONENT_DENIED"
_EXAMPLE = re.compile(r"^```g6-example op=([a-z_][a-z0-9_]*)[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)

Call = Callable[[str, str, str], dict]
Describe = Callable[[str], dict]


def _code(reply: dict) -> str:
    card = reply.get("warning_card") if isinstance(reply, dict) else None
    return str(card.get("code", "")) if isinstance(card, dict) else ""


def _in_process_describe(component: str) -> dict:
    from g6.mcp import describe_reply

    return describe_reply(component)


def check(call: Call, describe: Describe | None = None) -> list[str]:
    """Every problem with the P1 operations, as ``call(component, op, params_json)`` (``g6_invoke``) and ``describe``
    (``g6_describe``; G6's own in this process by default) answer them."""
    describe = describe or _in_process_describe
    problems: list[str] = []
    for component, expected in EXPECTED_OPS.items():
        info = describe(component) or {}
        if not info.get("block"):
            problems.append(f"{component}: g6_describe names no block")
        if info.get("maturity") != "beta":
            problems.append(f"{component}: maturity is {info.get('maturity')!r}, not 'beta'")
        listed = call(component, "ops", "{}")
        ops = (listed.get("result") or {}).get("ops") if isinstance(listed, dict) else None
        if sorted(ops or []) != sorted(expected):
            problems.append(f"{component}: its ops are {ops!r}, not {expected!r}")
        sensitive = set(info.get("sensitive_ops") or ())
        examples = _EXAMPLE.findall(str(info.get("skill") or ""))
        if not examples:
            problems.append(f"{component}: its skill shows no g6-example")
        for op, body in examples:
            try:
                params = json.loads(body)
            except json.JSONDecodeError:
                problems.append(f"{component}.{op}: its g6-example is not JSON")
                continue
            reply = call(component, op, json.dumps(params))
            problems += _reply_problems(component, op, reply, op in sensitive)
        for op in sorted(sensitive - {op for op, _ in examples}):
            problems += _reply_problems(component, op, call(component, op, "{}"), True)
    return problems


def _reply_problems(component: str, op: str, reply: Any, sensitive: bool) -> list[str]:
    if not isinstance(reply, dict):
        return [f"{component}.{op}: the reply is not an object"]
    if sensitive:
        return [] if _code(reply) == DENIED else [f"{component}.{op}: a sensitive op answered {_code(reply)!r}, not "
                                                  f"{DENIED}"]
    out = []
    if reply.get("completion_state") not in STATES:
        out.append(f"{component}.{op}: completion_state is {reply.get('completion_state')!r}")
    if _code(reply) == "G6_E_NO_BLOCK":
        out.append(f"{component}.{op}: answered G6_E_NO_BLOCK")
    return out


def _payload(result: Any) -> dict:
    """A tool call's answer as a dict, whichever way the client hands it over."""
    for attr in ("structured_content", "data"):
        value = getattr(result, attr, None)
        if isinstance(value, dict):
            return value.get("result", value) if set(value) == {"result"} and isinstance(value["result"], dict) \
                else value
    for part in getattr(result, "content", None) or ():
        text = getattr(part, "text", None)
        if text:
            try:
                found = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(found, dict):
                return found
    return {}


def _default_server() -> list[str]:
    folder = Path(sys.executable).parent
    found = shutil.which("g6-mcp", path=str(folder)) or shutil.which("g6-mcp", path=str(folder / "Scripts"))
    if not found:
        raise SystemExit(f"no g6-mcp script next to {sys.executable}; install G6 with its mcp extra, or pass --server")
    return [found]


def run_over_stdio(command: list[str]) -> list[str]:
    """``check`` against a real MCP server started over stdio with ``command``."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    home = tempfile.mkdtemp(prefix="g6-mcp-smoke-")
    env = {k: v for k, v in os.environ.items() if k not in ("G6_MCP_SENSITIVE", "G6_LLM_PROVIDER", "G6_LLM_MODEL")}
    env.update({"G6_DISABLE_LLM": "1", "G6_HOME": home})
    client = Client(StdioTransport(command=command[0], args=command[1:], env=env, cwd=home))
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(client.__aenter__())
        try:
            def call(component: str, op: str, params: str) -> dict:
                return _payload(loop.run_until_complete(client.call_tool(
                    "g6_invoke", {"component": component, "op": op, "params_json": params}, raise_on_error=False)))

            def describe(component: str) -> dict:
                return _payload(loop.run_until_complete(client.call_tool(
                    "g6_describe", {"component": component}, raise_on_error=False)))

            return check(call, describe)
        finally:
            loop.run_until_complete(client.__aexit__(None, None, None))
    finally:
        _close(loop)
        shutil.rmtree(home, ignore_errors=True)


def _close(loop: asyncio.AbstractEventLoop) -> None:
    """Close the loop once the transport's own tasks and threads have finished, so none calls into a closed loop."""
    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(asyncio.sleep(0.5))   # the subprocess reader threads hand back their last lines
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        loop.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", nargs=argparse.REMAINDER, help="the command that starts the MCP server")
    args = parser.parse_args(argv)
    command = args.server or _default_server()
    try:
        problems = run_over_stdio(command)
    except Exception as exc:  # noqa: BLE001 - a server that cannot start is what the smoke test reports
        print(f"the MCP server could not be used: {type(exc).__name__}: {exc}"[:500])
        return 2
    for line in problems:
        print(line)
    ops = sum(len(v) for v in EXPECTED_OPS.values())
    print(f"{len(problems)} problem(s) across {len(EXPECTED_OPS)} components and {ops} operations")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
