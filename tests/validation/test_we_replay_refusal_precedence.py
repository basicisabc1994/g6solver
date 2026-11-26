# SPDX-License-Identifier: Apache-2.0
"""Path refusals retain their reasons when index metadata is incomplete."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634")
RUN = "run/black-5634/one"
CHECK = "check/black-5634/one"


@pytest.mark.parametrize("row,reason", [
    ({"event": "started", "kind": "run", "run": "sealed/one"}, "forbidden archive path:"),
    ({"event": "started", "kind": "check", "run": CHECK, "checked_run": "sealed/one"},
     "forbidden archive path:"),
    ({"event": "started", "kind": "check", "run": "cohort/one"}, "invalid run path:"),
    ({"event": "started", "kind": "check", "run": CHECK, "checked_run": "cohort/one"},
     "invalid checked_run path:"),
    ({"event": "started", "kind": "unknown", "task": TASK.id, "run": RUN}, "invalid run path:"),
    ({"event": "started", "kind": "run", "task": TASK.id}, "invalid run path:"),
    ({"event": "ended", "run": 7, "status": "finished"}, "invalid run path:"),
    ({"event": "other", "run": "cohort/one"}, "invalid run path:"),
    ({"event": "other", "run": RUN}, "unknown index row shape:"),
    ({"event": "started", "kind": "check", "task": [], "run": CHECK, "checked_run": RUN},
     "unknown index row shape:"),
])
def test_path_rule_precedes_unknown_shape_in_api_and_cli(tmp_path, monkeypatch, capsys, row, reason):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    (source / "runs.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    read_bytes = rc.Source.read_bytes

    def index_only(reader, rel):
        assert str(rel) == "runs.jsonl", "candidate read before index refusal"
        return read_bytes(reader, rel)

    monkeypatch.setattr(rc.Source, "read_bytes", index_only)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    with pytest.raises(ValueError, match="^" + reason):
        rc.enumerate_cases(source, [TASK])
    assert rc.main(["--source", str(source), "--registered-commit", "a" * 40, "--dry"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("replay_rc: " + reason)
    assert not captured.out and not output.exists()
