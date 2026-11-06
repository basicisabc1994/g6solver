# SPDX-License-Identifier: Apache-2.0
import json

from g6.cli import main
from g6.metaharness.declarative import create
from g6.metaharness.surface import Surface


def test_the_terminal_records_no_confirmation_and_says_it_is_given_in_the_dialog(tmp_path, monkeypatch, capsys):
    """Changed on purpose (owner's standing rule: consent through the client's dialog, no terminal fallback): this
    pinned ``g6 workflow confirm --as`` recording a confirmation."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    entry = create({"name": "labels", "task": "Label {input}",
                    "cases": [{"input": "a", "expected": "x"}, {"input": "b", "expected": "y"}],
                    "baseline_code": {"main.py": "def solve(task):\n    return 'x'\n"}}, tmp_path / ".g6/workflows")
    assert main(["workflow", "confirm", "labels", entry["workflow"].digest(), "--as", "Ada"]) == 1
    got = json.loads(capsys.readouterr().out)
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_CONSENT_DIALOG_ONLY"
    assert Surface().inspect("labels")["confirmed"] is None
