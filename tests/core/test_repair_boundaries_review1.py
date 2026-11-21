# SPDX-License-Identifier: Apache-2.0
"""Filesystem and transport acceptance checks for repair review 1 (synthetic sources only)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as policy
from g6.mcp import build_server


def test_undeclared_arguments_send_no_source_and_never_execute_tool(tmp_path, monkeypatch):
    from g6.mcp import repair

    home = tmp_path / "home"
    monkeypatch.setenv("G6_HOME", str(home))
    captures = []
    original = repair.block

    def watched(*args, **kwargs):
        captures.append(tuple(policy.current_capture().sources))
        return original(*args, **kwargs)

    monkeypatch.setattr(repair, "block", watched)

    async def run():
        async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as client:
            return await client.call_tool("g6_cloud_grant", {
                "source": "synthetic-secret", "provider": "openai:codex", "answer": "agent cannot accept"},
                raise_on_error=False)

    result = asyncio.run(run())
    assert result.is_error
    reply = result.structured_content
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["repair"]["code"] == "G6_E_INPUT_VALIDATION"
    # since 3d45b793 any refusal of g6_cloud_grant is the person's, with no call to it: an undeclared argument there
    # never yields a call that reopens the dialog
    assert reply["repair"]["fix_by"] == "person" and reply["repair"]["call"] is None
    assert reply["repair"]["fill"] == []
    assert "synthetic-secret" not in json.dumps(reply)
    assert captures == [()]
    assert not list(home.rglob("*")), "No grant, disclosure, source record or tool state is written"


def test_cross_process_log_appends_keep_all_twelve_thousand_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    code = '''
import sys
from g6.core.repair_log import append, flush
for index in range(1500):
    append({"event": "refused", "id": "%s:%s" % (sys.argv[1], index),
            "code": "G6_E_INPUT_VALIDATION", "tool": "g6_select"})
assert flush(110)
'''
    processes = []
    try:
        for index in range(8):
            processes.append(subprocess.Popen([sys.executable, "-c", code, str(index)], env=dict(os.environ)))
        assert all(process.wait(timeout=120) == 0 for process in processes)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    rows = (tmp_path / "home/repairs/repairs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == len({json.loads(row)["id"] for row in rows}) == 12000
