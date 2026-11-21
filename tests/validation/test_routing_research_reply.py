# SPDX-License-Identifier: Apache-2.0
"""Actual offline pre-start probe with stand-in client identities and fresh synthetic homes."""
from __future__ import annotations

from pathlib import Path
import importlib.util

import pytest

spec = importlib.util.spec_from_file_location("routing_research_audit", Path(__file__).with_name("test_routing_research_audit.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
run = audit.run


@pytest.mark.parametrize("client", ["claude_code", "codex"])
def test_merged_reply_delivery_admits_granted_session(tmp_path: Path, monkeypatch, client):
    from g6.core import cloud_consent as cloud
    from g6.mcp import review_reply

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "add.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8", newline="\n")
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    # Keep the real server, job capture/poll, consent egress and runner admission. Only the review body
    # stands in: it echoes the initiating input without git discovery, subprocesses or model work.
    monkeypatch.setattr(review_reply, "review", lambda repo, **kwargs: {"ok": True, "echo": kwargs["task"]})
    task = run.load_moment()["tasks"][0]
    withheld = run.load_withheld()
    identity = run.CLIENTS[client]
    sources = ["agent_environment", f"routing-fixture.{task['fixture']}", cloud.repository_source(workspace),
               cloud.repository_source(home / run.REVIEWS), *withheld["granted"]]
    for source in sources:
        assert cloud._grant(source, identity.provider, cloud.preview(source, identity.provider)[1],
                            "synthetic stand-in person") == "granted"
    session = {}
    run._ready(task, home, workspace, withheld, (), identity, session)
    assert session["reply_probe"]["reply"] == "returned", session
    assert not session["reply_probe"]["unshared"]
