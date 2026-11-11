"""Actual report writers with process-only doubles; stdout is display data after issuance."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

from g6.harness import patch_review, verification_search
from g6.workforce import repo_tests as rt
from g6.metaharness import coding
from tests.harness.efu2_cases import NODE, PASSED, report
from tests.harness.test_producer_report_paths import counted_writer, emit, process as producer_process


@pytest.fixture
def process(tmp_path, monkeypatch):
    return producer_process.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("output,expected", [
    ("G6-OBSERVATION {\"input\": 1, \"output\": 2}\n", [{"input": 1, "output": 2}]),
    ("ordinary captured text", None),
])
def test_real_result_builder_ingests_payload_once(process, monkeypatch, output, expected):
    repo, scratch = process
    def bounded(cmd, **kw):
        code = emit(cmd, repo, "passed", kw["log"])
        kw["log"].write_text(output, encoding="utf-8")
        return code
    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert rt.read_report(raw).valid
    assert raw["probe_observations"] == expected
    before = verification_search.reading(raw, NODE)
    assert before["observations"] == expected
    assert verification_search.reading({**raw, "output": "G6-OBSERVATION broken"}, NODE) == before


def test_malformed_payload_is_recorded_by_the_producer_as_an_unknown(process, monkeypatch):
    repo, scratch = process
    def bounded(cmd, **kw):
        code = emit(cmd, repo, "passed", kw["log"])
        kw["log"].write_text("G6-OBSERVATION broken\n", encoding="utf-8")
        return code
    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert isinstance(raw["probe_observations"], str)
    assert verification_search.reading(raw, NODE)["result"] == "error"


def test_real_hook_writer_reports_missing_selections_without_parsing_a_usage_message(tmp_path):
    path = tmp_path / "counted.json"
    writer = counted_writer(path)
    # The extracted real hook uses os from the bootstrap's module globals.
    writer.pytest_collectreport.__globals__["os"] = os
    session = SimpleNamespace(config=SimpleNamespace(rootpath=tmp_path, rootdir=tmp_path), items=[],
                              _notfound=[(str(tmp_path / "test_calc.py") + "::test_add", [])])
    writer.pytest_sessionstart(session)
    writer.pytest_collectreport(SimpleNamespace(nodeid="", skipped=False, failed=False))
    session._notfound.clear()  # pytest clears this before collection_finish
    writer.pytest_collection_finish(session)
    progress = rt._progress({}, path)
    raw = rt._not_run_report("")
    raw.update({key: value for key, value in progress.items() if key in rt.REPORT_FIELDS})
    raw.update(verdict="collection_error", rootdir="")
    assert rt.read_report(raw).valid
    assert raw["not_found_ids"] == [NODE]
    assert patch_review._verdicts([NODE], raw)[NODE]["verdict"] == "not_collected"


def test_real_hook_records_the_missing_module_from_the_exception_object(tmp_path):
    path = tmp_path / "counted.json"
    writer = counted_writer(path)
    session = SimpleNamespace(config=SimpleNamespace(rootpath="", rootdir=""), items=[])
    writer.pytest_collection_finish(session)
    writer.pytest_collectreport(SimpleNamespace(nodeid="test_broken.py", skipped=False, failed=True,
                                               longrepr="a collection failure"))
    error = ModuleNotFoundError("display words are arbitrary", name="new_dependency")
    writer.pytest_exception_interact(None, SimpleNamespace(excinfo=SimpleNamespace(value=error)),
                                     SimpleNamespace(nodeid="test_broken.py"))
    assert json.loads(path.read_text(encoding="utf-8"))["missing_modules"] == ["new_dependency"]


@pytest.mark.parametrize("field,wrong", [
    ("not_found_ids", [False]), ("missing_modules", "library"),
    ("probe_observations", [{"input": 1}]),
    ("probe_observations", [{"input": {False: 1}, "output": 2}]),
    ("probe_observations", [{"input": 1, "output": float("nan")}]),
])
def test_wrong_typed_new_annotations_make_the_whole_report_unknown(field, wrong):
    assert not rt.read_report(report(PASSED, **{field: wrong})).valid


def test_valid_payloads_near_the_protocol_limit_survive_decoding():
    # Decoding can expand a short float spelling and change Unicode escaping.
    # The line budget belongs to the producer, not a second serialization.
    for payload in ('{"input":[' + ",".join(["1e7"] * 200) + '],"output":0}',
                    json.dumps({"input": "á" * 900, "output": 0}, ensure_ascii=False)):
        assert len(payload) <= rt.MAX_OBSERVATION_CHARS
        raw = report(PASSED, probe_observations=rt.probe_observations("G6-OBSERVATION " + payload))
        assert rt.read_report(raw).valid


def test_an_outer_oracle_does_not_support_the_trees_code_loaded_from_elsewhere(process, monkeypatch):
    repo, scratch = process
    (repo / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    def bounded(cmd, **kw):
        code = emit(cmd, repo, "passed", kw["log"])
        (kw["log"].parent / "loaded.json").write_text(
            json.dumps({"modules": {"calc": "elsewhere/calc.py"}, "complete": True}), encoding="utf-8")
        return code
    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert rt.read_report(raw).valid
    assert rt.shadowed(["calc.py"], raw)
    monkeypatch.setattr(rt, "_git", lambda *a, **kw: b"calc.py\0test_calc.py\0")
    oracle = coding.coding_oracle(run=lambda *a, **kw: raw)
    assert oracle(str(repo), {"tests": [NODE]}) is False
