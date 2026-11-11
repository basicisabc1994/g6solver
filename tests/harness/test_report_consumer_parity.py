"""Compare released readers with this tree on reports made by the real writers.

The frozen fixture never imports product code. Only corrupt/incomplete reports
may lose evidence; their explicitly named differences are checked below.
"""
from __future__ import annotations

import hashlib
import json
import sys
import ast
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop, patch_review, verification_search
from g6.workforce import receipt_store, repo_tests as rt
from tests.harness.frozen_db65ca1 import completion_loop as old_completion
from tests.harness.frozen_db65ca1 import patch_review as old_review
from tests.harness.frozen_db65ca1 import receipt_store as old_receipts
from tests.harness.frozen_db65ca1 import repo_tests as old_runner
from tests.harness.frozen_db65ca1 import verification_search as old_search
from tests.harness import test_producer_report_paths as producers
from tests.harness.test_producer_report_paths import NODE, counted_writer, emit
from tests.harness.report_consumer_inventory import RECEIVERS, consumer_fields

OTHER = "test_other.py::test_other"
CHANGE = SimpleNamespace(sources=("test_calc.py",))


@pytest.fixture
def process(tmp_path, monkeypatch):
    compare_judge(monkeypatch)
    return producers.process.__wrapped__(tmp_path, monkeypatch)


def compare_judge(monkeypatch):
    judge = rt._judge

    def compared(*args, **kwargs):
        before, after = old_runner._judge(*args, **kwargs), judge(*args, **kwargs)
        assert after == before, (before, after)
        return after

    monkeypatch.setattr(rt, "_judge", compared)


def reading(review, search, completion, raw):
    ids = [NODE, OTHER]
    verdicts = review._verdicts(ids, raw)
    elsewhere = review._loaded_elsewhere(CHANGE, raw)
    req = SimpleNamespace(id="R1", text="the stated behavior", checks=[NODE], provenance="stated",
                          kind="requirement", merged_from=[])
    return {
        "verdicts": verdicts,
        "failed_ids": sorted(review._failed_ids(raw)),
        "observed": review._observed(ids, raw),
        "outcomes": {node: value["verdict"] for node, value in verdicts.items()},
        "observations": {node: review.observation(value) for node, value in verdicts.items()},
        "requirement": review._requirement(req, verdicts, {NODE: NODE}),
        "derived_observations": dict.fromkeys(review._observed(ids, raw), review.NOT_LOADED)
        if elsewhere else review._observed(ids, raw),
        "record": review._record(2, "derived", ids, raw),
        "loaded_elsewhere": elsewhere,
        "search": search.reading(raw, NODE),
        "completion": asdict(completion._read(CHANGE, [raw])),
    }


def differences(before, after):
    return {key: (before[key], after[key]) for key in before if before[key] != after[key]}


def intended_reading(before, case):
    """Exhaustive exceptions: only missing/incomplete evidence becomes unknown.

    Persisted verdicts/outcomes, counters, annotations and loading decisions are
    never excluded. Each exception below has a named producer case.
    """
    expected = deepcopy(before)
    if case in {"could_not_start", "pytest_not_found", "timed_out", "crashed", "inconsistent_report"}:
        # No progress exists: the attempt cannot establish candidate absence.
        for field in ("observed", "derived_observations"):
            expected[field] = dict.fromkeys((NODE, OTHER), "error")
    elif case in {"deselected", "collection_error"}:
        # These checks have no body record; their unknown must reach issuance.
        for field in ("observed", "derived_observations"):
            expected[field] = {NODE: "deselected" if case == "deselected" else "error"}
        if case == "collection_error":
            expected["verdicts"][NODE]["observation"] = "error"
            expected["observations"][NODE] = "error"
    elif case in {"timeout_completed_body", "copy_timeout"}:
        # Its body completed, so its persisted verdict stays passed. The timed
        # out run cannot supply a supporting observation.
        expected["verdicts"][NODE]["observation"] = "error"
        expected["observations"][NODE] = "error"
        expected["observed"] = expected["derived_observations"] = {NODE: "error"}
    elif case in {"timeout_started", "interrupted", "interrupted_failure"}:
        # A partial phase record keeps diagnostic verdicts, never body evidence.
        for value in expected["verdicts"].values():
            value["observation"] = "error"
        for field in ("observed", "observations", "derived_observations"):
            expected[field] = dict.fromkeys((NODE, OTHER), "error")
        expected["search"]["result"] = "error"
        expected["search"]["detail"] = "the runner report is unknown"
        expected["search"].pop("observations", None)
        expected["completion"]["exit"] = None
        expected["completion"]["results"] = {NODE: "unknown"}
        expected["completion"]["tail"] = f"{before['record']['verdict']}: {before['record']['said']}"
    elif case == "loaded_incomplete":
        # The decided loading fact now withholds evidence in every reader;
        # diagnostic verdicts, outcomes and recorded run fields stay released.
        expected["verdicts"][NODE]["observation"] = "error"
        expected["observations"][NODE] = "error"
        expected["observed"] = {NODE: "error"}
        expected["search"]["result"] = "error"
        expected["search"]["detail"] = "the runner cannot say which code it loaded"
        expected["search"].pop("observations", None)
    elif case == "teardown_skipped":
        # A passed call with skipped cleanup is not a complete passing check.
        expected["search"]["result"] = "error"
        expected["search"]["detail"] = "its tests did not run to a pass or a fail (passed)"
        expected["search"].pop("observations", None)
        expected["completion"]["results"][NODE] = "skipped"
    elif case == "mixed_collection_error":
        # A different file failed collection beside a completed body. Preserve
        # the completed check, but retain the missing collection as unknown.
        expected["completion"]["exit"] = None
        expected["completion"]["results"]["test_broken.py"] = "unknown"
    if "observation" in expected["verdicts"][NODE]:
        expected["requirement"]["checks"][0]["observation"] = expected["verdicts"][NODE]["observation"]
    return expected


def assert_parity(raw, *, case="complete"):
    if rt.read_report(raw).valid:
        # This preserves every scalar-only consumer, including those outside
        # the harness: values and absence have identical released meanings.
        assert rt.read_report(raw).wire() == raw
    before = reading(old_review, old_search, old_completion, raw)
    expected = intended_reading(before, case)
    for label, projected in (("direct", raw), ("projected", patch_review._confined(raw))):
        after = reading(patch_review, verification_search, completion_loop, projected)
        delta = differences(expected, after)
        assert delta == {}, (case, label, json.dumps(delta, indent=2))


def test_frozen_reader_files_have_no_product_import_and_keep_their_recorded_bytes():
    folder = Path(__file__).with_name("frozen_db65ca1")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest.items():
        content = (folder / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest, name
        assert b"from g6" not in content and b"import g6" not in content, name


@pytest.mark.parametrize("kind", [
    "passed", "failed", "could_not_start", "pytest_not_found", "timed_out", "crashed",
    "no_tests_collected", "deselected", "collection_error", "inconsistent_report",
    "outside_loaded", "loaded_incomplete", "timeout_started", "timeout_completed_body",
])
@pytest.mark.parametrize("copied", [False, True], ids=["in-place", "copy"])
def test_producer_report_consumer_parity(process, monkeypatch, kind, copied):
    repo, scratch = process

    def bounded(cmd, **kw):
        emitted = ("passed" if kind in ("outside_loaded", "loaded_incomplete", "timeout_completed_body")
                   else "interrupted" if kind == "timeout_started" else kind)
        code = emit(cmd, repo, emitted, kw["log"])
        if kind in ("outside_loaded", "loaded_incomplete"):
            (kw["log"].parent / "loaded.json").write_text(json.dumps({
                "modules": {"test_calc": "elsewhere/test_calc.py"}, "complete": kind == "outside_loaded",
            }), encoding="utf-8")
        return None if kind.startswith("timeout_") else code

    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=copied,
                  python=sys.executable, scratch_dir=scratch)
    assert_parity(raw, case=kind)


def test_projection_retains_the_started_timeout_and_the_unreached_check(process, monkeypatch):
    repo, scratch = process

    def bounded(cmd, **kw):
        emit(cmd, repo, "interrupted", kw["log"])
        return None

    monkeypatch.setattr(rt, "_bounded", bounded)
    (repo / OTHER.partition("::")[0]).write_text("def test_other(): pass\n", encoding="utf-8")
    raw = rt._run(repo, [NODE, OTHER],
                  roots=(), timeout_s=10, network=False, copied=True, python=sys.executable, scratch_dir=scratch)
    before = old_review._verdicts([NODE, OTHER], raw)
    after = patch_review._verdicts([NODE, OTHER], patch_review._confined(raw))
    assert before[NODE]["verdict"] == after[NODE]["verdict"] == "timed_out"
    assert before[OTHER]["verdict"] == after[OTHER]["verdict"] == "not_run"


def test_the_outside_loaded_regression_fixture_records_the_distinction_in_fields():
    # A detached validated report cannot preserve a process fake's dict identity.
    # Exercise the existing regression fixture without its git/owner-only setup.
    source = Path(__file__).with_name("test_every_observation_reaches_the_issuer.py").read_text(encoding="utf-8")
    start = source.index("def test_a_derived_run_of_code_from_outside_the_tree")
    body = source[start:source.index("\ndef ", start + 1)]
    assert "loaded_outside" in body and "loaded_complete" in body
    assert "ran is derived" not in body


def test_receipt_report_fields_keep_their_released_meaning(process, monkeypatch):
    repo, scratch = process
    monkeypatch.setattr(rt, "_bounded", lambda cmd, **kw: emit(cmd, repo, "passed", kw["log"]))
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    monkeypatch.setattr(receipt_store, "_executable", lambda value: {"path": value})
    args = dict(started_at="start", finished_at="end", before={}, after={}, base_commit="a" * 40,
                python=sys.executable, requested=[NODE], found=raw)
    before, after = old_receipts._body(**args), receipt_store._body(**args)
    # These fields come from snapshots or the host, not from a run report.
    for key in ("execution_id", "g6_version", "source_before", "source_after"):
        before.pop(key)
        after.pop(key)
    assert before == after
    assert old_receipts.counts_as_pass(before) == receipt_store.counts_as_pass(after)


def test_every_scalar_and_phase_consumer_distinction_is_in_the_schema():
    inventory = json.loads(Path(__file__).with_name("report_consumer_distinctions.json").read_text(encoding="utf-8"))
    assert set(inventory["fields"]) == set(rt.REPORT_FIELDS)
    assert set(inventory["values"]["verdict"]) == rt.REPORT_VERDICTS
    root = Path(rt.__file__).parents[1]
    for relative in RECEIVERS:
        found = consumer_fields((root / relative).read_text(encoding="utf-8"), relative)
        baseline = inventory["consumers"][relative]
        for name, fields in found.items():
            assert name in baseline, (relative, name)
            assert set(fields) <= set(baseline[name]), (relative, name, set(fields) - set(baseline[name]))
            assert set(fields) <= set(rt.REPORT_FIELDS), (relative, name)
    for field in inventory["fields"].values():
        assert field["meaning"] and field["consumers"] and field["constraint"]


def test_all_declared_annotations_survive_projection_with_their_released_values(process, monkeypatch):
    repo, scratch = process
    monkeypatch.setattr(rt, "_bounded", lambda cmd, **kw: emit(cmd, repo, "passed", kw["log"]))
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    for name, field in rt.REPORT_FIELDS.items():
        populated = {**raw, name: raw.get(name, deepcopy(field.example))}
        assert rt.read_report(populated).valid, name
        assert_parity(populated)


def test_consumer_inventory_cannot_silently_skip_a_new_report_field():
    found = consumer_fields('def consumer(ran):\n    return ran.get("future_field")\n', "harness/patch_review.py")
    assert found == {"consumer": ["future_field"]}


def test_no_new_runner_caller_escapes_the_consumer_inventory():
    root = Path(rt.__file__).parents[1]
    found = set()
    entries = {"run", "run_before", "run_at_base"}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases, direct = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "g6.workforce":
                aliases.update(part.asname or part.name for part in node.names if part.name == "repo_tests")
            elif isinstance(node, ast.ImportFrom) and node.module == "g6.workforce.repo_tests":
                direct.update(part.asname or part.name for part in node.names if part.name in entries)
            elif isinstance(node, ast.Import):
                aliases.update(part.asname or part.name for part in node.names if part.name == "g6.workforce.repo_tests")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = ast.unparse(node.func)
            if called in direct or any(called == f"{alias}.{entry}" for alias in aliases for entry in entries):
                found.add(path.relative_to(root).as_posix())
    assert found <= RECEIVERS.keys(), found - RECEIVERS.keys()
    assert found  # the scan must actually encounter the repository's callers


@pytest.mark.parametrize("phases", [
    {"setup": "failed", "teardown": "passed"},
    {"setup": "skipped", "teardown": "passed"},
    {"setup": "passed", "call": "skipped", "teardown": "passed"},
    {"setup": "passed", "call": "passed", "teardown": "failed"},
    {"setup": "passed", "call": "failed", "teardown": "failed"},
    {"setup": "passed", "call": "passed", "teardown": "skipped"},
], ids=["setup-failed", "setup-skipped", "call-skipped", "teardown-failed", "call-and-teardown-failed", "teardown-skipped"])
def test_each_producer_phase_value_keeps_released_consumer_distinctions(process, monkeypatch, phases):
    repo, scratch = process
    cleanup_skipped = phases.get("teardown") == "skipped"
    if cleanup_skipped:
        (repo / "test_other.py").write_text("def test_other(): pass\n", encoding="utf-8")

    def bounded(cmd, **kw):
        own = kw["log"].parent
        # Build telemetry by the actual pytest hook writer, not a report dict.
        writer = counted_writer(own / "counted.json")
        item = SimpleNamespace(nodeid=NODE)
        items = [item, SimpleNamespace(nodeid=OTHER)] if cleanup_skipped else [item]
        session = SimpleNamespace(config=SimpleNamespace(rootpath=repo), items=items)
        writer.pytest_itemcollected(item)
        if cleanup_skipped:
            writer.pytest_itemcollected(items[1])
        writer.pytest_collection_finish(session)
        for phase, outcome in phases.items():
            writer.pytest_runtest_logreport(SimpleNamespace(
                nodeid=NODE, when=phase, outcome=outcome, failed=outcome == "failed", longrepr="AssertionError"))
        if cleanup_skipped:
            for phase in ("setup", "call", "teardown"):
                writer.pytest_runtest_logreport(SimpleNamespace(
                    nodeid=OTHER, when=phase, outcome="passed", failed=False, longrepr=""))
        writer.pytest_sessionfinish(session)
        failures = "failed" in phases.values()
        body = ("<failure>AssertionError</failure>" if failures else
                "<skipped/>" if "skipped" in phases.values() else "")
        sibling = '<testcase classname="test_other" name="test_other"/>' if cleanup_skipped else ""
        (own / "report.xml").write_text(
            f'<testsuite><testcase classname="test_calc" name="test_add">{body}</testcase>{sibling}</testsuite>', encoding="utf-8")
        (own / "loaded.json").write_text('{"modules": {}, "complete": true}', encoding="utf-8")
        kw["log"].write_text("", encoding="utf-8")
        return 1 if failures else 0

    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [NODE, OTHER] if cleanup_skipped else [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert rt.read_report(raw).valid
    assert_parity(raw, case="teardown_skipped" if phases.get("teardown") == "skipped" else "complete")


def test_a_collection_error_beside_a_complete_body_retains_both_distinctions(process, monkeypatch):
    repo, scratch = process
    (repo / "test_broken.py").write_text("raise ValueError('broken')\n", encoding="utf-8")

    def bounded(cmd, **kw):
        emit(cmd, repo, "passed", kw["log"])
        writer = counted_writer(kw["log"].parent / "counted.json")
        item = SimpleNamespace(nodeid=NODE)
        session = SimpleNamespace(config=SimpleNamespace(rootpath=repo), items=[item])
        writer.pytest_itemcollected(item)
        writer.pytest_collection_finish(session)
        writer.pytest_collectreport(SimpleNamespace(
            nodeid="test_broken.py", skipped=False, failed=True, longrepr="test_broken.py:1: ValueError: broken"))
        for phase in ("setup", "call", "teardown"):
            writer.pytest_runtest_logreport(SimpleNamespace(
                nodeid=NODE, when=phase, outcome="passed", failed=False, longrepr=""))
        writer.pytest_sessionfinish(session)
        (kw["log"].parent / "report.xml").write_text(
            '<testsuite><testcase classname="test_calc" name="test_add"/>'
            '<testcase name="test_broken"><error message="collection failure">'
            'test_broken.py:1: ValueError: broken</error></testcase></testsuite>', encoding="utf-8")
        return 2

    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [NODE, "test_broken.py"], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert raw["verdict"] == "failed" and rt.read_report(raw).valid
    assert_parity(raw, case="mixed_collection_error")


@pytest.mark.parametrize("mode", ["failure_order", "setup_lookup", "root_prefix"])
def test_replay_persisted_fields_keep_the_exact_failure_disposition(process, monkeypatch, mode):
    repo, scratch = process
    raw_file = NODE.partition("::")[0]
    file = f"sub/{raw_file}" if mode == "root_prefix" else raw_file
    nodes = [f"{raw_file}::test_z", f"{raw_file}::test_a"] if mode == "failure_order" else [NODE]
    (repo / file).parent.mkdir(parents=True, exist_ok=True)
    (repo / file).write_text("def test_z(): pass\ndef test_a(): pass\n" if mode == "failure_order"
                             else "def test_add(missing): pass\n" if mode == "setup_lookup"
                             else "def test_add(): pass\n", encoding="utf-8")

    class FixtureLookupErrorRepr:
        def __str__(self):
            return "fixture 'missing' not found"

    def bounded(cmd, **kw):
        own = kw["log"].parent
        writer = counted_writer(own / "counted.json")
        items = [SimpleNamespace(nodeid=node) for node in nodes]
        session = SimpleNamespace(config=SimpleNamespace(rootpath=repo / "sub" if mode == "root_prefix" else repo), items=items)
        for item in items:
            writer.pytest_itemcollected(item)
        writer.pytest_collection_finish(session)
        for node in nodes:
            phases = {"setup": "passed", "call": "failed", "teardown": "passed"} if mode != "setup_lookup" else {
                "setup": "failed", "teardown": "passed"}
            for phase, outcome in phases.items():
                writer.pytest_runtest_logreport(SimpleNamespace(
                    nodeid=node, when=phase, outcome=outcome, failed=outcome == "failed",
                    longrepr=FixtureLookupErrorRepr() if mode == "setup_lookup" else "AssertionError"))
        writer.pytest_sessionfinish(session)
        tag = "failure" if mode != "setup_lookup" else "error"
        cases = "".join(f'<testcase classname="test_calc" name="{node.rsplit("::", 1)[-1]}">'
                        f'<{tag}>AssertionError</{tag}></testcase>' for node in nodes)
        (own / "report.xml").write_text(f"<testsuite>{cases}</testsuite>", encoding="utf-8")
        (own / "loaded.json").write_text('{"modules": {}, "complete": true}', encoding="utf-8")
        kw["log"].write_text("", encoding="utf-8")
        return 1

    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [file], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert rt.read_report(raw).valid

    def replay_reading(reader):
        # Inject transport of this same real-builder report into each copied
        # reader. No producer builder or baseline reading is replaced.
        monkeypatch.setattr(reader, "_execute", lambda *a, **k: {file: raw})
        runs, unknowns, reasons, observed = [], [], [], []
        reader._on_base(repo, CHANGE, [file], raw, time.monotonic() + 60,
                        runs, unknowns, lambda *a, **k: reasons.append((a, k)), stated=[file], observed=observed)
        return dict(runs=runs, unknowns=unknowns, reasons=reasons, observed=observed)

    before, after = replay_reading(old_review), replay_reading(patch_review)
    assert before == after, (mode, before["reasons"], after["reasons"])
