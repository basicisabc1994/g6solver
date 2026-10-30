# SPDX-License-Identifier: Apache-2.0
"""G6's second MCP server, ``g6.abstraction.server``, started in a project's folder (the security review of W1-W5, F3).

It was documented as ``python -m g6.abstraction.server``: ``-m`` puts the folder it starts in first on the import path,
so a project's own packages would replace G6's (W1). And the mcp SDK's FastMCP builds its settings with
``env_file=".env"``, read from that folder, the project's, whatever ``FASTMCP_ENV_FILE`` says (W5). The documented
start is ``python -P -m g6.abstraction.server``, and the server reads no ``.env`` from the project. Each start here is
watched by a ``sitecustomize`` outside the project: it records every file the server opens, every connection and name
lookup it tries (refused, loopback aside) and every module's file, when the server exits.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("mcp")

START = [sys.executable, "-P", "-m", "g6.abstraction.server"]
PLANTED = ("pydantic", "starlette", "anyio", "mcp")
PROBE = '''
import atexit, json, os, socket, sys

opened, attempts = [], []
LOCAL = (None, "", "localhost", "127.0.0.1", "::1")

def _hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, os.PathLike)):
        opened.append(os.path.abspath(os.fspath(args[0])))
sys.addaudithook(_hook)

def _host(value):
    return value[0] if isinstance(value, tuple) and value else value

def _refused(kind, original, where):
    def refuse(*args, **kwargs):
        if _host(where(args)) in LOCAL:
            return original(*args, **kwargs)
        attempts.append([kind, repr(where(args))])
        raise OSError("no network in this test")
    return refuse

socket.getaddrinfo = _refused("getaddrinfo", socket.getaddrinfo, lambda a: (a[0], a[1]) if len(a) > 1 else a[0])
socket.create_connection = _refused("create_connection", socket.create_connection, lambda a: a[0])
socket.socket.connect = _refused("connect", socket.socket.connect, lambda a: a[1])

def _dump():
    modules = {k: getattr(m, "__file__", None) for k, m in list(sys.modules.items())}
    with open(os.environ["G6_TEST_PROBE"], "w", encoding="utf-8") as out:
        json.dump({"opened": opened, "attempts": attempts, "modules": modules}, out)
atexit.register(_dump)
'''


def _start(command: list[str], root: Path) -> tuple[dict, dict]:
    """Start ``command`` in a project's folder that holds packages named like G6's dependencies and a ``.env``;
    initialize, list the tools, stop. What the probe saw, and the tool list's reply."""
    project, probe = root / "project", root / "probe"
    project.mkdir(parents=True)
    probe.mkdir()
    for name in PLANTED:
        (project / name).mkdir()
        (project / name / "__init__.py").write_text(
            f"open({str(root / ('canary_' + name))!r}, 'w').write(__file__)\n", encoding="utf-8")
    (project / ".env").write_text("FASTMCP_LOG_LEVEL=DEBUG\nFASTMCP_DEBUG=true\n", encoding="utf-8")
    (probe / "sitecustomize.py").write_text(PROBE, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("FASTMCP_")}
    env.update(PYTHONPATH=os.pathsep.join([str(probe), *filter(None, [os.environ.get("PYTHONPATH")])]),
               G6_HOME=str(root / "state"), G6_TEST_PROBE=str(root / "probe.json"))
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
    return json.loads((root / "probe.json").read_text(encoding="utf-8")), tools


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def test_the_documented_start_loads_g6s_own_packages_reads_no_project_env_and_makes_no_request(tmp_path):
    seen, tools = _start(START, tmp_path)
    assert "load_dataset" in {tool["name"] for tool in tools["result"]["tools"]}
    assert not list(tmp_path.glob("canary_*"))
    inside = _key(tmp_path / "project")
    for name in PLANTED:
        assert seen["modules"].get(name) and not _key(seen["modules"][name]).startswith(inside), name
    assert not [path for path in seen["opened"] if _key(path) == _key(tmp_path / "project" / ".env")]
    assert seen["attempts"] == []


def test_a_plain_m_start_in_that_project_imports_its_packages(tmp_path):
    # survival control: without -P the planted packages are live
    subprocess.run([sys.executable, "-c", "import pydantic"], cwd=_project_only(tmp_path), capture_output=True,
                   timeout=120, check=False)
    assert (tmp_path / "canary_pydantic").exists()


def _project_only(root: Path) -> Path:
    project = root / "project"
    (project / "pydantic").mkdir(parents=True)
    (project / "pydantic" / "__init__.py").write_text(
        f"open({str(root / 'canary_pydantic')!r}, 'w').write(__file__)\n", encoding="utf-8")
    return project


def test_the_server_settings_ignore_a_projects_env_file(tmp_path, monkeypatch):
    # in process: the settings FastMCP builds for the server name no env file
    from mcp.server.fastmcp import server as sdk

    import g6.abstraction.server  # noqa: F401  (building the server sets it)
    assert sdk.Settings.model_config.get("env_file") is None
