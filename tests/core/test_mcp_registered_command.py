# SPDX-License-Identifier: Apache-2.0
"""The command ``g6 setup`` registers G6's MCP server with, started in a project's folder (worked examples, W1).

Claude Code and Codex start the server in the project's folder. ``python -c`` puts that folder first on the import
path, so in pydantic's repository G6 imported the repository's ``pydantic`` and its server failed to start, and in
starlette's repository the server imported and ran the repository's ``starlette``, unconfined, as the person. The
registered command starts Python with ``-P``, so the folder it starts in is never on its import path. Offline: no
model, no network.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cli import main

OSS = Path(__file__).resolve().parents[2]
#: Packages and a module named after dependencies G6's server imports when it starts.
PLANTED_PACKAGES = ("pydantic", "starlette", "anyio", "mcp")
PLANTED_MODULE = "typing_extensions"
PLANTED = (*PLANTED_PACKAGES, PLANTED_MODULE)


def _registered(home: Path, monkeypatch, client: str) -> list[str]:
    """The command setup registers for ``client``: everything after the ``--``."""
    from g6.cli import setup
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("G6_HOME", str(home / "state"))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(setup, "STEPS", ())
    calls: list[list[str]] = []
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: f"/tools/{name}")
    monkeypatch.setattr("subprocess.run", lambda command, **kw: calls.append(command) or
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert main(["setup", "--client", client, "--refuse-unconfined", "--allow", "patch_review.review"]) == 0
    command, = calls
    return command[command.index("--") + 1:]


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_setup_registers_the_server_so_the_folder_it_starts_in_is_never_on_its_import_path(tmp_path, monkeypatch,
                                                                                         client):
    assert _registered(tmp_path, monkeypatch, client) == [sys.executable, "-P", "-c", "from g6.mcp import main; main()"]


def _project(root: Path) -> Path:
    """A project whose top-level packages and module have the names of G6's own dependencies; each one, if imported,
    leaves a canary file beside the project."""
    project = root / "project"
    project.mkdir()
    for name in PLANTED:
        source = project / name / "__init__.py" if name in PLANTED_PACKAGES else project / f"{name}.py"
        source.parent.mkdir(exist_ok=True)
        source.write_text(f"open({str(root / ('canary_' + name))!r}, 'w').write(__file__)\n", encoding="utf-8")
    return project


def _environment(root: Path) -> dict[str, str]:
    """The test's environment for the server: G6's state in the test's folder, FastMCP's update check off (no
    network), and a ``sitecustomize`` outside the project that writes every loaded module's file when the server
    exits."""
    probe = root / "probe"
    probe.mkdir()
    (probe / "sitecustomize.py").write_text(
        "import atexit, json, os, sys\n"
        "def _dump():\n"
        "    with open(os.environ['G6_TEST_MODULES'], 'w', encoding='utf-8') as out:\n"
        "        json.dump({k: getattr(m, '__file__', None) for k, m in list(sys.modules.items())}, out)\n"
        "atexit.register(_dump)\n", encoding="utf-8")
    path = os.pathsep.join([str(probe), *filter(None, [os.environ.get("PYTHONPATH")])])
    return {**os.environ, "PYTHONPATH": path, "G6_HOME": str(root / "state"), "FASTMCP_CHECK_FOR_UPDATES": "off",
            "G6_TEST_MODULES": str(root / "modules.json"), "G6_DISABLE_LLM": "1"}


def test_a_plain_c_start_in_that_project_imports_its_packages(tmp_path):
    # survival control: the planted packages are live for a start without the flag
    project = _project(tmp_path)
    subprocess.run([sys.executable, "-c", f"import {', '.join(PLANTED)}"], cwd=project, env=_environment(tmp_path),
                   capture_output=True, timeout=120, check=False)
    assert sorted(p.name for p in tmp_path.glob("canary_*")) == sorted(f"canary_{name}" for name in PLANTED)


def _request(proc: subprocess.Popen, replies: queue.Queue, message: dict) -> dict | None:
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if "id" not in message:
        return None
    while True:
        reply = replies.get(timeout=120)
        if reply is None:
            raise AssertionError("the server closed its output before it replied")
        if reply.get("id") == message["id"]:
            return reply


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def test_the_registered_server_starts_in_a_project_that_shadows_its_dependencies_and_loads_its_own(tmp_path,
                                                                                                  monkeypatch):
    command = _registered(tmp_path / "home", monkeypatch, "claude")
    project = _project(tmp_path)
    replies: queue.Queue = queue.Queue()
    with (tmp_path / "stderr.log").open("w", encoding="utf-8") as stderr:
        proc = subprocess.Popen(command, cwd=project, env=_environment(tmp_path), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8")

        def read() -> None:
            for line in proc.stdout:
                try:
                    replies.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            replies.put(None)

        threading.Thread(target=read, daemon=True).start()
        try:
            started = _request(proc, replies, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
            _request(proc, replies, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = _request(proc, replies, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            proc.stdin.close()
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
    assert started["result"]["serverInfo"]["name"] == "g6"
    assert "g6_review_patch" in {tool["name"] for tool in tools["result"]["tools"]}
    assert not list(tmp_path.glob("canary_*"))
    modules = json.loads((tmp_path / "modules.json").read_text(encoding="utf-8"))
    for name in PLANTED:
        assert modules.get(name), f"the server never loaded {name}"
    inside = _key(project)
    assert not [name for name, where in modules.items() if where and _key(where).startswith(inside)]


#: Folders that are not G6's own text: environments, caches, and the tests, which hold examples of the defect.
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache", ".hypothesis",
        ".benchmarks", "tests"}
TEXT = {".md", ".py", ".html", ".txt", ".toml", ".json", ".yml", ".yaml", ".ps1", ".sh", ".cfg", ".ini"}
#: Python's flags before -c or -m; -X and -W take a value.
FLAGS = r"((?:\s-[XW]\s+\S+|\s-[A-Za-z]\w*)*)"
#: A Python start of G6's server, in shell lines, Python lists, JSON and TOML alike.
SERVER_START = re.compile(FLAGS + r"\s-(?:c\s+from\s+g6\.mcp\s+import|m\s+g6\.mcp\b)")
#: A Python start in a registration line (``claude mcp add ... -- python -c ...``).
REGISTERED_PYTHON = re.compile(r"\bpython[\w.]*" + FLAGS + r"\s-[cm]\b")
#: -P (or -I, which implies it), alone or among other short flags.
SAFE = re.compile(r"-(?![XW])[A-Za-z]*[PI][A-Za-z]*")


def _starts(line: str) -> list[str]:
    """The flags of each Python start of a server in ``line``."""
    plain = " " + " ".join(re.sub(r"[\"'`\[\](),=]", " ", line).split())
    starts = [m.group(1) for m in SERVER_START.finditer(plain)]
    if "mcp add" in plain:
        starts += [m.group(1) for m in REGISTERED_PYTHON.finditer(plain)]
    return starts


def _bare(flags: str) -> bool:
    return not any(SAFE.fullmatch(flag) for flag in flags.split())


def _documented() -> list[Path]:
    found = []
    for folder, folders, files in os.walk(OSS):
        folders[:] = [name for name in folders if name not in SKIP]
        found += [Path(folder) / name for name in files if Path(name).suffix in TEXT]
    return found


def test_no_documented_registration_starts_python_with_the_folder_on_its_import_path():
    files = _documented()
    assert OSS / "src" / "g6" / "cli" / "setup.py" in files and OSS / "README.md" in files
    bare = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "g6.mcp" not in text and "mcp add" not in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            bare += [f"{path.relative_to(OSS)}:{number}" for flags in _starts(line) if _bare(flags)]
    assert not bare, bare


@pytest.mark.parametrize("line", [
    'command += ["--", sys.executable, "-c", "from g6.mcp import main; main()"]',
    '+ json.dumps(["-c", "from g6.mcp import main; main()"])',
    '"args": ["-B", "-c", "from g6.mcp import main; main()"]',
    'claude mcp add g6 -- /venv/bin/python -c "from g6.mcp import main; main()"',
    "codex mcp add g6 -- python3 -m g6.mcp",
    "claude mcp add g6 -- python.exe -X utf8 -m some_server",
])
def test_the_scan_finds_each_form_of_a_bare_start(line):
    # survival control for the scan above
    assert _starts(line) and all(_bare(flags) for flags in _starts(line)), line


@pytest.mark.parametrize("line", [
    'command += ["--", sys.executable, "-P", "-c", "from g6.mcp import main; main()"]',
    "claude mcp add g6 -- python -I -m g6.mcp",
    "claude mcp add g6 -- python -X utf8 -P -c 'from g6.mcp import main; main()'",
    "claude mcp add g6 -- g6-mcp",
])
def test_the_scan_passes_a_start_with_the_flag(line):
    assert not any(_bare(flags) for flags in _starts(line)), line
