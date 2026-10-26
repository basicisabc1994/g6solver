# SPDX-License-Identifier: Apache-2.0
"""The four demonstrations run and still show what they claim (examples/README.md)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _run(name: str, tmp_path: Path) -> str:
    env = dict(os.environ, G6_HOME=str(tmp_path / "home"))
    done = subprocess.run([sys.executable, str(EXAMPLES / f"{name}.py")], capture_output=True, text=True, env=env,
                          timeout=300)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_demo_1_memory_changes_the_next_session(tmp_path) -> None:
    out = _run("demo_1_persistent_state", tmp_path)
    labels = [line.split()[-1] for line in out.splitlines() if line.strip().startswith("label:")]
    assert labels == ["blocked-escalated", "blocked-escalated", "qualified-draft", "qualified-draft"]
    assert "G6_E_HARNESS_REMEMBERED_FAILURE" in out


def test_demo_2_blocks_then_revises_on_evidence(tmp_path) -> None:
    out = _run("demo_2_evidence_revision", tmp_path)
    labels = [line.split()[-1] for line in out.splitlines() if line.strip().startswith("label:")]
    assert labels == ["blocked-escalated", "qualified-draft", "blocked-escalated"]
    assert "numbers differ: claim has 14, source has 30" in out


@pytest.mark.skipif(shutil.which("git") is None, reason="demo 3 needs git")
def test_demo_3_rejects_the_bad_change_and_keeps_the_good_one(tmp_path) -> None:
    out = _run("demo_3_governed_change", tmp_path)
    assert "decision:       reject" in out and "rolled back" in out
    assert "decision:       canary" in out and "merged into the project" in out
    assert "Refunds are issued within 14 days" in out.split("Later task")[1]


def test_demo_4_discovers_and_composes(tmp_path) -> None:
    out = _run("demo_4_agent_discovery", tmp_path)
    assert "grounding" in out.split("g6_describe")[1].split("\n")[0]
    assert 'g6_describe("action_gating")' in out
    assert "decision: block" in out and "decision: require_human_review" in out
