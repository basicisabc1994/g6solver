# SPDX-License-Identifier: Apache-2.0
"""G6's MCP server makes no network request when it starts or lists its tools (worked examples, W5).

FastMCP asks PyPI for its latest version each time a server starts and shows its banner, unless its
``check_for_updates`` setting is off; and FastMCP reads its settings from a ``.env`` file in the folder the server
starts in, which is the project's, where ``FASTMCP_SERVER_AUTH`` names a class it imports and builds while the server
is created. G6 turns both off for its own server, keeping what the person set in the environment. Each test starts
the exact command ``g6 setup`` registers, in a project's folder, with every outbound connection and name lookup
recorded and refused (a ``sitecustomize`` outside the project, on the import path), and FastMCP's home in the test's
folder, so no cached answer hides a request.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from g6.cli import main

PROBE = '''
import atexit, json, os, socket

attempts = []
LOCAL = (None, "", "localhost", "127.0.0.1", "::1")

def _host(value):
    return value[0] if isinstance(value, tuple) and value else value

def _refused(kind, original, where):
    # loopback is let through: asyncio's socket pair on Windows connects over it
    def refuse(*args, **kwargs):
        if _host(where(args)) in LOCAL:
            return original(*args, **kwargs)
        attempts.append([kind, repr(where(args))])
        raise OSError("no network in this test")
    return refuse

socket.getaddrinfo = _refused("getaddrinfo", socket.getaddrinfo, lambda a: (a[0], a[1]) if len(a) > 1 else a[0])
socket.create_connection = _refused("create_connection", socket.create_connection, lambda a: a[0])
socket.socket.connect = _refused("connect", socket.socket.connect, lambda a: a[1])
socket.socket.connect_ex = _refused("connect_ex", socket.socket.connect_ex, lambda a: a[1])

def _dump():
    with open(os.environ["G6_TEST_NETWORK"], "w", encoding="utf-8") as out:
        json.dump(attempts, out)
atexit.register(_dump)
'''
#: A class a project's .env could name in FASTMCP_SERVER_AUTH: building it leaves a canary.
AUTH = '''
import os
from fastmcp.server.auth import AuthProvider

class Planted(AuthProvider):
    def __init__(self, *args, **kwargs):
        open(os.environ["G6_TEST_CANARY"], "w").write("built")
        super().__init__(*args, **kwargs)
'''


def _registered(home: Path, monkeypatch) -> list[str]:
    from g6.cli import setup
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("G6_HOME", str(home / "state"))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(setup, "STEPS", ())
    calls: list[list[str]] = []
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: f"/tools/{name}")
    monkeypatch.setattr("subprocess.run", lambda command, **kw: calls.append(command) or
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert main(["setup", "--client", "claude", "--refuse-unconfined", "--allow", "patch_review.review"]) == 0
    command, = calls
    return command[command.index("--") + 1:]


def _start(command: list[str], root: Path, *, dotenv: str = "", **environment: str) -> tuple[list, dict]:
    """Start ``command`` in a project's folder (with ``dotenv`` as its .env), initialize, list the tools, stop; the
    connections and name lookups it attempted, and the tool list's reply."""
    project, probe = root / "project", root / "probe"
    project.mkdir(parents=True)
    probe.mkdir()
    if dotenv:
        (project / ".env").write_text(dotenv, encoding="utf-8")
    (probe / "sitecustomize.py").write_text(PROBE, encoding="utf-8")
    (probe / "g6_test_auth.py").write_text(AUTH, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("FASTMCP_")}
    env.update(PYTHONPATH=os.pathsep.join([str(probe), *filter(None, [os.environ.get("PYTHONPATH")])]),
               G6_HOME=str(root / "state"), FASTMCP_HOME=str(root / "fastmcp"), G6_DISABLE_LLM="1",
               G6_TEST_NETWORK=str(root / "network.json"), G6_TEST_CANARY=str(root / "canary"), **environment)
    replies: queue.Queue = queue.Queue()
    with (root / "stderr.log").open("w", encoding="utf-8") as stderr:
        proc = subprocess.Popen(command, cwd=project, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=stderr, text=True, encoding="utf-8")

        def read() -> None:
            for line in proc.stdout:
                try:
                    replies.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            replies.put(None)

        def ask(message: dict) -> dict | None:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            while "id" in message:
                reply = replies.get(timeout=120)
                assert reply is not None, (root / "stderr.log").read_text(encoding="utf-8")[-2000:]
                if reply.get("id") == message["id"]:
                    return reply
            return None

        threading.Thread(target=read, daemon=True).start()
        try:
            ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
            ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            proc.stdin.close()
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
    return json.loads((root / "network.json").read_text(encoding="utf-8")), tools


def test_the_registered_server_starts_and_lists_its_tools_with_no_network_request(tmp_path, monkeypatch):
    attempts, tools = _start(_registered(tmp_path / "home", monkeypatch), tmp_path)
    assert "g6_review_patch" in {tool["name"] for tool in tools["result"]["tools"]}
    assert attempts == []


def test_the_console_script_and_the_module_start_make_none_either(tmp_path, monkeypatch):
    # the g6-mcp console script runs ``sys.exit(main())``; where it is installed beside this Python, it runs as it is
    script = shutil.which("g6-mcp", path=str(Path(sys.executable).parent))
    starts = {"module": [sys.executable, "-c", "from g6.mcp import main; main()"],
              "script": [script] if script else [sys.executable, "-P", "-c",
                                                 "import sys; from g6.mcp import main; sys.exit(main())"]}
    for name, command in starts.items():
        attempts, tools = _start(command, tmp_path / name)
        assert tools["result"]["tools"] and attempts == [], (name, attempts)


def test_a_projects_dotenv_neither_turns_the_check_on_nor_configures_g6s_server(tmp_path, monkeypatch):
    dotenv = "FASTMCP_CHECK_FOR_UPDATES=stable\nFASTMCP_SERVER_AUTH=g6_test_auth.Planted\n"
    attempts, tools = _start(_registered(tmp_path / "home", monkeypatch), tmp_path, dotenv=dotenv)
    assert "g6_review_patch" in {tool["name"] for tool in tools["result"]["tools"]}
    assert attempts == [] and not (tmp_path / "canary").exists()


def test_the_persons_own_choice_to_check_for_updates_is_kept(tmp_path, monkeypatch):
    # survival control: the probe sees FastMCP's request when the check is on
    attempts, _ = _start(_registered(tmp_path / "home", monkeypatch), tmp_path, FASTMCP_CHECK_FOR_UPDATES="stable")
    assert attempts and all(kind in ("getaddrinfo", "create_connection", "connect", "connect_ex")
                            for kind, _ in attempts)
    assert any("pypi.org" in where for _, where in attempts), attempts
