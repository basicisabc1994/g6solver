"""Producer schema, consumer inventory, and single-field corruption table.

Recorded runs only: no services, models, or runner processes.
"""
from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path

import pytest

import g6
from g6.harness import patch_review as review
from g6.harness import completion_loop, verification_search
from g6.workforce import repo_tests
from tests.harness.efu2_cases import FAILED, NODE, PASSED, assess_runs, report
from tests.harness.test_the_phase_reading_is_total import checked, plugin_fields, producer_fields


def schema():
    assert hasattr(repo_tests, "REPORT_FIELDS"), "the producer owns no strict report schema"
    return repo_tests.REPORT_FIELDS


def read(raw):
    schema()
    return repo_tests.read_report(raw)


def plants(valid):
    """Mutate one field, including its nested containers, per planted report."""
    for key in valid:
        for wrong in (None, False, 1, 1.5, "text", [], {}, ()):
            if type(wrong) is type(valid[key]):
                continue
            changed = deepcopy(valid)
            changed[key] = wrong
            yield f"{key}:{type(wrong).__name__}", changed
        if key != "isolation":  # execution annotations are optional in recorded runs
            changed = deepcopy(valid)
            del changed[key]
            yield f"missing:{key}", changed
    yield "extra", {**valid, "new_unclassified_field": []}
    for key in ("failed_ids", "collected_ids", "omitted_ids"):
        changed = deepcopy(valid)
        changed[key] = [False]
        yield f"{key}:item-type", changed
        changed = deepcopy(valid)
        changed[key] = [NODE, NODE]
        yield f"{key}:duplicate", changed
    for key in ("test_outcomes", "setup_errors"):
        changed = deepcopy(valid)
        changed[key] = {False: valid[key].get(NODE, "message")}
        yield f"{key}:key-type", changed
    for wrong in (None, False, 1, "text", [], {"call": False}):
        changed = deepcopy(valid)
        changed["test_outcomes"][NODE] = wrong
        yield f"phases:{type(wrong).__name__}", changed
    for phase in PASSED:
        changed = deepcopy(valid)
        del changed["test_outcomes"][NODE][phase]
        yield f"phase-missing:{phase}", changed
        for wrong in (None, False, 1, "new-outcome"):
            changed = deepcopy(valid)
            changed["test_outcomes"][NODE][phase] = wrong
            yield f"phase-outcome:{phase}:{wrong!r}", changed
    changed = deepcopy(valid)
    changed["test_outcomes"][NODE] = dict(reversed(list(valid["test_outcomes"][NODE].items())))
    yield "phase-order", changed
    changed = deepcopy(valid)
    changed["test_outcomes"][NODE]["unknown-phase"] = "passed"
    yield "phase-extra", changed
    yield "omitted-and-run", {**valid, "omitted_ids": [NODE]}
    yield "run-not-collected", {**valid, "collected_ids": []}
    yield "collected-only", {**valid, "collected_ids": [NODE, "test_calc.py::test_other"]}
    yield "failure-unlisted-or-spurious", {**valid, "failed_ids": [] if valid["failed_ids"] else [NODE]}
    yield "failure-two-dispositions", {**valid, "setup_errors": {NODE: "fixture lookup failed"}}


@pytest.mark.parametrize("phases", [PASSED, FAILED], ids=["pass", "body-failure"])
def test_single_field_corruptions_are_unknown_as_a_whole(phases):
    valid = report(phases)
    assert read(valid).valid
    expected = "passed" if phases == PASSED else "failed"
    assert review.body_of(review.phases_of(valid)[NODE]) == expected
    escaped = []
    for name, raw in plants(valid):
        try:
            got = read(raw)
            observed = review._observed([NODE], raw)
            verdict = review.observation(review._verdicts([NODE], raw)[NODE])
            if got.valid or observed.get(NODE) in ("passed", "failed") or verdict in ("passed", "failed"):
                escaped.append(name)
        except Exception as exc:
            escaped.append(f"{name}: {type(exc).__name__}")
    assert escaped == []


def test_whole_report_unknown_also_poison_sibling_support_and_body_failure(monkeypatch):
    base = report(FAILED)
    patch = report(PASSED)
    for raw in (base, patch):
        raw["test_outcomes"]["test_other.py::test_other"] = {"call": "passed"}
        raw["collected_ids"].append("test_other.py::test_other")
    assert not assess_runs(monkeypatch, base, report(PASSED))["requirements"][0]["checked"]
    assert not assess_runs(monkeypatch, report(FAILED), patch)["requirements"][0]["checked"]


def test_all_observation_consumers_read_every_corruption_as_unknown():
    from types import SimpleNamespace

    schema()
    change = SimpleNamespace(sources=())
    for name, raw in plants(report(PASSED)):
        confined = review._confined(raw)
        assert not read(confined).valid, name
        assert verification_search.reading(raw, NODE)["result"] == "error", name
        got = completion_loop._read(change, [raw])
        assert got.exit is None and not any(value in ("passed", "failed") for value in got.results.values()), name


def test_support_needs_the_same_positive_phase_match_in_every_consumer():
    skipped_cleanup = {**PASSED, "teardown": "skipped"}
    raw = report(skipped_cleanup)
    assert read(raw).valid
    assert review.body_of(read(raw).phases[NODE]) == "skipped"
    assert verification_search.reading(raw, NODE)["result"] == "error"


def test_an_omitted_only_replay_reaches_issuance_as_an_unknown_observation(monkeypatch):
    import time
    from types import SimpleNamespace

    file = NODE.partition("::")[0]
    omitted = report({}, test_outcomes={}, omitted_ids=[NODE])
    monkeypatch.setattr(review, "_execute", lambda *a, **k: {file: omitted})
    observed = []
    review._on_base(Path.cwd(), SimpleNamespace(sources=()), [file], report(FAILED),
                    time.monotonic() + 30, [], [], lambda *a, **k: None, stated=[NODE], observed=observed)
    assert observed and observed[0]["verdicts"] == {NODE: "deselected"}
    got = assess_runs(monkeypatch, report(FAILED), report(PASSED), observed=observed)
    assert not got["requirements"][0]["checked"]


def test_a_whole_passing_check_keeps_its_verdict_when_another_check_failed():
    # Positive control for the released per-check verdict, not the run summary.
    other = "test_other.py::test_other"
    raw = report(PASSED, verdict="failed", test_outcomes={NODE: PASSED, other: FAILED},
                 collected_ids=[NODE, other], failed_ids=[other], passed=0, failed=1, not_passed=0, total=1)
    got = review._verdicts([NODE, other], raw)
    assert got[NODE]["verdict"] == "passed" and review.observation(got[NODE]) == "passed"
    assert got[other]["verdict"] == "failed" and review.observation(got[other]) == "failed"


def test_complete_records_keep_the_critics_fixture_error_policy():
    from types import SimpleNamespace

    cases = [(PASSED, "passed"), (FAILED, "failed"),
             ({"setup": "failed", "teardown": "passed"}, "error"),
             ({**FAILED, "teardown": "failed"}, "error"),
             ({**PASSED, "teardown": "failed"}, "error")]
    for phases, expected in cases:
        got = completion_loop._read(SimpleNamespace(sources=()), [report(phases)])
        assert got.results[NODE] == expected


def test_producer_counters_agree_with_their_identity_and_phase_listings():
    for name in ("collected", "started", "finished"):
        valid = {**report(PASSED), name: 1}
        assert read(valid).valid
        assert not read({**valid, name: 0}).valid, name
    skipped = {**report({"collection": "skipped"}), "collected": 0, "started": 0, "finished": 0}
    assert read(skipped).valid  # collection skips are not pytest items


def test_os_error_annotations_match_the_producers_message_field_before_projection():
    file = NODE.partition("::")[0]
    raw = report({}, verdict="collection_error", test_outcomes={}, collected_ids=[],
                 messages={file: "OSError while collecting"}, os_errors=[file])
    assert read(raw).valid
    assert verification_search.reading({**raw, "failure_messages": raw["messages"]}, NODE)["result"] == "error"


def test_tallies_cannot_contradict_complete_phase_records():
    for phases, field, value in ((PASSED, "passed", 0), (FAILED, "failed", 0), (PASSED, "total", 0),
                                 (PASSED, "failed", 1), (PASSED, "not_passed", 1),
                                 ({"setup": "skipped", "teardown": "passed"}, "not_passed", 0)):
        assert not read({**report(phases), field: value}).valid, (field, value)


def test_a_collection_failure_cannot_disappear_beside_a_passing_body(monkeypatch):
    raw = report(PASSED, verdict="failed", failure_messages={"test_calc.py::Broken": "ImportError in collection"})
    assert not checked(monkeypatch, at_base=report(FAILED), on_patch=raw)


def test_unknown_projection_contains_no_nonfinite_or_unbounded_summary_values():
    for name, value in (("seconds", float("nan")), ("seconds", float("inf")), ("passed", 10 ** 5000)):
        got = read({**report(PASSED), name: value})
        assert not got.valid
        json.dumps(got.wire(), allow_nan=False)


def test_rejected_types_never_run_their_comparison_operators():
    class WrongNumber(int):
        def __eq__(self, other):
            raise AssertionError("a rejected number ran its comparison")

    class WrongKey:
        def __hash__(self):
            return hash("test_outcomes")

        def __eq__(self, other):
            raise AssertionError("a rejected key ran its comparison")

    assert not read({**report(PASSED), "deselected": WrongNumber(0)}).valid
    assert not read({WrongKey(): {}}).valid


def test_a_failed_parent_collection_reaches_its_stated_child_check(monkeypatch):
    for parent in (NODE.partition("::")[0], ""):
        raw = report({}, verdict="failed", test_outcomes={}, collected_ids=[],
                     failure_messages={parent: "ImportError in collection"})
        seen = review._observed([NODE], raw)
        got = assess_runs(monkeypatch, report(FAILED), report(PASSED),
                          observed=[{"run": ["derived"], "verdicts": seen}])
        assert not got["requirements"][0]["checked"], parent


def test_schema_classifies_every_producer_field_and_exact_nested_type():
    fields = schema()
    assert set(fields) == producer_fields() | set(plugin_fields()), "unclassified producer field or stale schema field"
    for name, field in fields.items():
        assert field.purpose, name
        assert field.constraint, name
        valid = report(PASSED)
        if name not in valid:
            valid[name] = deepcopy(field.example)
        if name == "loaded_complete":
            valid["loaded_outside"] = {}
        if name == "loaded_outside":
            valid["loaded_complete"] = True
        assert read(valid).valid, name
        for wrong in (False, None, 1, 1.0, "text", [], {}, ()):
            if field.accepts(wrong):
                continue
            assert not read({**valid, name: wrong}).valid, (name, wrong)


def test_schema_refuses_builtin_subclasses_and_boolean_integer_aliases():
    class Text(str):
        pass

    class Records(dict):
        pass

    class Ids(list):
        pass

    valid = report(PASSED)
    for name, wrong in (("rootdir", Text("")), ("test_outcomes", Records(valid["test_outcomes"])),
                        ("collected_ids", Ids([NODE])), ("passed", True), ("seconds", False)):
        assert not read({**valid, name: wrong}).valid, name
    for raw in (None, False, 1, [], "broken", Records(valid)):
        assert not read(raw).valid
        assert review._observed([NODE], raw) == {NODE: review.ERROR}


def test_validated_snapshot_keeps_no_caller_owned_mutable_values():
    raw = report(FAILED)
    got = read(raw)
    raw["test_outcomes"][NODE]["call"] = "passed"
    raw["collected_ids"].clear()
    assert got.valid
    assert review.body_of(got.phases[NODE]) == "failed"
    with pytest.raises(TypeError):
        got.phases[NODE]["call"] = "passed"


def test_each_nested_annotation_type_is_checked_without_coercion():
    fields = schema()
    for name, field in fields.items():
        if type(field.kind) is list:
            for member in (False, 1, None, [], {}):
                assert not read({**report(PASSED), name: [member]}).valid, (name, member)
        elif type(field.kind) is dict and str in field.kind:
            assert not read({**report(PASSED), name: {False: "text"}}).valid, name
            assert not read({**report(PASSED), name: {"node": False}}).valid, name
    for key, kind in fields["isolation"].kind.items():
        for wrong in (False, 1, None, [], {}):
            if repo_tests._report_type(wrong, kind):
                continue
            assert not read({**report(PASSED), "isolation": {key: wrong}}).valid, (key, wrong)
    assert not read({**report(PASSED), "isolation": {"extra": "text"}}).valid
    assert not read({**report(PASSED), "isolation": {}}).valid
    assert not read(review._confined({**report(PASSED), "isolation": {}})).valid
    for seconds in (float("nan"), float("inf"), -1.0):
        assert not read({**report(PASSED), "seconds": seconds}).valid


def counted_class():
    tree = ast.parse(repo_tests._BOOT)
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Counted")


def test_every_interrupted_producer_write_stays_unknown_until_the_complete_record(tmp_path):
    from types import SimpleNamespace

    schema()
    path = tmp_path / "counted.json"
    namespace = {"json": json, "counted": str(path)}
    exec(compile(ast.Module(body=[counted_class()], type_ignores=[]), "runner-counted", "exec"), namespace)
    producer = namespace["_Counted"]()
    item = SimpleNamespace(nodeid=NODE)
    producer.pytest_itemcollected(item)
    producer.pytest_collection_finish(SimpleNamespace(items=[item], config=SimpleNamespace(rootpath="", rootdir="")))
    for index, (phase, outcome) in enumerate(FAILED.items()):
        producer.pytest_runtest_logreport(SimpleNamespace(nodeid=NODE, when=phase, outcome=outcome,
                                                        failed=outcome == "failed", longrepr="assertion failed"))
        raw = json.loads(path.read_text(encoding="utf-8"))
        got = read({**raw, "verdict": "failed", "said": "", "output": ""})
        assert got.valid is (index == 2)
        assert review.body_of(got.phases[NODE]) == ("failed" if index == 2 else "error" if index == 1 else "not_run")
        progress = repo_tests._progress({"passed": 0}, path)
        projected = read({**{k: v for k, v in progress.items() if k in schema()},
                          "verdict": "failed", "said": "", "output": ""})
        assert ("test_calc.py::test_add" in projected.started) is True
        assert projected.valid is (index == 2)


def test_duplicate_json_keys_and_wrong_plugin_types_cannot_be_sanitized_to_readable_reports(tmp_path):
    schema()
    path = tmp_path / "counted.json"
    raw = {**report(FAILED), "collected": 1, "deselected": 0, "started": 1, "finished": 1,
           "messages": {}, "os_errors": []}
    raw = {key: value for key, value in raw.items() if key not in ("verdict", "said", "output", "isolation")}
    for key in raw:
        wrong = {**raw, key: False}
        path.write_text(json.dumps(wrong), encoding="utf-8")
        result = repo_tests._progress({"passed": 0}, path)
        assert result["expected"] is None, key
        projected = {**{k: v for k, v in result.items() if k in schema()},
                     "verdict": "failed", "said": "", "output": ""}
        assert not read(projected).valid, key
    path.write_text(json.dumps(raw)[:-1] + ', "rootdir": ""}', encoding="utf-8")
    assert repo_tests._progress({"passed": 0}, path)["expected"] is None
    path.write_text(json.dumps(raw).replace('"call": "failed"', '"call": "failed", "call": "failed"'),
                    encoding="utf-8")
    assert repo_tests._progress({"passed": 0}, path)["expected"] is None
    for broken in ("", "{", "[]", "false", '{"test_outcomes":', "[" * 2000 + "]" * 2000):
        path.write_text(broken, encoding="utf-8")
        assert repo_tests._progress({"passed": 0}, path)["expected"] is None


# Every product function that reads per-test report fields, found by AST. A new
# consumer fails this inventory until it goes through the producer's reader.
CONSUMERS = {
    "harness/patch_review.py": {"_confined", "_listing", "_failed_ids", "phases_of", "_in_repository", "_verdicts", "_observed", "_on_base", "_not_loaded", "_not_found", "_collection_errors"},
    "harness/verification_search.py": {"reading", "_messages", "_refused_access", "_uncollected", "search"},
    "harness/completion_loop.py": {"_read"},
    "workforce/repo_tests.py": {"read_report", "_judge", "_progress", "_run", "run_before", "shadowed", "loaded_from"},
    "workforce/receipt_store.py": {"_body"},
    "bundles/agent_change.py": {"run", "_as_run"},
    "metaharness/coding.py": {"coding_oracle", "_mutants_killed"},
    "workforce/bench.py": {"_in_place"},
}
PHASE_FIELDS = {"test_outcomes", "failed_ids", "setup_errors", "collected_ids", "omitted_ids", "rootdir"}
VALIDATED_FIELDS = {"phases", "nodes", "failed", "omitted", "full", "wire", "collection_errors",
                    "collection_errors_for", "outcome", "body_result", "loading_known"}


def consumers():
    src = Path(g6.__file__).parent
    found = {}
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for function in functions:
            reads = [n for n in ast.walk(function)
                     if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                         and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value in PHASE_FIELDS)
                     or (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                         and n.slice.value in PHASE_FIELDS)
                     or (isinstance(n, ast.Attribute) and n.attr in VALIDATED_FIELDS
                         and (ast.unparse(n.value) in ("report", "base_report", "patch_report")
                              or (isinstance(n.value, ast.Call) and ast.unparse(n.value.func) in (
                                  "read_report", "repo_tests.read_report", "rt.read_report"))))]
            if reads:
                found.setdefault(path.relative_to(src).as_posix(), set()).add(function.name)
    return found


def test_every_report_consumer_uses_the_producer_validated_form_only():
    schema()
    found = consumers()
    assert found == CONSUMERS, "a report consumer is unclassified"
    src = Path(g6.__file__).parent
    for rel, names in found.items():
        functions = {n.name: n for n in ast.parse((src / rel).read_text(encoding="utf-8")).body
                     if isinstance(n, ast.FunctionDef)}
        for name in names:
            if name == "read_report":
                continue  # the sole raw reader; the schema and plants measure it
            calls = {ast.unparse(n.func) for n in ast.walk(functions[name]) if isinstance(n, ast.Call)}
            if name == "_run":
                assert "_progress" in calls  # builder consumes the schema-read progress, not a raw report
                continue
            assert {"read_report", "repo_tests.read_report", "rt.read_report"} & calls, (rel, name)
