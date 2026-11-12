"""Every state in which a run establishes nothing, against every place that says what a run establishes.

Review 4 of the evidence follow-up found G6 stating support from a report it had read only in part: a pass beside a
record of changed source bytes, a degraded mark or a noncompletion mark (findings 1, 2 and 6), a replay of code from
outside its copy (3 and 4), a check with a test left out (7). The class, ``guard_covers_less_than_it_guards``, was met
before in this change, so it is removed at its root: the report's one reading decides every such state
(``repo_tests.NO_AUTHORITY``, ``RunReport.withheld``, ``repo_tests.established``), and every consumer states a pass, a
failure, a replay count or a negative control only through it.

The inventories here are derived from the code: every product function that reads a run report's decision or one of
its state fields is found by a scan and must be classified, and every function classified as stating something has a
row in the matrix, where each planted state must leave it stating nothing while the clean report makes it speak.
"""
import ast
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.algorithm_design.autonomous import RepoTestsRunner
from g6.bundles import agent_change
from g6.harness import completion_loop, patch_review as review, verification_search as search
from g6.metaharness import coding
from g6.workforce import bench, receipt_store, repo_tests as rt
from tests.harness.efu2_cases import FAILED, NODE, PASSED, assess_runs, report

CHANGE = SimpleNamespace(repo=Path.cwd(), files=("calc.py",), sources=("calc.py",), base_commit="0" * 40,
                         before={"test_calc.py": "def test_add(): pass\n"}, renamed={})
SAID_HOW = {"failure_messages": {NODE: "AssertionError: assert 1 == 2"}}


def clean(side: str, **over) -> dict:
    """A report the producer could write: a pass, or a failure of the test's body that says how it failed."""
    return report(PASSED, **over) if side == "pass" else report(FAILED, **{**SAID_HOW, **over})


#: Each planted state: the ``NO_AUTHORITY`` state it is, the sides it is planted on (a pass, a failure of the body),
#: and the fields that plant it. ``completed`` is False on every failure the producer writes (pytest's exit was 1), so
#: on a failure it is the opposite mark, a clean exit, that contradicts the verdict.
PLANTS = {
    "a summary its phases contradict": ("unreadable", ("pass", "fail"), None),
    "a crash after the phases were written": ("no_result", ("pass", "fail"), {"verdict": "crashed"}),
    "changed files named beside the verdict": ("changed_source", ("pass", "fail"), {"changed": ["calc.py"]}),
    "the verdict that says files changed": ("changed_source", ("pass", "fail"),
                                            {"verdict": rt.CHANGED, "changed": ["calc.py"]}),
    "marked degraded": ("degraded", ("pass", "fail"), {"degraded": True}),
    "a pass marked not completed": ("incomplete", ("pass",), {"completed": False}),
    "a failure marked completed": ("unreadable", ("fail",), {"completed": True}),
    "a session stopped while collecting": ("incomplete", ("fail",), {
        "collection_stopped": True, "failure_messages": {**SAID_HOW["failure_messages"], "test_other.py": "no import"}}),
    "an incomplete list of loaded modules": ("loading_unknown", ("pass", "fail"),
                                             {"loaded_complete": False, "loaded_outside": {}}),
    "one loading field without the other": ("loading_unknown", ("pass", "fail"), {"loaded_complete": True}),
    "the tree's module loaded from outside it": ("foreign_code", ("pass", "fail"), {
        "loaded_complete": True, "loaded_outside": {"calc": "elsewhere/calc.py"}, "python_sources": ["calc.py"]}),
}
CASES = [(name, side) for name, (_, sides, _) in PLANTS.items() for side in sides]


def planted(name: str, side: str, **base) -> dict:
    fields = PLANTS[name][2]
    if fields is None:
        fields = {"verdict": "failed" if side == "pass" else "passed"}
    return clean(side, **{**base, **fields})


def test_every_state_is_planted_and_read_by_the_one_reader():
    assert {state for state, _, _ in PLANTS.values()} == set(rt.NO_AUTHORITY)
    for side in ("pass", "fail"):
        decided = rt.established(clean(side))
        assert decided.report.withheld == () and decided.withheld == ""
        assert decided.of() == decided.of(NODE) == ("passed" if side == "pass" else "failed")
    for name, side in CASES:
        state, raw = PLANTS[name][0], planted(name, side)
        decided = rt.established(raw)
        withheld = decided.report.withheld + ((decided.withheld,) if decided.withheld else ())
        assert state in withheld, (name, side, withheld)
        assert decided.of() == decided.of(NODE) == "unknown", (name, side)
        if state != "foreign_code":   # whose code ran needs the listing; every other state is the report's own
            assert decided.report.outcome == decided.report.body_result(NODE) == "unknown", (name, side)


def test_a_state_field_of_the_wrong_type_or_value_establishes_nothing():
    for field, values in {"degraded": ["yes", 1, None, []], "completed": ["no", 0, None],
                          "changed": ["calc.py", {"calc.py": ""}, [1], None],
                          "collection_stopped": ["yes", 1, []]}.items():
        for value in values:
            for side in ("pass", "fail"):
                decided = rt.read_report(clean(side, **{field: value}))
                assert decided.withheld and decided.outcome == decided.body_result(NODE) == "unknown", (field, value)


# -- what each consumer states of one report --------------------------------------------------------------------------

def _stated(monkeypatch, tmp_path, raw):
    found = review._verdicts([NODE], raw)[NODE]
    return review.observation(found) in rt.RAN and not review._loaded_elsewhere(CHANGE, raw)


def _observed(monkeypatch, tmp_path, raw):
    return review._observed([NODE], raw).get(NODE) in rt.RAN and not review._loaded_elsewhere(CHANGE, raw)


def _control(monkeypatch, tmp_path, raw):
    """The run at the base commit as a negative control: the requirement is checked only when it killed one."""
    return assess_runs(monkeypatch, raw, clean("pass"))["requirements"][0]["checked"]


def _support(monkeypatch, tmp_path, raw):
    """The patch's runs (the stated run, and the run again after the control) as support."""
    return assess_runs(monkeypatch, clean("fail"), raw)["requirements"][0]["checked"]


def _search(monkeypatch, tmp_path, raw):
    return search._guarded(CHANGE, raw, search.reading(raw, NODE))["result"] in rt.RAN


def _probe_set(monkeypatch, tmp_path, raw):
    return completion_loop._read(CHANGE, [raw]).exit is not None


def _bundle_mutant(monkeypatch, tmp_path, raw):
    return agent_change._as_run(raw, [NODE], None).execution_id != ""


def _bundle(monkeypatch, first, replay):
    change = SimpleNamespace(files=("test_calc.py", "calc.py"), before={"test_calc.py": "def test_add(): pass\n"},
                             after={"test_calc.py": "def test_add(): pass\n"}, renamed={}, runner_files=())
    monkeypatch.setattr(rt, "checked_repo", lambda *a: Path.cwd())
    monkeypatch.setattr(rt, "change", lambda *a: change)
    monkeypatch.setattr(rt, "diff_text", lambda *a: "")
    monkeypatch.setattr(rt, "run", lambda *a, **kw: first)
    monkeypatch.setattr(rt, "run_before", lambda *a, **kw: {"test_calc.py": replay})
    monkeypatch.setattr(agent_change, "_mutations", lambda *a: (
        {"check": "mutations", "result": "not run", "detail": "", "label": "qualified-draft"}, []))
    out = agent_change.run({"repo": "recorded", "task": "retain add", "answer": "changed implementation",
                            "tests": "test_calc.py"})
    return {row["check"]: row["result"] for row in out.rows}


COUNTED = {"passed": 1, "failed": 0, "not_passed": 0, "total": 1}


def _bundle_first(monkeypatch, tmp_path, raw):
    return _bundle(monkeypatch, raw, clean("pass"))["Your tests on the change"] == "passed"


def _bundle_replay(monkeypatch, tmp_path, raw):
    return _bundle(monkeypatch, clean("pass", **COUNTED), raw)["Original tests on the new code"] != "not run"


def _algorithm(monkeypatch, tmp_path, raw):
    runner = object.__new__(RepoTestsRunner)
    runner._checker, runner._sources, runner.repo = "checker", {}, None
    runner.mission = SimpleNamespace(tests=(NODE,))
    monkeypatch.setattr(runner, "_checker_digest", lambda: runner._checker)
    monkeypatch.setattr(runner, "_source_digests", lambda: runner._sources)
    return runner._observed(raw, "source").completed


def _bench(monkeypatch, tmp_path, raw):
    monkeypatch.setattr(bench, "_repo", lambda *a: Path.cwd())
    monkeypatch.setattr(bench.confine, "run_folder", lambda **kw: nullcontext(str(Path.cwd())))
    monkeypatch.setattr(rt, "run", lambda *a, **kw: raw)
    got = bench._in_place({"repo": "recorded", "tests": ["test_calc.py"]}, None)
    return bool(got.get("ok")) and got["result"]["passed"]


def _oracle(monkeypatch, tmp_path, raw):
    return coding.coding_oracle(run=lambda *a, **kw: raw)(str(Path.cwd()), {"tests": [NODE]})


def _mutant(monkeypatch, tmp_path, raw):
    """A mutant counts as caught only when its run establishes a failure."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    monkeypatch.setattr(rt, "change", lambda *a: SimpleNamespace(files=("calc.py",)))
    return coding._mutants_killed(tmp_path, [NODE], timeout_s=30, run=lambda *a, **kw: raw)


def _baseline(monkeypatch, tmp_path, raw):
    """A replay's base-commit run: the file is replayed on the patch only when that run established a pass."""
    runs = iter([raw, clean("fail")])
    monkeypatch.setattr(rt, "repository_copy", lambda *a, **kw: nullcontext(Path.cwd()))
    monkeypatch.setattr(rt, "_base_revision", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(rt, "interpreter", lambda *a: "recorded-python")
    monkeypatch.setattr(rt, "_run", lambda *a, **kw: next(runs))
    return rt.run_before(CHANGE)["test_calc.py"]["verdict"] != "not_run"


def _receipt(monkeypatch, tmp_path, raw):
    try:
        body = receipt_store._body(started_at="", finished_at="", before={}, after={}, base_commit="",
                                   python=sys.executable, requested=[NODE], found=raw)
    except ValueError:
        return False
    return receipt_store.counts_as_pass(body)


#: Every place that states something of a run, with the sides it states (a pass, a failure) and the fields its clean
#: report needs beyond the common ones.
ROWS = {
    "the stated checks' run": (_stated, ("pass", "fail"), {}),
    "another run that observed a stated check": (_observed, ("pass", "fail"), {}),
    "the run at the base commit as a control": (_control, ("fail",), {}),
    "the patch's runs as support": (_support, ("pass",), {}),
    "a proposed check's run": (_search, ("pass", "fail"), {}),
    "a probe set's run": (_probe_set, ("pass", "fail"), {}),
    "the bundle's mutant run": (_bundle_mutant, ("pass", "fail"), {}),
    "the bundle's first run": (_bundle_first, ("pass",), COUNTED),
    "the bundle's replay of an original test": (_bundle_replay, ("pass", "fail"), {}),
    "the algorithm mission's run": (_algorithm, ("pass", "fail"), {"degraded": False}),
    "the bench's run in place": (_bench, ("pass",), {**COUNTED, "seconds": 0.0, "interpreter_source": "recorded"}),
    "the coding oracle's run": (_oracle, ("pass",), {}),
    "the coding oracle's mutant run": (_mutant, ("fail",), {}),
    "a replay's baseline": (_baseline, ("pass",), {"not_passed": 0, "deselected": 0}),
    "a test receipt": (_receipt, ("pass",), {**COUNTED, "completed": True, "degraded": False}),
}
#: What a row does not decide, and who does. Nothing else is left out of the matrix.
ELSEWHERE = {
    ("a test receipt", "loading_unknown"): "repo_tests._run withholds the receipt before a body is made",
    ("a test receipt", "foreign_code"): "repo_tests._run withholds the receipt before a body is made",
}


@pytest.mark.parametrize("row", sorted(ROWS))
def test_each_consumer_states_its_clean_report(row, monkeypatch, tmp_path):
    states, sides, base = ROWS[row]
    for side in sides:
        with monkeypatch.context() as patch:
            assert states(patch, tmp_path, clean(side, **base)), (row, side)


#: Every cell of the matrix: each row, with each state planted on each side the row states.
CELLS = [(row, name, side) for row in sorted(ROWS) for name, side in CASES
         if side in ROWS[row][1] and (row, PLANTS[name][0]) not in ELSEWHERE]


def test_the_matrix_leaves_out_only_what_is_named():
    stated = sum(len(set(sides) & set(PLANTS[name][1])) for _, sides, _ in ROWS.values() for name in PLANTS)
    left_out = sum(1 for row, state in ELSEWHERE for name, (planted_state, sides, _) in PLANTS.items()
                   for side in sides if planted_state == state and side in ROWS[row][1])
    assert len(CELLS) == stated - left_out and left_out == 3
    assert all(row in ROWS and state in rt.NO_AUTHORITY for row, state in ELSEWHERE)


@pytest.mark.parametrize("row,name,side", CELLS)
def test_no_consumer_states_anything_of_a_run_that_establishes_nothing(row, name, side, monkeypatch, tmp_path):
    states, _, base = ROWS[row]
    assert not states(monkeypatch, tmp_path, planted(name, side, **base)), (row, name, side)   # the planted value stands


@pytest.mark.parametrize("consumer", ["reader", "review", "search", "oracle", "bundle"])
def test_a_check_with_a_test_left_out_is_no_result(consumer, monkeypatch):
    """Finding 7's state, through every reader of a check's scope: one parameter ran, pytest left the other out."""
    first, second = NODE + "[first]", NODE + "[second]"
    for phases, verdict in ((PASSED, "passed"), (FAILED, "failed")):
        failed = [first] if verdict == "failed" else []
        how = {node: "AssertionError: assert 1 == 2" for node in failed}
        whole = report(phases, test_outcomes={first: dict(phases), second: dict(PASSED)}, failed_ids=failed,
                       collected_ids=[first, second], failure_messages=how)
        partial = report(phases, test_outcomes={first: dict(phases)}, failed_ids=failed, collected_ids=[first, second],
                         omitted_ids=[second], failure_messages=how)
        assert rt.read_report(whole).valid and rt.read_report(partial).valid
        said = {
            "reader": lambda raw: rt.established(raw).of(NODE),
            "review": lambda raw: review.observation(review._verdicts([NODE], raw)[NODE]),
            "search": lambda raw: search.reading(raw, NODE)["result"],
            "oracle": lambda raw: "passed" if coding.coding_oracle(run=lambda *a, **kw: raw)(
                str(Path.cwd()), {"tests": [NODE]}) else "unknown",
            "bundle": lambda raw: verdict if agent_change._as_run(raw, [NODE], None).execution_id else "unknown",
        }[consumer]
        if not (consumer == "oracle" and verdict == "failed"):   # an oracle states a pass only
            assert said(whole) == verdict, (consumer, verdict)
        assert said(partial) not in rt.RAN, (consumer, verdict, said(partial))


# -- the inventory, from the code ------------------------------------------------------------------------------------

#: The reader, its decisions, and the three readers built on them that say what a run shows of a check.
DECISIONS = {"read_report", "established", "shadowed", "loaded_from", "body_result", "read_phases",
             "_verdicts", "_observed", "reading"}
STATE_FIELDS = {"degraded", "completed", "changed", "collection_stopped", "loaded_outside", "loaded_complete",
                "python_sources", "omitted_ids", "deselected"}


def scan(root: Path) -> dict[tuple[str, str], set[str]]:
    """Each product function that calls the report's reader, one of its decisions or a reader built on them, or reads
    a state field of a mapping by name, with what it calls and reads."""
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()

        def visit(node, scope=""):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = node.name
            if isinstance(node, ast.Call):
                called = node.func
                name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
                if name in DECISIONS:
                    found.setdefault((relative, scope), set()).add(name)
                if (name == "get" and node.args and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value in STATE_FIELDS):
                    found.setdefault((relative, scope), set()).add("field:" + node.args[0].value)
            if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                    and isinstance(node.slice, ast.Constant) and node.slice.value in STATE_FIELDS):
                found.setdefault((relative, scope), set()).add("field:" + node.slice.value)
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        source = path.read_text(encoding="utf-8")
        if "repo_tests" in source or relative == "workforce/repo_tests.py":
            visit(ast.parse(source))
    return found


#: Every function the scan finds, classified. A row of the matrix (``ROWS``) for each that states something of a run;
#: for the others, why what they read states nothing.
CLASSIFIED = {
    # the reader itself, and the producer
    ("workforce/repo_tests.py", "read_report"): "the one reader: decides every state from the whole report",
    ("workforce/repo_tests.py", "of"): "the reading's statement for a check, with whose code ran",
    ("workforce/repo_tests.py", "established"): "the one statement: the reading and whose code ran",
    ("workforce/repo_tests.py", "shadowed"): "whose code ran, for the files given",
    ("workforce/repo_tests.py", "loaded_from"): "whose code ran, for every Python file of the tree",
    ("workforce/repo_tests.py", "run_before"): "a replay's baseline",
    ("workforce/repo_tests.py", "_run"): "the producer: withholds a receipt from a run of code from elsewhere",
    ("workforce/repo_tests.py", "result"): "the producer: writes the report's fields",
    ("workforce/repo_tests.py", "_progress"): "the producer: reads its plugin's progress file through the reader",
    ("workforce/repo_tests.py", "_judge"): "the producer: the run's verdict, from the exit code and the tallies",
    ("workforce/repo_tests.py", "_said"): "words: a message table, not a report",
    ("workforce/receipt_store.py", "_body"): "a test receipt",
    ("workforce/receipt_store.py", "counts_as_pass"): "a test receipt",
    ("workforce/bench.py", "_in_place"): "the bench's run in place",
    ("workforce/coding.py", "_unchecked"): "not a run report: a reconfiguration decision's record",
    ("workforce/coding.py", "advance"): "not a run report: a reconfiguration decision's record",
    ("workforce/coding.py", "repair_turn"): "not a run report: a reconfiguration decision's record",
    # the patch review
    ("harness/patch_review.py", "_verdicts"): "the stated checks' run",
    ("harness/patch_review.py", "_assess"): "the stated checks' run",
    ("harness/patch_review.py", "_at_base"): "the run at the base commit as a control",
    ("harness/patch_review.py", "_run_again"): "the patch's runs as support",
    ("harness/patch_review.py", "_observed"): "another run that observed a stated check",
    ("harness/patch_review.py", "_loaded_elsewhere"): "whose code ran, for the change's files: read at each run",
    ("harness/patch_review.py", "unusable"): "whose code ran, for the change's files: read at each run in place",
    ("harness/patch_review.py", "_on_base"): "names the failing files to replay; a regression it then reports is a "
                                             "refusal, never support",
    ("harness/patch_review.py", "_confined"): "refuses an unconfined run; passes the reading's own wire form on",
    ("harness/patch_review.py", "_collection_errors"): "words: which files failed to collect",
    ("harness/patch_review.py", "_failed_ids"): "words: which tests a run lists as failed",
    ("harness/patch_review.py", "_in_repository"): "names: a test id with its root folder",
    ("harness/patch_review.py", "_listing"): "names: one listing of the report",
    ("harness/patch_review.py", "_not_found"): "words: a selection pytest did not find",
    ("harness/patch_review.py", "_not_loaded"): "turns a check's verdict into not_run, never into a pass or a failure",
    ("harness/patch_review.py", "_patch_imports_missing"): "words: a module the patch imports that is not installed",
    ("harness/patch_review.py", "phases_of"): "the reading's phases, for the helpers above",
    ("harness/patch_review.py", "_whole"): "whether one phase record is complete: the reader's own rule",
    ("harness/patch_review.py", "body_of"): "one phase record's body: the reader's own rule",
    ("harness/patch_review.py", "_record"): "the reply's record of a run: copies what the run said of its loading",
    ("harness/patch_review.py", "_untrusted"): "words: the files a run changed",
    ("harness/patch_review.py", "infer"): "the coding gate's item for a run, which the gate counts as G6's own only "
                                          "through a stored receipt (a test receipt)",
    # the other harness readers
    ("harness/verification_search.py", "reading"): "a proposed check's run",
    ("harness/verification_search.py", "read"): "a proposed check's run",
    ("harness/verification_search.py", "measure_adequacy"): "test adequacy: counts of mutants for the report only, "
                                                            "never an outcome; ruled outside this change",
    ("harness/verification_search.py", "_messages"): "words: how a check failed",
    ("harness/verification_search.py", "_refused_access"): "makes a failure an error, never a result",
    ("harness/verification_search.py", "_uncollected"): "whether the check's own file failed to collect",
    ("harness/completion_loop.py", "_read"): "a probe set's run",
    ("harness/completion_loop.py", "_outcome"): "one case's phases: the reader's own rule, a fixture error unknown",
    # the whole-repository contexts
    ("bundles/agent_change.py", "_as_run"): "the bundle's mutant run",
    ("bundles/agent_change.py", "run"): "the bundle's first run",
    ("metaharness/coding.py", "oracle"): "the coding oracle's run",
    ("metaharness/coding.py", "run_mutant"): "the coding oracle's mutant run",
    ("algorithm_design/autonomous.py", "_observed"): "the algorithm mission's run",
    ("algorithm_design/autonomous.py", "baseline"): "the algorithm mission's run",
    ("algorithm_design/autonomous.py", "run"): "the algorithm mission's run",
}


def test_every_reader_of_a_report_s_decision_or_state_is_classified():
    found = scan(Path(rt.__file__).parents[1])
    assert set(found) == set(CLASSIFIED), set(found) ^ set(CLASSIFIED)
    rows = {why for why in CLASSIFIED.values() if why in ROWS}
    assert rows | {"the bundle's replay of an original test"} == set(ROWS)   # the bundle's run states both


def test_the_scan_finds_a_new_reader_and_a_new_field_read(tmp_path):
    (tmp_path / "new.py").write_text(
        "from g6.workforce import repo_tests\n"
        "def mine(ran):\n    return repo_tests.read_report(ran).valid and not ran.get('degraded')\n"
        "def other(ran):\n    return ran['completed']\n", encoding="utf-8")
    assert scan(tmp_path) == {("new.py", "mine"): {"read_report", "field:degraded"},
                              ("new.py", "other"): {"field:completed"}}


def test_only_the_reader_and_the_producer_decide_from_a_state_field():
    """A state field read by name outside the reader decides no pass and no failure: each such read is listed."""
    found = {key: {name[6:] for name in names if name.startswith("field:")}
             for key, names in scan(Path(rt.__file__).parents[1]).items()}
    outside = {key: fields for key, fields in found.items() if fields and key[0] != "workforce/repo_tests.py"
               and not CLASSIFIED[key].startswith("not a run report")}
    assert outside == {
        ("algorithm_design/autonomous.py", "_observed"): {"degraded"},   # stricter: completed needs an explicit False
        ("bundles/agent_change.py", "run"): {"changed"},                 # words: the files a run changed
        ("workforce/bench.py", "_in_place"): {"changed"},                # words: the files a run changed
        ("harness/verification_search.py", "reading"): {"changed"},      # words: the files a run changed
        ("harness/patch_review.py", "_untrusted"): {"changed"},          # words: the files a run changed
        ("harness/patch_review.py", "_record"): {"loaded_complete"},     # the reply's record of the run
        ("harness/patch_review.py", "_verdicts"): {"collection_stopped"},   # words: why a check did not run
        ("harness/patch_review.py", "infer"): {"degraded"},              # the coding gate's item (see CLASSIFIED)
        ("workforce/receipt_store.py", "_body"): {"deselected", "omitted_ids"},   # a receipt's record of the run
        ("workforce/receipt_store.py", "counts_as_pass"): {"completed", "degraded"},   # a stored receipt's own fields
    }


def _calls(function: ast.AST) -> set[str]:
    return {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(function) if isinstance(node, ast.Call)}


def test_every_run_the_review_reads_is_asked_whose_code_it_ran():
    """The patch review's rule for whose code ran is the change's own files (``_loaded_elsewhere``, ``unusable``),
    asked where each run is made: every function that reads a run's checks asks it, or is the helper they call."""
    root = Path(rt.__file__).parents[1]
    functions = {node.name: node for node in ast.walk(ast.parse(
        (root / "harness/patch_review.py").read_text(encoding="utf-8"))) if isinstance(node, ast.FunctionDef)}
    readers = {name for name, node in functions.items() if _calls(node) & {"_verdicts", "_observed"}}
    assert readers == {"_assess", "_at_base", "_run_again", "_on_base", "_observed"}
    assert all(_calls(functions[name]) & {"_loaded_elsewhere", "unusable"} for name in readers - {"_observed"})
    tree = ast.parse((root / "harness/verification_search.py").read_text(encoding="utf-8"))
    counted = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and "_verdicts" in _calls(node)]
    assert [node.name for node in counted] == ["measure_adequacy"] and "_loaded_elsewhere" in _calls(counted[0])
    wrapped = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and getattr(node.func, "id", "") == "reading"]
    guarded = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and getattr(node.func, "id", "") == "_guarded" and any(
                   isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == "reading" for arg in node.args)]
    assert len(wrapped) == len(guarded) == 1   # the one reading of a proposed check's run is guarded where it is made
