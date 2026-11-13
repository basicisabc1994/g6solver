# SPDX-License-Identifier: Apache-2.0
"""Local inspection, correction, expiry and deletion of the workflow record (design 17.3 "Workflow data and consent"):
all by local code, with no model and no network, and tested before collection is switched on anywhere."""
from __future__ import annotations

import ast
import json
import os
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from g6.harness.workflow import lifecycle, policy
from g6.harness.workflow import record as rec
from tests.workflow.kit import OTHER_TASK, PROJECT, T0, TASK, event, started

MARK = "ZEBRA-MARKER-7731"


def _folder(home) -> Path:
    return home / "workflow" / PROJECT


def _two_runs(home) -> tuple[rec.ProjectRecord, str, str]:
    record = rec.ProjectRecord(PROJECT)
    first = started(record, "session-a")
    for number in (1, 2, 3):
        record.append(event(first, "loop_round", str(number), seconds=10 * number, data={"round": number}))
    record.append(event(first, "loop_ended", seconds=40, data={"ending": "clean"}))
    second = started(record, "session-b", seconds=100)
    record.append(event(second, "loop_round", "1", seconds=110, data={"round": 1}))
    return record, first, second


def _corrections(home, rows, *, raw: bytes | None = None) -> Path:
    path = _folder(home) / "corrections.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if raw is not None else json.dumps(
        {"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": rows}, indent=1).encode())
    return path


def _snapshot(home) -> dict:
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*"))
            if p.is_file() and not p.name.endswith(".lock")}


def test_export_writes_inspection_json_without_a_model_or_network(home):
    _two_runs(home)
    path = lifecycle.export(PROJECT, now=T0 + timedelta(hours=1))
    assert path == (_folder(home) / "inspection.json").resolve() and path.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(_folder(home).glob("*.part"))
    names = {node.module if isinstance(node, ast.ImportFrom) else alias.name
             for node in ast.walk(ast.parse(Path(lifecycle.__file__).read_text(encoding="utf-8")))
             if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert not {n for n in names if n and n.split(".")[0] in {"socket", "urllib", "http", "httpx", "requests",
                                                              "anthropic", "openai"}}
    assert not {n for n in names if n and (n.startswith("g6.core.llm") or n.startswith("g6.core.host_models"))}


def test_export_lists_runs_rounds_and_rework_with_observed_and_derived_entries_apart(home):
    record, first, second = _two_runs(home)
    target = record.events(run=first)[2].event_id
    _corrections(home, [{"target": target, "action": "annotate", "note": "this round was a retry", "by": "A person"}])
    assert lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(hours=1))["applied"] == 1
    shown = json.loads(lifecycle.export(PROJECT, now=T0 + timedelta(hours=2)).read_text(encoding="utf-8"))
    assert shown["schema"] == lifecycle.INSPECTION_SCHEMA and shown["policy_version"] == policy.POLICY_VERSION
    assert shown["project"] == PROJECT and shown["expires_at"] > shown["written_at"]
    (task,) = shown["tasks"]
    assert task["task"] == TASK and [run["run"] for run in task["runs"]] == [first, second]
    one = task["runs"][0]
    assert one["rounds"] == 3 and one["rework_rounds"] == 2 and task["runs"][1]["rework_rounds"] == 0
    assert [e["kind"] for e in one["observed"]] == ["run_started", "loop_round", "loop_round", "loop_round", "loop_ended"]
    assert [e.get("repeat", False) for e in one["observed"]] == [False, False, True, True, False]
    assert {e["provenance"] for e in one["observed"]} == {"host_observed"}
    (note,) = one["derived"]
    assert note["provenance"] == "attributed_label" and note["kind"] == "annotation" and note["data"]["target"] == target
    assert all("provenance" in e and "at" in e and "event_id" in e for run in task["runs"] for e in run["observed"])


def test_correction_appends_and_never_overwrites_a_host_event(home):
    record, first, _ = _two_runs(home)
    before = {p: b for p, b in _snapshot(home).items() if "runs" in p}
    target = record.events(run=first)[1]
    _corrections(home, [{"target": target.event_id, "action": "annotate", "note": "wrong count", "by": "A person"}])
    result = lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(hours=1))
    assert result["status"] == "applied" and result["applied"] == 1 and result["problems"] == []
    assert {p: b for p, b in _snapshot(home).items() if "runs" in p} == before      # run chains byte-identical
    assert rec.ProjectRecord(PROJECT).events(run=first)[1] == target
    (note,) = rec.ProjectRecord(PROJECT).annotations(TASK)
    assert note.provenance == "attributed_label" and note.data == {"target": target.event_id, "note": "wrong count",
                                                                   "by": "A person"}
    again = lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(hours=2))        # the same file, read twice
    assert again["applied"] == 0 and again["already"] == 1
    assert len(rec.ProjectRecord(PROJECT).annotations(TASK)) == 1


def test_supersession_keeps_the_superseded_event_readable(home):
    record, first, _ = _two_runs(home)
    target = record.events(run=first)[4]
    _corrections(home, [{"target": target.event_id, "action": "supersede", "note": "it ended contested", "by": "P"}])
    assert lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(hours=1))["applied"] == 1
    assert target in rec.ProjectRecord(PROJECT).events(run=first)
    shown = json.loads(lifecycle.export(PROJECT, now=T0 + timedelta(hours=2)).read_text(encoding="utf-8"))
    run = shown["tasks"][0]["runs"][0]
    old = next(e for e in run["observed"] if e["event_id"] == target.event_id)
    assert old["data"] == {"ending": "clean"} and old["superseded_by"] == run["derived"][0]["event_id"]
    assert run["derived"][0]["kind"] == "supersession"


def _bad_files(target: str) -> dict[str, tuple[bytes, str]]:
    row = {"target": target, "action": "annotate", "note": MARK, "by": "P"}
    good = json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [row]}, indent=1)
    return {
        "bad json": (good.replace('"action"', '"action" oops').encode(), "line 6"),
        "half saved": (good[: len(good) // 2].encode(), "line "),
        "crlf bad json": (good.replace('"action"', '"action" oops').replace("\n", "\r\n").encode(), "line 6"),
        "bom bad json": (b"\xef\xbb\xbf" + good.replace('"by"', 'by').encode(), "line "),
        "not utf-8": (good.encode("utf-16"), "UTF-8"),
        "too large": (json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [row] * 20000}).encode(),
                      "too large"),
        "wrong schema": (good.replace("corrections.v1", "corrections.v9").encode(), "schema"),
        "not a list": (json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": row}).encode(), "list"),
        "unknown action": (good.replace("annotate", "erase").encode(), "entry 1"),
        "unknown target": (good.replace(target, "f" * 64).encode(), "entry 1"),
        "extra field": (good.replace('"by"', '"path": "x", "by"').encode(), "entry 1"),
        "long note": (good.replace(MARK, MARK * 20).encode(), "entry 1"),
        "no name": (good.replace('"by": "P"', '"by": " "').encode(), "entry 1"),
        "second entry bad": (json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
            row, {**row, "action": "erase"}]}).encode(), "entry 2"),
    }


@pytest.mark.parametrize("case", sorted(_bad_files("0" * 64)))
def test_invalid_corrections_file_changes_nothing_and_names_the_line(home, case):
    record, first, _ = _two_runs(home)
    raw, names = _bad_files(record.events(run=first)[1].event_id)[case]
    assert case != "too large" or len(raw) > 2_000_000
    _corrections(home, None, raw=raw)
    before = _snapshot(home)
    result = lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(hours=1))
    assert result["status"] == "refused" and result["applied"] == 0 and result["problems"]
    assert any(names in problem for problem in result["problems"]), result["problems"]
    assert MARK not in json.dumps(result) and "oops" not in json.dumps(result)     # the file is never echoed
    assert _snapshot(home) == before and rec.ProjectRecord(PROJECT).annotations(TASK) == ()


def test_valid_corrections_with_a_bom_and_crlf_are_applied(home):
    record, first, _ = _two_runs(home)
    row = {"target": record.events(run=first)[1].event_id, "action": "annotate", "note": "ok", "by": "P"}
    text = json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [row]}, indent=1).replace("\n", "\r\n")
    _corrections(home, None, raw=b"\xef\xbb\xbf" + text.encode())
    assert lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(hours=1))["applied"] == 1


def test_no_corrections_file_is_not_an_error(home):
    assert lifecycle.apply_corrections(PROJECT)["status"] == "absent"
    assert not home.exists()


def test_delete_run_removes_its_file_and_its_export_and_leaves_a_receipt_with_only_receipt_fields(home):
    record, first, second = _two_runs(home)
    lifecycle.export(PROJECT, now=T0 + timedelta(hours=1))
    result = lifecycle.delete(PROJECT, scope="run", ident=first, by="A person", now=T0 + timedelta(hours=2))
    assert result["status"] == "deleted" and result["removed"] >= 1
    assert rec.ProjectRecord(PROJECT).runs(TASK) == (second,)
    assert not (_folder(home) / "inspection.json").exists()
    assert not list(_folder(home).rglob(f"{first}*"))
    (receipt,) = lifecycle.receipts(PROJECT)
    assert tuple(receipt) == policy.RECEIPT_FIELDS and result["receipt"] == receipt
    assert receipt["scope"] == "run" and receipt["reason"] == "deletion_requested"
    text = json.dumps(receipt)
    assert first not in text and TASK not in text and "A person" not in text and str(home) not in text


def test_delete_task_and_project_leave_nothing_but_the_receipt(home):
    record, first, second = _two_runs(home)
    other = started(record, "session-c", task=OTHER_TASK, seconds=200)
    target = record.events(run=first)[1].event_id
    _corrections(home, [{"target": target, "action": "annotate", "note": MARK, "by": "P"}])
    lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(hours=1))
    assert lifecycle.delete(PROJECT, scope="task", ident=TASK, by="P", now=T0 + timedelta(hours=2))["status"] == "deleted"
    assert rec.ProjectRecord(PROJECT).tasks() == (OTHER_TASK,) and rec.ProjectRecord(PROJECT).runs(OTHER_TASK) == (other,)
    assert not (_folder(home) / "tasks" / TASK).exists()
    assert lifecycle.delete(PROJECT, scope="project", by="P", now=T0 + timedelta(hours=3))["status"] == "deleted"
    left = sorted(p.name for p in _folder(home).rglob("*") if p.is_file() and not p.name.endswith(".lock"))
    assert left == ["receipts.jsonl"]
    assert MARK.encode() not in (_folder(home) / "receipts.jsonl").read_bytes()
    assert [r["scope"] for r in lifecycle.receipts(PROJECT)] == ["task", "project"]


def test_delete_refuses_what_it_cannot_name(home):
    _two_runs(home)
    before = _snapshot(home)
    for kwargs in ({"scope": "everything", "by": "P"}, {"scope": "run", "ident": "f" * 32, "by": "P"},
                   {"scope": "run", "ident": "../x", "by": "P"}, {"scope": "task", "ident": "f" * 64, "by": "P"},
                   {"scope": "task", "ident": TASK, "by": "  "}, {"scope": "project", "ident": "x", "by": "P"}):
        result = lifecycle.delete(PROJECT, now=T0, **kwargs)
        assert result["status"] == "refused" and result["removed"] == 0, kwargs
    assert _snapshot(home) == before


def test_delete_does_not_touch_reviews_questions_or_consent_ledger(home):
    _two_runs(home)
    kept = {}
    for name in ("reviews/job.json", f"questions/{TASK}.jsonl", "cloud_consent/events.jsonl", "loop/session/state.json",
                 f"workflow/{'e' * 64}/receipts.jsonl"):
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("kept", encoding="utf-8")
        kept[name] = path
    lifecycle.delete(PROJECT, scope="project", by="P", now=T0)
    assert all(path.read_text(encoding="utf-8") == "kept" for path in kept.values())


def test_expire_removes_only_records_past_retention(home):
    record, first, second = _two_runs(home)
    days = policy.RETENTION_DAYS["events"]
    assert days == 180
    early = lifecycle.expire(PROJECT, now=T0 + timedelta(days=days - 1))
    assert early["removed"] == 0 and rec.ProjectRecord(PROJECT).runs(TASK) == (first, second)
    fresh = started(record, "session-late", days=days)                      # a run with recent activity stays
    late = lifecycle.expire(PROJECT, now=T0 + timedelta(days=days + 1))
    assert late["removed"] >= 2 and rec.ProjectRecord(PROJECT).runs(TASK) == (fresh,)
    assert [r["reason"] for r in lifecycle.receipts(PROJECT)] == ["expired"]
    assert tuple(lifecycle.receipts(PROJECT)[0]) == policy.RECEIPT_FIELDS


def test_expire_removes_old_annotations_and_old_receipts(home):
    record, first, _ = _two_runs(home)
    _corrections(home, [{"target": record.events(run=first)[1].event_id, "action": "annotate", "note": "n", "by": "P"}])
    lifecycle.apply_corrections(PROJECT, now=T0)
    lifecycle.expire(PROJECT, now=T0 + timedelta(days=181))
    assert not (_folder(home) / "tasks" / TASK).exists()
    assert len(lifecycle.receipts(PROJECT)) == 1
    lifecycle.expire(PROJECT, now=T0 + timedelta(days=181 + policy.RETENTION_DAYS["receipts"] + 1))
    assert lifecycle.receipts(PROJECT) == ()


def test_expired_export_is_removed_at_next_use(home):
    _two_runs(home)
    path = lifecycle.export(PROJECT, now=T0)
    lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(days=policy.RETENTION_DAYS["exports"] - 1))
    assert path.exists()
    lifecycle.apply_corrections(PROJECT, now=T0 + timedelta(days=policy.RETENTION_DAYS["exports"] + 1))
    assert not path.exists()
    path.write_text("{not an export", encoding="utf-8")          # an export G6 cannot date is not kept
    lifecycle.expire(PROJECT, now=T0)
    assert not path.exists()


def test_unreadable_record_is_reported_not_exported_as_empty(home):
    record, first, _ = _two_runs(home)
    run_file = _folder(home) / "tasks" / TASK / "runs" / f"{first}.jsonl"
    run_file.write_bytes(run_file.read_bytes()[:-30])
    with pytest.raises(rec.RecordUnreadable):
        lifecycle.export(PROJECT, now=T0)
    assert not (_folder(home) / "inspection.json").exists()
    assert lifecycle.delete(PROJECT, scope="run", ident=first, by="P", now=T0)["status"] == "deleted"   # still removable
    assert lifecycle.export(PROJECT, now=T0).is_file()
