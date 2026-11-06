# SPDX-License-Identifier: Apache-2.0
"""A scripted host session for the replay cohort's round-trip control (``cohorts.round_trip``): it stands where the host
executor starts Claude Code, with no model. It is started with Claude Code's own command line (``run.host_args``) after
``--known PATCH``, in the run's workspace, and does what an agent's session would do there, through the same doors:

1. it writes the known patch into the workspace as a file, as Claude Code's Write tool would (``KNOWN``);
2. it starts the box from the run's MCP configuration, as Claude Code does, and calls its ``run`` once over JSON-RPC, with a
   command that applies that patch in the container and removes the file (``APPLY``), so every change the patch makes is
   written by a command inside the box;
3. it prints a stream as Claude Code's ``stream-json`` does: an init naming exactly the five file tools and the box, the box's
   reply, and a result.

The runner then keeps the patch as for any run (``run._run_task``); the control holds when that patch is the known patch, byte
for byte. Only the standard library, so ``-I`` holds.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

KNOWN = ".we-known.patch"
APPLY = f"git apply --binary --whitespace=nowarn {KNOWN} && rm -f {KNOWN}"
TOOLS = ["Edit", "Glob", "Grep", "Read", "Write", "mcp__box__run"]


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def box_call(config: dict, command: str) -> dict:
    """The box started as the MCP configuration names it, ``initialize``, the initialized notification, then one
    ``tools/call`` of ``run``: the call's result."""
    proc = subprocess.Popen([config["command"], *config["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "run", "arguments": {"command": command}}}]
    said, _ = proc.communicate(b"".join(json.dumps(m).encode("utf-8") + b"\n" for m in messages), timeout=1800)
    replies = [json.loads(line) for line in said.splitlines() if line.strip()]
    return next(r["result"] for r in replies if r.get("id") == 2)


def main(argv: list[str]) -> int:
    known = Path(argv[argv.index("--known") + 1])
    config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text("utf-8"))["mcpServers"]["box"]
    sys.stdin.buffer.read()                       # the prompt, which no model reads
    sid = f"scripted-{uuid.uuid4().hex}"
    emit({"type": "system", "subtype": "init", "session_id": sid, "claude_code_version": "scripted", "tools": TOOLS,
          "mcp_servers": [{"name": "box", "status": "connected"}]})
    Path(KNOWN).write_bytes(known.read_bytes())    # the Write tool's way: a file in the session's folder, the workspace
    got = box_call(config, APPLY)
    emit({"type": "user", "session_id": sid, "message": {"content": [{"type": "tool_result", "content": got.get("content")}]}})
    failed = bool(got.get("isError"))
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "num_turns": 1, "total_cost_usd": 0.0,
          "result": json.dumps({"box_error": failed, "said": (got.get("content") or [{}])[0].get("text", "")[-2000:]})})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
