"""Run the producer's own writers with process and OS-boundary doubles only.

Removing a required field from any result builder must break this contract.
No report under test is a hand-written replacement for a runner result.
"""
from __future__ import annotations

import ast
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import confine
from g6.harness import patch_review
from g6.workforce import repo_tests as rt

NODE = "test_calc.py::test_add"


@pytest.fixture(autouse=True)
def compare_every_producer_exit(monkeypatch, request):
    """All 47 cases also compare real result builders with the frozen readers."""
    from tests.harness.test_report_consumer_parity import assert_parity, compare_judge

    compare_judge(monkeypatch)

    original_run, original_refusal = rt._run, rt._not_run_report
    case = getattr(request.node, "callspec", None)
    kind = case.params.get("kind", "complete") if case else "complete"
    kind = {
        "test_no_phase_shape_is_explicit_and_never_supports_a_ran_verdict": "could_not_start",
        "test_an_invalid_no_phase_report_cannot_be_sanitized_by_projection": "timed_out",
    }.get(request.node.originalname, kind)

    def run(*args, **kwargs):
        raw = original_run(*args, **kwargs)
        assert_parity(raw, case=kind)
        return raw

    def refusal(*args, **kwargs):
        raw = original_refusal(*args, **kwargs)
        assert_parity(raw)
        return raw

    monkeypatch.setattr(rt, "_run", run)
    monkeypatch.setattr(rt, "_not_run_report", refusal)


def counted_writer(path):
    tree = ast.parse(rt._BOOT)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    namespace = {"json": json, "counted": str(path)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<runner-counted>", "exec"), namespace)
    return namespace["_Counted"]()


@pytest.fixture
def process(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_calc.py").write_text("def test_add(): pass\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    @contextmanager
    def folder(**kwargs):
        # Stand in for the OS boundary's owner-only scratch allocation. Do not
        # change filesystem permissions or replace any report-producing code.
        path = scratch / str(len(list(scratch.iterdir())))
        path.mkdir()
        yield str(path)

    monkeypatch.setattr(confine, "run_folder", folder)
    monkeypatch.setattr(confine, "plan", lambda *a: confine.Plan("recorded-process", ""))
    monkeypatch.setattr(rt, "_git", lambda *a, **k: b"")
    monkeypatch.setattr(rt, "_private", lambda repo, commit, *commands: [b""] * len(commands))
    monkeypatch.setattr(rt, "interpreter", lambda repo: sys.executable)
    return repo, scratch


def emit(cmd, repo, kind, log):
    own = log.parent
    if kind == "could_not_start":
        raise OSError("process not started")
    if kind in ("pytest_not_found", "timed_out", "crashed", "inconsistent_report"):
        log.write_text("No module named 'pytest'" if kind == "pytest_not_found" else "", encoding="utf-8")
        return {"pytest_not_found": 1, "timed_out": None, "crashed": -9, "inconsistent_report": 1}[kind]
    plugin = counted_writer(own / "counted.json")
    plugin.pytest_itemcollected(SimpleNamespace(nodeid=NODE))
    session = SimpleNamespace(config=SimpleNamespace(rootpath=repo), items=[SimpleNamespace(nodeid=NODE)])
    if kind in ("no_tests_collected", "collection_error"):
        plugin.collected_ids.clear()
        session.items.clear()
    if kind == "deselected":
        plugin.pytest_deselected(session.items)
        session.items = []
    plugin.pytest_collection_finish(session)
    if kind == "collection_error":
        plugin.pytest_collectreport(SimpleNamespace(nodeid="test_calc.py", skipped=False, failed=True,
                                                    longrepr="ImportError: missing dependency"))
    if kind not in ("no_tests_collected", "collection_error", "deselected"):
        for phase in ("setup", "call", "teardown"):
            if kind == "interrupted" and phase != "setup":
                break
            if kind == "interrupted_failure" and phase == "teardown":
                break
            outcome = "failed" if kind in ("failed", "interrupted_failure") and phase == "call" else "passed"
            plugin.pytest_runtest_logreport(SimpleNamespace(nodeid=NODE, when=phase, outcome=outcome,
                                                           failed=outcome == "failed", longrepr="AssertionError"))
    plugin.pytest_sessionfinish(session)
    body = ('<testcase classname="test_calc" name="test_add">'
            + ('<failure message="assert False">AssertionError</failure>' if kind in ("failed", "interrupted_failure") else "")
            + '</testcase>')
    if kind in ("no_tests_collected", "deselected"):
        body = ""
    if kind == "collection_error":
        body = '<testcase name="test_calc"><error message="collection failure">ImportError</error></testcase>'
    (own / "report.xml").write_text(f"<testsuite>{body}</testsuite>", encoding="utf-8")
    (own / "loaded.json").write_text('{"modules": {}, "complete": true}', encoding="utf-8")
    log.write_text("", encoding="utf-8")
    return 1 if kind in ("failed", "interrupted_failure") else 2 if kind == "collection_error" else 5 if kind in (
        "no_tests_collected", "deselected") else 0


@pytest.mark.parametrize("copied", [False, True], ids=["in-place", "confined-copy"])
@pytest.mark.parametrize("kind,want", [
    ("passed", "passed"), ("failed", "failed"), ("could_not_start", "could_not_start"),
    ("pytest_not_found", "pytest_not_found"), ("timed_out", "timed_out"), ("crashed", "crashed"),
    ("no_tests_collected", "no_tests_collected"), ("deselected", "no_tests_collected"),
    ("collection_error", "collection_error"), ("inconsistent_report", "inconsistent_report"),
])
def test_each_execution_path_writes_a_valid_report(process, monkeypatch, copied, kind, want):
    repo, scratch = process
    monkeypatch.setattr(rt, "_bounded", lambda cmd, **kw: emit(cmd, repo, kind, kw["log"]))
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=copied,
                  python=sys.executable, scratch_dir=scratch)
    assert raw["verdict"] == want
    required = {name for name, field in rt.REPORT_FIELDS.items() if field.required}
    assert required <= raw.keys(), f"missing required fields: {required - raw.keys()}"
    assert rt.read_report(raw).valid, raw
    if kind in ("could_not_start", "pytest_not_found", "timed_out", "crashed", "inconsistent_report"):
        assert patch_review._observed([NODE], raw) == {NODE: "error"}
    observed = patch_review.observation(patch_review._verdicts([NODE], raw)[NODE])
    assert observed == (kind if kind in ("passed", "failed") else "error" if kind == "collection_error"
                        else "deselected" if kind == "deselected" else "not_collected" if kind == "no_tests_collected"
                        else "not_run" if kind == "timed_out" else want)


@pytest.mark.parametrize("api", ["run_before", "run_at_base"])
def test_copy_failure_writes_an_explicit_not_run_report(process, monkeypatch, api):
    repo, _ = process
    change = rt.Change(repo, "HEAD", "a" * 40, "a" * 40, (), {"test_calc.py": ""}, {}, (), ())

    def unavailable(*a, **k):
        raise rt.CopyNotRun("copy unavailable")

    monkeypatch.setattr(rt, "_git", unavailable)
    raw = rt.run_at_base(change, [NODE]) if api == "run_at_base" else rt.run_before(change)["test_calc.py"]
    assert raw["verdict"] == "not_run" and raw["said"] == "copy unavailable"
    assert "rootdir" in raw, "missing required 'rootdir'"
    assert raw["rootdir"] == ""
    assert rt.read_report(raw).valid, raw
    assert patch_review._observed([NODE], raw) == {}


@pytest.mark.parametrize("kind,want", [("interrupted", "inconsistent_report"), ("interrupted_failure", "failed")])
def test_an_interrupted_process_keeps_its_incomplete_phases_unknown(process, monkeypatch, kind, want):
    repo, scratch = process
    monkeypatch.setattr(rt, "_bounded", lambda cmd, **kw: emit(cmd, repo, kind, kw["log"]))
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert raw["verdict"] == want
    assert not rt.read_report(raw).valid
    assert patch_review.observation(patch_review._verdicts([NODE], raw)[NODE]) == "error"


@pytest.mark.parametrize("kind,want", [("copy_timeout", "timed_out"), ("git_unreadable", rt.CHANGED),
                                        ("changed", rt.CHANGED)])
def test_post_process_snapshot_exits_keep_the_full_report(process, monkeypatch, kind, want):
    repo, scratch = process

    def bounded(cmd, **kw):
        code = emit(cmd, repo, "passed", kw["log"])
        if kind == "copy_timeout":
            monkeypatch.setattr(rt, "COPY_MAX_FILES", 0)
        elif kind == "git_unreadable":
            def unavailable(*a, **k):
                raise rt.RepoRefused("G6_E_GIT_REPO_INVALID", "git unreadable")
            monkeypatch.setattr(rt, "_git", unavailable)
        else:
            (repo / "test_calc.py").write_text("changed\n", encoding="utf-8")
        return code

    monkeypatch.setattr(rt, "_bounded", bounded)
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=kind != "git_unreadable",
                  python=sys.executable, scratch_dir=scratch)
    assert raw["verdict"] == want
    assert rt.read_report(raw).valid, raw
    assert patch_review.observation(patch_review._verdicts([NODE], raw)[NODE]) not in ("passed", "failed")


@pytest.mark.parametrize("kind", ["limit", "budget", "baseline_failed", "mismatch", "passed", "failed"])
def test_every_replay_exit_builds_a_schema_report(process, monkeypatch, kind):
    repo, _ = process
    content = (repo / "test_calc.py").read_bytes()
    change = rt.Change(repo, "HEAD", "a" * 40, "a" * 40, (), {"test_calc.py": content.decode()}, {}, (), ())

    def git(repo, *args, **kw):
        if args[0] == "ls-files":
            return b"100644 " + b"a" * 40 + b" 0\ttest_calc.py\0" if "--stage" in args else b"test_calc.py\0"
        if args[0] == "ls-tree" and "--name-only" not in args:
            return b"100644 blob " + b"a" * 40 + b" " + str(len(content)).encode() + b"\ttest_calc.py\0"
        if args[0] == "cat-file":
            return content
        return b""

    kinds = iter(["failed"] if kind == "baseline_failed" else ["passed", "no_tests_collected" if kind == "mismatch"
                                                                else "failed" if kind == "failed" else "passed"])
    monkeypatch.setattr(rt, "_git", git)
    monkeypatch.setattr(rt, "_bounded", lambda cmd, **kw: emit(cmd, kw["cwd"], next(kinds), kw["log"]))
    if kind == "limit":
        monkeypatch.setattr(rt, "MAX_DERIVED", 0)
    if kind == "budget":
        clock = iter([0.0, 20.0])
        monkeypatch.setattr(rt.time, "monotonic", lambda: next(clock))
    raw = rt.run_before(change, timeout_s=10)["test_calc.py"]
    assert raw["verdict"] == (kind if kind in ("passed", "failed") else "not_run"), raw
    assert rt.read_report(raw).valid, raw
    if kind == "baseline_failed":
        assert raw["baseline"] == "failed"
    if kind == "mismatch":
        assert (raw["baseline"], raw["patched"]) == ("passed", "no_tests_collected")


def test_no_phase_shape_is_explicit_and_never_supports_a_ran_verdict(process, monkeypatch):
    repo, scratch = process
    monkeypatch.setattr(rt, "_bounded", lambda cmd, **kw: emit(cmd, repo, "could_not_start", kw["log"]))
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    assert raw["rootdir"] == "" and raw["test_outcomes"] is None
    for verdict in ("could_not_start", "pytest_not_found", "timed_out", "crashed", "inconsistent_report"):
        assert not rt.read_report({**raw, "verdict": verdict, "rootdir": "sub"}).valid
    for verdict in ("passed", "failed"):
        changed = {**raw, "verdict": verdict}
        assert not rt.read_report(changed).valid
        assert patch_review.observation(patch_review._verdicts([NODE], changed)[NODE]) == "error"
    for field in (name for name, value in rt.REPORT_FIELDS.items() if value.required):
        changed = raw.copy()
        del changed[field]
        assert not rt.read_report(changed).valid, field


def test_an_invalid_no_phase_report_cannot_be_sanitized_by_projection(process, monkeypatch):
    repo, scratch = process
    monkeypatch.setattr(rt, "_bounded", lambda cmd, **kw: emit(cmd, repo, "timed_out", kw["log"]))
    raw = rt._run(repo, [NODE], roots=(), timeout_s=10, network=False, copied=True,
                  python=sys.executable, scratch_dir=scratch)
    raw["seconds"] = False
    report = rt.read_report(raw)
    assert not report.valid
    assert not rt.read_report(report.wire()).valid


def test_all_report_return_sites_use_an_exercised_builder():
    tree = ast.parse(Path(rt.__file__).read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    # Fail when a new report exit bypasses the builders this file executes.
    for name in ("run", "_run", "run_before", "run_at_base"):
        nested = [node for node in ast.walk(functions[name]) if isinstance(node, ast.FunctionDef)
                  and node is not functions[name]]
        assert {node.name for node in nested} <= {"result", "moved"}
        identity_returns = {id(node) for function in nested if function.name == "moved"
                            for node in ast.walk(function) if isinstance(node, ast.Return)}
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Return) and id(node) not in identity_returns:
                expression = ast.unparse(node.value)
                assert (expression in ("found", "results") or expression.startswith((
                    "_run(", "_not_run_report(", "{'verdict': verdict,", "{**found,", "{**result("))), (name, expression)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and ast.unparse(target.value) == "results":
                        assert isinstance(node.value, ast.Call) and ast.unparse(node.value.func) in (
                            "_run", "_not_run_report", "patch_report.wire"), ast.unparse(node)


@pytest.mark.parametrize("file,function,args", [
    ("test_patch_review_security.py", "result", ([NODE],)),
    ("test_control_before_receipt.py", "result", ([NODE],)),
    ("test_every_observation_reaches_the_issuer.py", "result", ([NODE],)),
    ("test_run_again_obeys_review.py", "result", (True,)),
    ("test_requirement_evidence.py", "result", ([NODE],)),
    ("test_patch_review_imports.py", "_result", ([NODE],)),
])
def test_recorded_results_use_the_same_required_shape(file, function, args):
    # Execute each existing fixture's own builder without its git/owner-folder
    # fixture. These are controls for old consumer assertions, not producer tests.
    tree = ast.parse(Path(__file__).with_name(file).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    namespace = {"sys": sys, "NODE": NODE}
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), namespace)
    raw = namespace[function](*args)
    from tests.harness.test_report_consumer_parity import assert_parity
    assert_parity(raw)
    assert rt.read_report(raw).valid, (file, raw)
    assert patch_review.observation(patch_review._verdicts([NODE], raw)[NODE]) == "passed"


@pytest.mark.parametrize("call", ["passed", "skipped"])
def test_phase_review_recording_keeps_the_runners_total_meaning(call):
    file = Path(__file__).with_name("test_a_run_becomes_observations_by_phase.py")
    tree = ast.parse(file.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("run", "recorded")]
    namespace = {"sys": sys, "NODE": NODE}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(file), "exec"), namespace)
    raw = namespace["recorded"]({"setup": "passed", "call": call, "teardown": "passed"})
    from tests.harness.test_report_consumer_parity import assert_parity
    assert_parity(raw)
    assert rt.read_report(raw).valid, raw
    assert raw["total"] == (1 if call == "passed" else 0)


@pytest.mark.parametrize("failed", [False, True])
def test_mcp_recording_supplies_complete_dispositions(failed):
    file = Path(__file__).parents[1] / "core/test_mcp_review_loop.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    rig = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Rig")
    method = next(n for n in rig.body if isinstance(n, ast.FunctionDef) and n.name == "_run_check")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(file), "exec"), namespace)
    raw = namespace["_run_check"](SimpleNamespace(failing={"critic"} if failed else set()), None,
                                   "critic.py", "", "critic.py::test_critic", at_base=False, timeout_s=10)
    from tests.harness.test_report_consumer_parity import assert_parity
    assert_parity(raw)
    assert rt.read_report(raw).valid, raw
    assert patch_review.observation(patch_review._verdicts(["critic.py::test_critic"], raw)["critic.py::test_critic"]) == (
        "failed" if failed else "passed")


def test_recorded_replay_refusals_use_the_producers_explicit_shape():
    files = ("test_patch_review.py", "test_patch_review_security.py", "test_control_before_receipt.py",
             "test_every_observation_reaches_the_issuer.py", "test_requirement_evidence.py",
             "test_patch_review_imports.py", "test_pdm_build_output.py", "test_force_added_files.py",
             "test_ignored_file_guidance.py")
    found = 0
    for file in files:
        tree = ast.parse(Path(__file__).with_name(file).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            pairs = {key.value: value for key, value in zip(node.keys, node.values, strict=True)
                     if isinstance(key, ast.Constant)}
            if isinstance(pairs.get("verdict"), ast.Constant) and pairs["verdict"].value == "not_run":
                names = {part.id for part in ast.walk(node) if isinstance(part, ast.Name)}
                assert names <= {"verdict"}, (file, node.lineno, names)
                for verdict in ("failed", "collection_error"):
                    raw = eval(compile(ast.Expression(node), file, "eval"), {"__builtins__": {}}, {"verdict": verdict})
                    from tests.harness.test_report_consumer_parity import assert_parity
                    assert_parity(raw)
                    assert rt.read_report(raw).valid, (file, node.lineno, raw)
                found += 1
    assert found >= 9
