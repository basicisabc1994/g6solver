"""A finite shared exception graph must not stall family discovery."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entry", ["family", "startup"])
def test_shared_exception_graph_finishes_promptly(entry):
    environment = os.environ.copy()
    environment.update(
        G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe",
        G6_CODEX=r"C:\nonexistent\g6-no-host.exe",
        PYTHONDONTWRITEBYTECODE="1",
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-B", "-u", str(Path(__file__).resolve()), "--probe", entry],
            env=environment, capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired as stopped:
        output = stopped.stdout or ""
        if isinstance(output, bytes):
            output = output.decode()
        assert "entered lookup" in output, "probe did not reach discovery"
        pytest.fail(f"{entry} stalled for 5 seconds on only 1,202 exception objects")
    assert completed.returncode == 0, completed.stderr
    assert "finished lookup" in completed.stdout


def _probe(entry):
    from g6 import mcp
    from g6.mcp import families, registrations

    disk = Path(__file__).resolve().parent / "_disk_graph"
    disk.mkdir(exist_ok=True)
    (disk / "rfu_graph_server.py").write_text(
        "def build():\n"
        "    failure = ModuleNotFoundError('missing optional', name='rfu_optional')\n"
        "    for _ in range(1200):\n"
        "        failure = ExceptionGroup('shared children', [failure, failure])\n"
        "    raise ImportError('dependency wrapper') from failure\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(disk))
    registrations.SERVERS = {"abstraction": "rfu_graph_server:build"}
    families._DETAILS = {}
    if entry == "startup":
        mcp._quiet = lambda: registrations._load("rfu_graph_server:build")
    print("entered lookup", flush=True)
    try:
        if entry == "family":
            families.describe("load_state")
        else:
            mcp.build_server()
    except (Exception, SystemExit):
        # Either a response or a propagated defect is responsive; a hang is not.
        pass
    print("finished lookup", flush=True)


if __name__ == "__main__" and sys.argv[1:2] == ["--probe"]:
    _probe(sys.argv[2])
