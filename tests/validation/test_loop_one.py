# SPDX-License-Identifier: Apache-2.0
"""A saved row's verdict is the verdict of that row's own run, even after a restart, with a later run of the same bug on record."""
import json
from types import SimpleNamespace

import compare
import loop
import run as we
import wecommon as c


def test_a_recovered_k1_row_is_not_scored_on_k0s_later_patch(tmp_path, monkeypatch):
    archive = c.Archive(tmp_path)
    task = SimpleNamespace(id="fake-1")
    archive.start("validate", task.id).finish({"fail_to_pass": ["test_issue"], "pass_to_pass": []}, "finished")
    runs = {}
    for arm in ("K1", "K0"):
        run = archive.start("run", task.id, arm=arm)
        run.write("final.patch", arm)
        run.finish({"arm": arm, "agent_started": True}, "finished")
        runs[arm] = run.rel
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"task": task.id, "arm": "K1", "repeat": 1, "run": runs["K1"], "status": "finished",
                                                       "cost_usd": 0.1}) + "\n", encoding="utf-8")
    ctx = SimpleNamespace(archive=archive)
    monkeypatch.setattr(we, "_oracle_run", lambda ctx, task, *, patch, **kw: {"results": {"test_issue": "passed" if patch.read_text() == "K0" else "failed"}})

    def tool(command, *args, **kwargs):
        if command == "run":
            return json.dumps({"status": "refused", "why": "run was already made (finished)"})
        return json.dumps(we.check(ctx, task, args[args.index("--run") + 1] if "--run" in args else None))

    monkeypatch.setattr(loop, "tool", tool)
    monkeypatch.setattr(we, "amendment", lambda task: None)
    row = loop.one(tmp_path, "g6", "K1", task.id, 1)
    assert row["run"] == runs["K1"] and row["passes"] is False
    assert compare.saved(tmp_path)[("K1", task.id, 1)]["maintainers"] is False
