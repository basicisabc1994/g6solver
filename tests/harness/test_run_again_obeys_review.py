# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The run again obeys the review's own rules (G6 0.3, slice 2, repair 1; review 1's findings 5 and 10).

- An operator stop or a spent allowance (``ExecutionInterrupted``) and process cleanup that could not be confirmed
  (``ContainmentFailure``) reach the review's door from the run again, as from every other step: every handler of the
  review lets the one list (``patch_review.LETS_THROUGH``) through first, and the inventory, built from the code, fails
  on any that catches a signal by its type without it (finding 5).
- A counted check's identity is the hash of the bytes its runs executed, read before any run, and the final integrity
  snapshot comes after the last read the issuer's inputs need: a change between is the review's integrity problem, and
  a review with one gives the issuer nothing to count (finding 10).

Development fixtures only: G6's test runs are replaced by recorded results, so nothing is confined and no model is
called.
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.evidence.mapping import id_of
from g6.harness import patch_report, patch_review, requirement_evidence

NODE = "test_calc.py::test_add"
TEST = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
SIGNALS = [ExecutionInterrupted("operator stop", stopped=True),
           ContainmentFailure("owned process cleanup could not be confirmed")]


# --- review 1's finding 5, brought in as this change's own ------------------------------------------------------------


@pytest.mark.parametrize("error", SIGNALS, ids=["operator-stop", "unconfirmed-cleanup"])
def test_finding_5_the_run_again_does_not_swallow_execution_control_failures(monkeypatch, error):
    def execute(*args, **kwargs):
        raise error

    monkeypatch.setattr(patch_review, "_execute", execute)
    with pytest.raises(type(error)):
        patch_review._run_again(
            Path.cwd(), SimpleNamespace(sources=("calc.py",)),
            [SimpleNamespace(kind="requirement", checks=[NODE])], {NODE: NODE},
            {NODE: {"verdict": "passed"}},
            [{"check": patch_review.AT_BASE, "outcomes": {NODE: "failed"}}],
            time.monotonic() + 60, "a" * 64,
        )


def test_an_ordinary_error_of_g6_s_own_in_the_run_again_still_counts_nothing_and_says_why(monkeypatch):
    def execute(*args, **kwargs):
        raise RuntimeError("G6's own work failed")

    monkeypatch.setattr(patch_review, "_execute", execute)
    got = patch_review._run_again(Path.cwd(), SimpleNamespace(sources=("calc.py",)),
                                  [SimpleNamespace(kind="requirement", checks=[NODE])], {NODE: NODE},
                                  {NODE: {"verdict": "passed"}},
                                  [{"check": patch_review.AT_BASE, "outcomes": {NODE: "failed"}}],
                                  time.monotonic() + 60, "a" * 64)
    assert "verdicts" not in got and "RuntimeError" in got["why"]


# --- a review, with its runs recorded -------------------------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return root


def result(passed: bool) -> dict:
    status = "passed" if passed else "failed"
    return {"verdict": status, "said": "", "rootdir": "", "passed": int(passed), "failed": int(not passed), "not_passed": 0,
            "total": 1, "deselected": 0,
            "test_outcomes": {NODE: {"setup": "passed", "call": status, "teardown": "passed"}},
            "collected_ids": [NODE], "omitted_ids": [], "changed": [], "output": "", "seconds": 0.1,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": [] if passed else [NODE],
            "setup_errors": {}, "degraded": False, "receipt": ""}


def recorded(monkeypatch, calls: list[str], on_patch=None) -> None:
    def execute(root, ids, *, timeout_s, change=None, roots=()):
        calls.append("patch")
        if on_patch is not None:
            on_patch(len([c for c in calls if c == "patch"]))
        return result(True)

    def at_base(change, ids, *, timeout_s, roots=()):
        calls.append("base")
        return result(False)

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base", at_base)


def review(root: Path) -> dict:
    return patch_review.review_patch(root, task="Fix addition", requirements=[
        {"id": "R1", "text": "add returns a sum", "checks": [NODE]}])


def statement(reply: dict) -> dict:
    return reply["assessment"]["requirement_evidence"]["requirements"][0]


def test_a_clean_review_counts_the_check_by_the_bytes_its_runs_executed(repo, monkeypatch):
    calls: list[str] = []
    recorded(monkeypatch, calls)
    frozen = id_of(NODE, hashlib.sha256((repo / "test_calc.py").read_bytes()).hexdigest(), digest=True)
    reply = review(repo)
    assert calls[-2:] == ["base", "patch"], calls   # the run again comes after the run at the base commit
    assert statement(reply)["checked"] is True   # the positive control for every refusal below
    assert reply["assessment"]["requirement_evidence"]["certificate"]["checks"][0]["check_sha"] == frozen


def test_finding_10_a_check_changed_after_the_final_snapshot_is_not_certified_on_the_earlier_runs(repo, monkeypatch):
    # review 1's reproduction: the test replaced once every run, the run again included, had finished
    calls: list[str] = []
    recorded(monkeypatch, calls)
    frozen = id_of(NODE, hashlib.sha256((repo / "test_calc.py").read_bytes()).hexdigest(), digest=True)
    hasher = patch_review._check_shas

    def change_between(root, checks):
        if calls[-2:] == ["base", "patch"]:
            (repo / "test_calc.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")
        return hasher(root, checks)

    monkeypatch.setattr(patch_review, "_check_shas", change_between)
    reply = review(repo)
    if not reply["ok"]:
        return
    evidence = reply["assessment"]["requirement_evidence"]
    if any(s["checked"] for s in evidence.get("requirements", [])):
        assert evidence["certificate"]["checks"][0]["check_sha"] == frozen, (
            "unrun replacement test was certified on the previous tree after the final integrity snapshot",
            reply["assessment"]["outcome"], evidence["requirements"][0])


def test_a_check_replaced_after_the_run_again_and_before_the_final_snapshot_is_not_counted(repo, monkeypatch):
    calls: list[str] = []
    recorded(monkeypatch, calls)
    again = patch_review._run_again

    def then_replace(*args, **kwargs):
        got = again(*args, **kwargs)
        (repo / "test_calc.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")
        return got

    monkeypatch.setattr(patch_review, "_run_again", then_replace)
    reply = review(repo)
    assert reply["assessment"]["outcome"] == patch_review.INTEGRITY
    found = statement(reply)
    assert found["checked"] is False and requirement_evidence.INTEGRITY in found["not_counted"]
    assert "repository_changed_during_review" in found["not_counted"]
    assert reply["assessment"]["requirement_evidence"]["certificate"]["receipts"] == []


def test_a_check_replaced_between_its_identity_read_and_its_first_run_is_not_counted(repo, monkeypatch):
    calls: list[str] = []
    recorded(monkeypatch, calls)
    hasher = patch_review._check_shas

    def then_replace(root, checks):
        found = hasher(root, checks)
        (repo / "test_calc.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")
        return found

    monkeypatch.setattr(patch_review, "_check_shas", then_replace)
    reply = review(repo)
    assert reply["assessment"]["outcome"] == patch_review.INTEGRITY
    assert statement(reply)["checked"] is False


@pytest.mark.parametrize("error", SIGNALS, ids=["operator-stop", "unconfirmed-cleanup"])
def test_a_stop_during_the_run_again_reaches_the_review_s_door(repo, monkeypatch, error):
    calls: list[str] = []

    def stop_on_the_run_again(count: int) -> None:
        if calls[-2:] == ["base", "patch"]:
            raise error

    recorded(monkeypatch, calls, on_patch=stop_on_the_run_again)
    with pytest.raises(type(error)):
        review(repo)


def test_an_integrity_problem_gives_the_issuer_nothing_to_count_and_the_report_says_why():
    req = {"id": "R1", "text": "t", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": NODE, "provenance": "stated", "verdict": "passed"}]}
    runs = [{"check": requirement_evidence.AT_BASE, "outcomes": {NODE: "failed"}}]
    common = {"artifact": "a" * 64, "base_commit": "0" * 40, "check_sha": {NODE: "c" * 64},
              "written": lambda _check: False, "environment": {}, "again": {"verdicts": {NODE: "passed"}}}
    assert requirement_evidence.assess([req], runs, **common)["requirements"][0]["checked"] is True
    got = requirement_evidence.assess([req], runs, integrity=["runner_configuration_changed"], **common)
    found = got["requirements"][0]
    assert found["checked"] is False and got["certificate"]["receipts"] == [] and got["integrity"]
    assert "runner_configuration_changed" in found["not_counted"]
    line = patch_report.render({"outcome": patch_review.INTEGRITY, "reasons": [], "requirements": [],
                                "completion": {"demonstrated": [{"requirement": "R1", "checks": [NODE]}]},
                                "requirement_evidence": got})
    assert "not counted: the review found an integrity problem" in line


# --- the inventory: every handler of the review lets the signals through ----------------------------------------------

SRC = Path(g6.__file__).resolve().parent
#: The names a handler catches an execution-control signal by: each class either signal is an instance of.
CATCHES = frozenset(cls.__name__ for signal in (ExecutionInterrupted, ContainmentFailure) for cls in signal.__mro__
                    if cls is not object)


def _names(node: ast.AST | None) -> set[str]:
    if node is None:
        return {"BaseException"}   # a bare except
    found = set()
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            found.add(part.id)
        elif isinstance(part, ast.Attribute):
            found.add(part.attr)
    return found


def _lets_through(handler: ast.ExceptHandler, copies_allowed: bool) -> bool:
    names = _names(handler.type)
    named = "LETS_THROUGH" in names or (copies_allowed and {"ExecutionInterrupted", "ContainmentFailure"} <= names)
    return named and len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None


def swallowing(tree: ast.Module, copies_allowed: bool = False) -> list[tuple[str, int]]:
    """(function, line) of every handler that catches a signal by one of its types (``CATCHES``, a bare except) with no
    handler before it, in the same ``try``, that lets the list through."""
    found = []

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Try | ast.TryStar):
                through = False
                for handler in child.handlers:
                    if _lets_through(handler, copies_allowed):
                        through = True
                    elif _names(handler.type) & CATCHES and not through:
                        found.append((where, handler.lineno))
            visit(child, where)

    visit(tree, "<module>")
    return found


def _tree(rel: str) -> ast.Module:
    return ast.parse((SRC / rel).read_text(encoding="utf-8"))


def _child_diagnostic_handlers(rel: str, tree: ast.Module) -> set[int]:
    """Only the recorder copied into the child may absorb a failure while describing its caught exception.

    This is not a host execution-control exception: no host call site invokes the recorder. Require its exact
    embedding seam, and exclude only handlers physically inside that function, never another function by name.
    """
    if rel != 'harness/behaviour_runner.py':
        return set()
    recorders = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_exception_observation']
    if not recorders:  # the d09eca9 baseline predates the recorder
        return set()
    assert len(recorders) == 1
    recorder = recorders[0]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == recorder.name], 'the host must not invoke the child diagnostic recorder'
    bootstrap = next(n for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'BOOTSTRAP' for t in n.targets))
    embeddings = [n for n in ast.walk(bootstrap)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'getsource'
                  and isinstance(n.func.value, ast.Name) and n.func.value.id == 'inspect'
                  and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id == recorder.name]
    assert embeddings, 'the recorder must be embedded in the isolated bootstrap'
    references = {id(n) for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id == recorder.name}
    assert references == {id(n.args[0]) for n in embeddings}, 'the host must not alias the child diagnostic recorder'
    return {n.lineno for n in ast.walk(recorder) if isinstance(n, ast.ExceptHandler)}


def _called_by_the_review() -> list[str]:
    """The harness modules the review imports, anywhere in it (its optional stages are imported inside functions)."""
    names = {f"{node.module}.{alias.name}" if node.module == "g6.harness" else node.module
             for node in ast.walk(_tree("harness/patch_review.py")) if isinstance(node, ast.ImportFrom)
             for alias in node.names if node.module and node.module.startswith("g6.harness")}
    return sorted(name.removeprefix("g6.").replace(".", "/") + ".py" for name in names
                  if (SRC / (name.removeprefix("g6.").replace(".", "/") + ".py")).is_file())


#: Handlers in the modules the review calls that catch a signal by its type and do not let it through, on this
#: change's base, each with what it guards. Each is outside this repair (finding 5 is the run again's) and listed for
#: a follow-up; the list may only shrink, and a new one fails.
OPEN: dict[tuple[str, str], str] = {
    ("patch_report.py", "safe_render"): "renders the report from fields; it runs and waits on nothing",
    ("verification_proposer.py", "context"): "reads a file of the tree for the proposer's context (OSError)",
    ("verification_proposer.py", "complete"): "the proposer's model call, after its budget's own refusal",
    ("verification_search.py", "measure_adequacy"): "a mutation run of test adequacy, after mutation_check's own stop",
    ("verification_templates.py", "resolve"): "reads and parses a module file to resolve a name (OSError)",
    ("verification_templates.py", "_declared"): "reads the repository's import-linter configuration (OSError)",
    ("verification_templates.py", "structural"): "reads a package's files for the structural template (OSError)",
}


def test_the_list_is_the_two_signals_and_the_review_names_them_nowhere_else():
    assert patch_review.LETS_THROUGH == (ExecutionInterrupted, ContainmentFailure)
    tree = _tree("harness/patch_review.py")
    named = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Attribute)
             and node.attr in ("ExecutionInterrupted", "ContainmentFailure")]
    definition = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(getattr(t, "id", "") == "LETS_THROUGH" for t in node.targets))
    assert named and set(named) == {definition.lineno}, "the review names the signals once, in its one list"


def test_no_handler_of_the_review_or_its_issuer_adapter_catches_a_signal_it_lets_through():
    for rel in ("harness/patch_review.py", "harness/requirement_evidence.py"):
        assert swallowing(_tree(rel)) == [], rel


def test_the_handlers_in_what_the_review_calls_that_catch_a_signal_are_known_and_only_shrink():
    called = _called_by_the_review()
    assert {"harness/verification_search.py", "harness/requirement_evidence.py"} <= set(called)
    found = set()
    for rel in called:
        lines = _tree(rel)
        child_diagnostics = _child_diagnostic_handlers(rel, lines)
        for function, _line in swallowing(lines, copies_allowed=True):
            if _line not in child_diagnostics:
                found.add((rel.removeprefix("harness/"), function))
    assert sorted(found - set(OPEN)) == [], "a new handler catches an execution-control signal: let it through"
    assert all(why.strip() for why in OPEN.values())


def test_child_diagnostic_classification_cannot_cover_a_host_signal_handler():
    rel = 'harness/behaviour_runner.py'
    tree = _tree(rel)
    original = _child_diagnostic_handlers(rel, tree)
    planted = ast.parse('def name():\n    try:\n        run()\n    except BaseException:\n        return None\n')
    ast.increment_lineno(planted, max(n.end_lineno or n.lineno for n in tree.body))
    tree.body.extend(planted.body)
    assert _child_diagnostic_handlers(rel, tree) == original
    assert any(function == 'name' and line not in original for function, line in swallowing(tree))
    if original:
        # A real host call to the recorder invalidates the classification, even if the embedding remains.
        for source in ('_exception_observation(exc, {}, False)', 'alias = _exception_observation'):
            planted = ast.parse(source).body
            tree.body.extend(planted)
            with pytest.raises(AssertionError, match='host must not'):
                _child_diagnostic_handlers(rel, tree)
            del tree.body[-len(planted):]


def test_the_scan_finds_a_planted_handler_that_swallows_a_signal():
    # survival control: OSError catches both signals by type; a handler that lets the list through first is clean
    planted = ast.parse("def run():\n    try:\n        go()\n    except OSError:\n        return None\n\n\n"
                        "def kept():\n    try:\n        go()\n    except LETS_THROUGH:\n        raise\n"
                        "    except Exception:\n        return None\n")
    assert swallowing(planted) == [("run", 4)]
    assert {"OSError", "TimeoutError", "ConnectionResetError", "Exception", "BaseException"} <= CATCHES
