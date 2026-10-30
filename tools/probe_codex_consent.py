"""Exercise an installed Codex app server against G6 without starting a model turn.

Uses temporary configuration; responses are automated, not authenticated human consent.
Run with the G6 interpreter: python tools/probe_codex_consent.py --output result.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from g6.core.process_tree import _Tree


def probe() -> dict:
    executable = shutil.which("codex.cmd" if os.name == "nt" else "codex")
    if not executable:
        raise RuntimeError("Codex CLI is not installed")
    version = subprocess.check_output([executable, "--version"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="g6-client-probe-") as folder:
        root = Path(folder)
        config = root / "codex"
        config.mkdir()
        # No credentials or user's MCP configuration are copied into this home.
        (config / "config.toml").write_text(
            '[mcp_servers.g6]\ncommand = ' + json.dumps(sys.executable) + '\nargs = '
            + json.dumps(["-P", "-c", "from g6.mcp import main; main()"])
            + '\n[mcp_servers.g6.env]\nG6_HOME = ' + json.dumps(str(root / "state"))
            + '\nG6_DISABLE_LLM = "1"\n', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not any(
            part in k.upper() for part in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))}
        env["CODEX_HOME"] = str(config)
        with (root / "stderr.log").open("w", encoding="utf-8") as stderr:
            tree = _Tree([executable, "app-server"], cwd=root, env=env, stderr=stderr)
            process = tree.proc
            messages = queue.Queue()

            def reader():
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                messages.put({"error": "app server exited"})

            threading.Thread(target=reader, daemon=True).start()
            serial = 0
            consent = "decline"
            prompts = []

            def send(value):
                process.stdin.write(json.dumps(value) + "\n")
                process.stdin.flush()

            def request(method, params):
                nonlocal serial
                serial += 1
                wanted = serial
                send({"id": wanted, "method": method, "params": params})
                deadline = time.monotonic() + 90
                while True:
                    event = messages.get(timeout=max(0, deadline - time.monotonic()))
                    if event.get("method") == "mcpServer/elicitation/request":
                        prompt = event["params"]
                        prompts.append(prompt)
                        schema = prompt.get("requestedSchema", {})
                        content = {key: "automated-protocol-probe" for key in schema.get("properties", {})}
                        send({"id": event["id"], "result": {"action": consent,
                              "content": content if consent == "accept" else None}})
                    elif event.get("id") == wanted:
                        if "error" in event:
                            raise RuntimeError(event["error"])
                        return event["result"]
                    elif event.get("error"):
                        raise RuntimeError(event["error"])

            try:
                request("initialize", {"clientInfo": {"name": "g6-release-probe", "version": "1"},
                                       "capabilities": {"experimentalApi": True,
                                                        "mcpServerOpenaiFormElicitation": True}})
                send({"method": "initialized", "params": {}})
                thread = request("thread/start", {"cwd": str(root), "approvalPolicy": "on-request",
                                                   "sandbox": "read-only", "ephemeral": True})
                thread_id = thread["thread"]["id"]

                def call(tool, arguments):
                    reply = request("mcpServer/tool/call", {"threadId": thread_id, "server": "g6",
                                                           "tool": tool, "arguments": arguments})
                    if reply.get("isError"):
                        raise RuntimeError(reply)
                    if reply.get("structuredContent") is not None:
                        return reply["structuredContent"]
                    return json.loads(next(c["text"] for c in reply["content"] if c["type"] == "text"))

                status = request("mcpServerStatus/list", {"threadId": thread_id, "serverName": "g6"})
                spec = {"name": "consent_probe", "task": "Return {input}",
                        "cases": [{"input": str(i), "expected": str(i)} for i in range(5)],
                        "baseline_code": {"main.py": "def solve(task):\n    return task\n"}, "check": "exact"}
                started = call("metaharness_start", {"specification_json": json.dumps(spec)})
                arguments = {"workflow": spec["name"], "workflow_digest": started["workflow_digest"]}
                results = {}
                for consent in ("decline", "cancel", "accept"):
                    results[consent] = call("metaharness_confirm", arguments)
                if not prompts:
                    assert all(r["completion_state"] == "blocked-escalated" for r in results.values()), results
                    assert all(r["warning_card"]["code"] == "G6_E_META_APPROVAL_REQUIRED"
                               for r in results.values()), results
                    return {"client": version, "model_calls": 0, "human_ui_tested": False,
                            "status": "elicitation-unavailable-direct-call", "fail_closed": True,
                            "prompt_count": 0, "responses": results}
                assert len(prompts) == 3, {"prompts": prompts, "results": results}
                assert all(arguments["workflow_digest"] in p["message"] for p in prompts)
                assert all(results[a]["completion_state"] == "blocked-escalated" for a in ("decline", "cancel"))
                assert results["accept"]["completion_state"] != "blocked-escalated", results
                return {"client": version, "model_calls": 0, "human_ui_tested": False,
                        "authenticated_identity": False, "prompt_digest_bound": True,
                        "status": "protocol-pass", "responses": results,
                        "server_discovered": bool(status.get("data"))}
            finally:
                tree.kill(strict=True)
                time.sleep(0.5)  # Windows may release killed descendants' file handles asynchronously.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(result["status"] + ": no model calls; not a human UI trial")
