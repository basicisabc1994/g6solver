# SPDX-License-Identifier: Apache-2.0
"""V10's append-only record, and its verification before any analysis (PROTOCOL.md on the verification search branch,
"Safeguards": the record's final hash is committed before any label is read). Fake records only."""
from __future__ import annotations

import json

import pytest

from patch_review_search import driver, record


def written(tmp_path, entries=(("excluded", {"id": "x0", "draw_index": 0, "reason": "empty"}),
                               ("arm", {"patch": "p1", "arm": "C", "output": {"disposition": {"accepted": True}}}),
                               ("rerun", {"patch": "p1", "arm": "C", "node": "t.py::t", "result": {}}))):
    path = tmp_path / "record.jsonl"
    kept = record.Record(path)
    for kind, data in entries:
        kept.add(kind, data)
    return path, kept.final


def test_the_driver_writes_the_record_this_module_verifies():
    assert driver.Record is record.Record


def test_a_record_whose_chain_holds_and_ends_at_the_committed_hash_is_read(tmp_path):
    path, final = written(tmp_path)
    entries = record.verify(path, final)
    assert [entry["kind"] for entry in entries] == ["excluded", "arm", "rerun"]
    assert entries[1]["data"]["output"]["disposition"]["accepted"] is True


@pytest.mark.parametrize("tamper", ["data", "digest", "drop", "swap", "seq", "extra"])
def test_a_broken_chain_is_refused(tmp_path, tamper):
    path, final = written(tmp_path)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if tamper == "data":
        lines[1]["data"]["output"]["disposition"]["accepted"] = False
    elif tamper == "digest":
        lines[1]["sha256"] = "0" * 64
    elif tamper == "drop":
        del lines[1]
    elif tamper == "swap":
        lines[0], lines[1] = lines[1], lines[0]
    elif tamper == "seq":
        lines[2]["seq"] = 7
    else:
        lines.append(dict(lines[-1]))
    path.write_text("".join(record.canonical(line).decode("utf-8") + "\n" for line in lines), encoding="utf-8")
    with pytest.raises(record.RecordRefused):
        record.verify(path, final)


def test_a_record_that_does_not_end_at_the_committed_hash_is_refused(tmp_path):
    path, final = written(tmp_path)
    with pytest.raises(record.RecordRefused, match="committed"):
        record.verify(path, "f" * 64)
    with pytest.raises(record.RecordRefused, match="committed"):
        record.verify(path, final.upper())
    with pytest.raises(record.RecordRefused):
        record.verify(tmp_path / "absent.jsonl", final)
    (tmp_path / "bad.jsonl").write_text("not json\n", encoding="utf-8")
    with pytest.raises(record.RecordRefused):
        record.verify(tmp_path / "bad.jsonl", final)
