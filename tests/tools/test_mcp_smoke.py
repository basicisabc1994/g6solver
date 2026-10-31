# SPDX-License-Identifier: Apache-2.0
"""Gate 5 (P1): every operation P1 added answers through ``g6_invoke`` as its component's own, in process and over a
real stdio MCP server, each skill example with one of the three states and never ``G6_E_NO_BLOCK``, and every
sensitive op refused (``tools/mcp_smoke.py``; CI runs the tool against an installed wheel on Linux and Windows)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from g6.core import discovery
from g6.mcp import invoke_reply

TOOLS = Path(__file__).resolve().parents[2] / "tools"
_spec = importlib.util.spec_from_file_location("mcp_smoke", TOOLS / "mcp_smoke.py")
mcp_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_smoke)


def test_every_new_operation_is_its_components_own():
    assert {n: sorted(discovery.ops(n)) for n in mcp_smoke.EXPECTED_OPS} == \
        {n: sorted(o) for n, o in mcp_smoke.EXPECTED_OPS.items()}


def test_every_new_operation_answers_through_g6_invoke_in_process(monkeypatch, tmp_path):     # the P1 exit
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    assert mcp_smoke.check(lambda c, o, p: invoke_reply(c, o, p)) == []


def test_a_missing_op_or_a_no_block_reply_is_a_problem():
    def broken(component, op, params):
        if op == "ops":
            return {"result": {"ops": []}}
        return {"completion_state": "blocked-escalated", "warning_card": {"code": "G6_E_NO_BLOCK"}, "ok": False}
    problems = mcp_smoke.check(broken)
    assert problems and any("G6_E_NO_BLOCK" in p for p in problems)


def test_the_same_answers_come_over_a_real_stdio_server(tmp_path):
    done = subprocess.run([sys.executable, str(TOOLS / "mcp_smoke.py"), "--server", sys.executable, "-P", "-c",
                           "from g6.mcp import main; main()"], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300, cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
