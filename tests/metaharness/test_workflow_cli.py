# SPDX-License-Identifier: Apache-2.0
import json

from g6.cli import main
from g6.metaharness.declarative import create


def test_terminal_confirmation_checks_digest_and_records_consent(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    entry = create({"name": "labels", "task": "Label {input}",
                    "cases": [{"input": "a", "expected": "x"}, {"input": "b", "expected": "y"}],
                    "baseline_code": {"main.py": "def solve(task):\n    return 'x'\n"}}, tmp_path / ".g6/workflows")
    assert main(["workflow", "confirm", "labels", "wrong", "--as", "Ada"]) == 1
    capsys.readouterr()
    assert main(["workflow", "confirm", "labels", entry["workflow"].digest(), "--as", "Ada"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["completion_state"] == "qualified-draft"
