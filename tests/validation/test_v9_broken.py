# SPDX-License-Identifier: Apache-2.0
"""V9's broken-task records (PROTOCOL.md, version 2, "Broken tasks"): the freeze entry's published-list check and task
screen, the published list's IDs, the screen's record, the replacement in the draw, and the label audit's kinds.

Development fixtures only: made-up task IDs, a made-up list file and made-up records; no benchmark row, ID or file is
read here."""
from __future__ import annotations

import hashlib

import pytest

from patch_review_utility import broken, draw

LIST = {"published_list": {"found": True, "source": "https://example.invalid/broken-tasks.csv",
                           "sha256": "0" * 64, "file": "broken-tasks.csv"}}
NONE_FOUND = {"published_list": {"found": False}, "screen": {"on": False}}


def test_the_freeze_entry_records_the_list_check_and_whether_the_screen_runs_with_its_screener():
    assert broken.frozen_problems(NONE_FOUND) == []
    assert broken.frozen_problems({**LIST, "screen": {"on": True, "screener": "S", "record": "vault/screen.json"}}) == []
    assert broken.frozen_problems(None) and broken.frozen_problems({"screen": {"on": False}})
    unsourced = {"published_list": {"found": True, "sha256": "0" * 64, "file": "x.csv"}, "screen": {"on": False}}
    assert any("source" in p for p in broken.frozen_problems(unsourced))
    unhashed = {"published_list": {**LIST["published_list"], "sha256": "abc"}, "screen": {"on": False}}
    assert any("SHA-256" in p for p in broken.frozen_problems(unhashed))
    unnamed = {**NONE_FOUND, "screen": {"on": True, "record": "vault/screen.json"}}
    assert any("screener" in p for p in broken.frozen_problems(unnamed))
    same = {**NONE_FOUND, "screen": {"on": True, "screener": "A", "record": "vault/screen.json"}}
    assert any("not the label auditor" in p for p in broken.frozen_problems(same, auditor="A"))
    assert broken.frozen_problems(same, auditor="B") == []


def test_a_published_lists_ids_join_the_exclusions_only_when_its_hash_matches(tmp_path):
    text = "instance_id,why\norg__calc-0001,unstated detail\norg__calc-0002-v2,weak tests\n"
    path = tmp_path / "broken-tasks.csv"
    path.write_bytes(text.encode("utf-8"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    pool = ["org__calc-0001", "org__calc-0002", "org__calc-0002-v2", "org__calc-0003"]
    assert broken.published_ids(path, digest, pool) == {"org__calc-0001", "org__calc-0002-v2"}  # whole IDs only
    with pytest.raises(ValueError, match="SHA-256"):
        broken.published_ids(path, "f" * 64, pool)


def test_the_screens_record_names_the_test_and_the_behaviour_and_keeps_tasks_with_weak_tests():
    record = {"screener": "S", "tasks": {
        "t1": {"label_invalid": True, "test": "test_a.py::test_x", "sentence": "It requires a log line never asked for."},
        "t2": {"label_invalid": False, "weak_tests": True}, "t3": {"label_invalid": False}}}
    got = broken.screened(record)
    assert got["label_invalid"] == {"t1": {"test": "test_a.py::test_x",
                                           "sentence": "It requires a log line never asked for."}}
    assert got["weak_tests"] == ["t2"] and got["screener"] == "S"
    with pytest.raises(ValueError, match="naming the test"):
        broken.screened({"screener": "S", "tasks": {"t1": {"label_invalid": True}}})


def test_the_draw_replaces_a_task_the_screen_marks_label_invalid_by_the_next_in_order_and_logs_it():
    pool = [{"instance_id": f"t{i}", "repo": "org/calc"} for i in range(6)]
    ordered = draw.order(pool, seed=3)
    got = draw.replacements(ordered, {ordered[1]: "the task screen: test_a.py::test_x"})
    assert got["tasks"] == [ordered[0], *ordered[2:]]
    assert got["log"] == [{"replaced": ordered[1], "why": "the task screen: test_a.py::test_x", "by": ordered[2]}]
    last = draw.replacements(ordered, {ordered[-1]: "oracle controls"})
    assert last["log"] == [{"replaced": ordered[-1], "why": "oracle controls", "by": None}]   # nothing left to take


def test_the_audit_records_a_kind_for_each_label_error_and_finds_the_label_invalid_patches():
    record = {"auditor": "A", "audited": 10, "label_errors": 4,
              "errors": [{"task_id": "d1", "kind": "1", "test": "test_d1.py::test_behaviour"},
                         {"task_id": "d2", "kind": "4", "test": "test_d2.py::test_behaviour"},
                         {"task_id": "d3", "kind": "other", "test": "test_d3.py::test_behaviour"},
                         {"task_id": "c1", "kind": "3", "test": "test_c1.py::test_behaviour"}]}
    assert broken.audit_problems(record) == [] and broken.audit_problems(None) == []
    got = broken.audit(record, {"d1": "defective", "d2": "correct", "d3": "defective", "c1": "correct"})
    assert got["label_invalid"] == ["d1"] and got["kind_4"] == 1 and got["inconsistent"] == ["c1"]
    assert got["by_kind"] == {"1": 1, "2": 0, "3": 1, "4": 1, "other": 1} and got["label_errors"] == 4
    assert broken.audit(None, {}) is None                                # no auditor: nothing is left out
    unkinded = {**record, "errors": [{"task_id": "d1", "test": "t"}, *record["errors"][1:]]}
    assert any("kind" in p for p in broken.audit_problems(unkinded))
    uncounted = {**record, "label_errors": 3}
    assert any("each label error" in p for p in broken.audit_problems(uncounted))
