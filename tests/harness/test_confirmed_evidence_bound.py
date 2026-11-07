# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Evidence bound to what ran, every result read, and a report that keeps the meaning (question slice, repair 1;
review 1's findings 4, 8, 9 and 11, one root: evidence not bound to what actually ran; and 5 and 10, one root: a
result that is not read, and a rendering that loses meaning).

- A saved review gives candidates only when it is bound to the question's task and repository and observed the tree
  the question is asked about.
- Each run of a confirmed check on the patch is made on a copy whose bytes are the artefact's; a copy that differs is
  refused and nothing is counted. The artefact digest reaches the issuer only from bound runs.
- The standard is read when a queued review executes, and the issuer's statement names the standard it used.
- A confirmed check's entry point must be the repository's file G6 resolved: a module from anywhere else (the standard
  library, a module loaded before, an installed copy) is refused, never passed; the guard is the loader's own.
- Every run of the confirmed case on the patch reaches the review's outcome: any failure is a defect.
- The report shows the confirmed input, positional and keyword, and the outcome, in the one encoding.

Development fixtures only: G6's test runs are replaced by recorded results or by the real compiled check run in this
process on trusted fixture code; nothing is confined, no model is called and no real dialog opens.
"""
from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from g6.core.ledger import digest
from g6.harness import behaviour_question as bq
from g6.harness import confirmed_checks, patch_report, patch_review, requirement_evidence
from g6.harness import question_store as qs
from g6.harness import verification_search as vs
from g6.harness.confirmed_expectation import ExpectationStandard, confirm
from g6.harness.verification_templates import TemplateInstance
from g6.mcp import behaviour, review_reply
from g6.workforce import repo_tests

ROOT = Path(__file__).resolve().parents[2]
TASK = "Repair addition for positive integers."
AT = "2026-11-07T10:00:00Z"
BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
NODE = "test_calc.py::test_add"
REQUIREMENTS = [{"id": "R1", "text": "sum", "checks": [NODE]}]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(qs.BUDGET_ENV, raising=False)


def candidate(**extra) -> dict:
    return {"requirement_id": "R1", "function": "calc.add", "args": [2, 3],
            "outcomes": {"A": {"returns": 5}, "B": {"returns": -1}}, "cited": TASK, "evidence": ["calc.py: add"],
            **extra}


def make_repo(parent: Path, test_text: str | None = None) -> Path:
    root = parent / "repo"
    root.mkdir()
    (root / "calc.py").write_text(BAD, encoding="utf-8")
    (root / "test_calc.py").write_text(test_text if test_text is not None else
                                       "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                                       encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                                "-c", "commit.gpgsign=false", "commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=20)
    (root / "calc.py").write_text(GOOD, encoding="utf-8")
    return root


class Dialog:
    def __init__(self, content=None, action="accept"):
        self.session, self.request_id, self.messages = self, "fixture", []
        self.content = content if content is not None else {"choice": "A", "name": "Ada"}
        self.action = action

    async def elicit_form(self, **kwargs):
        self.messages.append(kwargs["message"])
        return SimpleNamespace(action=self.action, content=self.content)


def ask(root: Path, *, data=None, dialog=None, task=TASK, **kwargs) -> dict:
    return asyncio.run(behaviour.confirm(dialog or Dialog(), str(root), task,
                                         candidates=[candidate()] if data is None else data, **kwargs))


def standard() -> ExpectationStandard:
    [one] = bq.caller_candidates(TASK, [candidate()])
    return ExpectationStandard().with_expectation(confirm(one.question("b" * 64), "A", answered_by="person:Ada",
                                                          answered_at=AT))


def run_result(node: str, status: str = "passed") -> dict:
    tests = {} if status == "collection_error" else {node: {"setup": "passed", "call": status, "teardown": "passed"}}
    return {"verdict": status, "said": "", "passed": int(status == "passed"), "failed": int(status == "failed"),
            "not_passed": 0, "total": len(tests), "deselected": 0, "test_outcomes": tests,
            "collected_ids": list(tests), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.01,
            "python": sys.executable, "interpreter_source": "g6", "isolation": {"backend": "fixture", "why": ""},
            "failed_ids": [node] if status == "failed" else [], "setup_errors": {}, "degraded": False, "receipt": "",
            "failure_messages": {node: "E   Failed: case 0 returned -1, expected 5"} if status == "failed" else {}}


def fake_stated(monkeypatch, at_base: str = "failed") -> None:
    monkeypatch.setattr(patch_review, "_execute", lambda *a, **k: run_result(NODE))
    monkeypatch.setattr(patch_review, "_execute_at_base", lambda *a, **k: run_result(NODE, at_base))


# --- finding 4: a saved review is used only for its own task, repository and tree -----------------------------------


def _saved_review(monkeypatch, source: Path, task: str, *, queued: bool = True) -> tuple[str, dict]:
    """A finished review of ``source`` for ``task``, saved as G6 saves it: run as g6_review_patch's job runs it
    (``queued``), or as the review alone, as review 1's finding 4 ran it."""
    one = TemplateInstance(id="D1", template="differential.v1", requirement_id="R1", intent="change",
                           bindings={"function": "calc.add", "mode": "outputs", "calls": [{"args": [2, 3]}]})
    instance = one.model_dump(exclude={"provenance"})
    fake_stated(monkeypatch)

    def observed(change, placed, text, node, *, at_base, **kwargs):
        return {**run_result(node + "::test_differential"), "output": "G6-OBSERVATION " + json.dumps({
            "input": {"args": [2, 3], "kwargs": {}}, "output": {"returned": -1 if at_base else 5}})}

    monkeypatch.setattr(vs, "_run_check", observed)
    if queued:
        saved = behaviour.reviewed(review_reply.review, str(source), task, requirements=REQUIREMENTS,
                                   template_instances=[instance])
    else:
        saved = patch_review.review_patch(source, task=task, requirements=REQUIREMENTS, template_instances=[instance])
    assert any(a.get("result") == "behaviour_changed" for a in saved["assessment"]["completion"]["activities"])
    job = uuid4().hex
    assert review_reply.save(saved, job)
    return job, instance


def test_finding_4_a_saved_review_of_another_repository_and_task_supplies_nothing(tmp_path, monkeypatch):
    source_parent = tmp_path / "source"
    source_parent.mkdir()
    source = make_repo(source_parent)
    target = make_repo(tmp_path)
    (target / "calc.py").write_text("def add(a, b):\n    return 900\n", encoding="utf-8")
    job, instance = _saved_review(monkeypatch, source, "Another team's completely different task.", queued=False)
    dialog = Dialog()
    reply = ask(target, data=[], dialog=dialog, review_job=job, template_instances=[instance])
    assert not dialog.messages, "foreign saved review was presented as G6's observations on this task"
    assert reply.get("status") != "confirmed" and reply["warning_card"]["code"] == "G6_E_QUESTION_REVIEW_NOT_BOUND"
    # and bound, as g6_review_patch's job binds it, to its own task and repository: still nothing here
    job, instance = _saved_review(monkeypatch, source, "Another team's completely different task.")
    reply = ask(target, data=[], dialog=dialog, review_job=job, template_instances=[instance])
    assert not dialog.messages and reply["warning_card"]["code"] == "G6_E_QUESTION_REVIEW_NOT_BOUND"


@pytest.mark.parametrize("change", ["none", "tree_changed", "unbound"])
def test_a_saved_review_is_taken_only_bound_to_this_task_and_tree(tmp_path, monkeypatch, change):
    root = make_repo(tmp_path)
    job, instance = _saved_review(monkeypatch, root, TASK)
    if change == "tree_changed":   # the working tree is not the one the review observed
        (root / "calc.py").write_text(GOOD + "\n# later\n", encoding="utf-8")
    elif change == "unbound":   # a reply saved without its binding (as before this change)
        from g6.core.storage import state_path

        state_path("reviews", f"{job}.binding.json").unlink()
    dialog = Dialog()
    reply = ask(root, data=[], dialog=dialog, review_job=job, template_instances=[instance])
    if change == "none":
        assert not dialog.messages and reply["warning_card"]["code"] == bq.PROBE_UNAVAILABLE
    else:
        assert not dialog.messages and reply["warning_card"]["code"] == behaviour.NOT_THIS_REVIEW


# --- finding 8: the issuer counts only runs on the artefact's bytes -------------------------------------------------


def test_finding_8_a_run_on_a_copy_that_is_not_the_artefact_is_refused_and_counts_for_nothing(tmp_path, monkeypatch):
    root = make_repo(tmp_path, "from calc import add\n\ndef test_add():\n    assert isinstance(add(2, 3), int)\n")
    bad_patch = BAD + "\n# unrelated patch\n"
    (root / "calc.py").write_text(bad_patch, encoding="utf-8")
    fake_stated(monkeypatch, at_base="passed")
    observed = []

    def run_trusted_fixture(copy, ids, **kwargs):
        text = (copy / "calc.py").read_text()
        assert text in (BAD, GOOD)
        module: dict = {}
        exec(compile(text, "<trusted fixture>", "exec"), module)  # noqa: S102 - the fixture's own code
        check: dict = {}
        exec(compile((copy / ids[0]).read_text(), "<G6 compiled check>", "exec"), check)  # noqa: S102
        load = check["_load"]
        check["_load"] = lambda spec: load(spec) if spec[0] == "builtin" else module[spec[2]]
        try:
            check["test_example"]()
            status = "passed"
        except BaseException as exc:
            assert type(exc).__name__ == "Failed"
            status = "failed"
        observed.append((text, status))
        return {**run_result(ids[0] + "::test_example", status),
                "source_before": digest(repo_tests._copy_snapshot(copy))}

    monkeypatch.setattr(repo_tests, "_run", run_trusted_fixture)
    run_check = vs._run_check

    def change_only_while_copying(change, placed, text, node, *, at_base, **kwargs):
        if at_base:
            return run_check(change, placed, text, node, at_base=True, **kwargs)
        (root / "calc.py").write_text(GOOD, encoding="utf-8")
        try:
            return run_check(change, placed, text, node, at_base=False, **kwargs)
        finally:
            (root / "calc.py").write_text(bad_patch, encoding="utf-8")

    monkeypatch.setattr(vs, "_run_check", change_only_while_copying)
    reply = patch_review.review_patch(root, task=TASK, confirmed=standard(), requirements=REQUIREMENTS)
    assert (root / "calc.py").read_text() == bad_patch
    assert reply.get("completion_state") != "blocked-escalated", reply
    [statement] = reply["assessment"]["confirmed_expectations"]["evidence"]["expectations"]
    assert statement["checked"] is False, "issuer supported the bad artifact using a different tree's passing checks"
    assert (GOOD, "passed") not in observed   # nothing ran on the substituted tree
    [found] = reply["assessment"]["confirmed_expectations"]["expectations"]
    assert found["check"]["tree_changed"] == vs.NOT_THE_ARTEFACT and found["check"]["result"] == "not_run"
    assert ["integrity", "repository_changed_during_review"] in [[r["stage"], r["code"]]
                                                                 for r in reply["assessment"]["reasons"]]


def test_the_executor_refuses_a_copy_whose_bytes_are_not_the_snapshot_s(tmp_path, monkeypatch):
    """The copy itself is checked, file by file, for every file the snapshot holds bytes for: a substitution made only
    while the copy is taken (between the two looks at the working tree) is still refused."""
    root = make_repo(tmp_path)
    before = repo_tests.snapshot(root)
    ran = []
    monkeypatch.setattr(repo_tests, "_run", lambda copy, ids, **kwargs: ran.append(copy) or run_result(ids[0]))
    real = repo_tests.repository_copy

    def substituted(repo, **kwargs):
        context = real(repo, **kwargs)

        class Swap:
            def __enter__(self):
                copy = context.__enter__()
                (copy / "calc.py").write_text(BAD, encoding="utf-8")   # only the copy differs
                return copy

            def __exit__(self, *exc):
                return context.__exit__(*exc)

        return Swap()

    monkeypatch.setattr(repo_tests, "repository_copy", substituted)
    change = repo_tests.change(root)
    got = vs._run_check(change, "test_g6_example.py", "def test_x():\n    pass\n", "test_g6_example.py::test_x",
                        at_base=False, timeout_s=60, artefact=before)
    assert got.get(vs.NOT_THE_ARTEFACT_KEY) is True and ran == []
    monkeypatch.setattr(repo_tests, "repository_copy", real)
    assert vs._run_check(change, "test_g6_example.py", "def test_x():\n    pass\n", "test_g6_example.py::test_x",
                         at_base=False, timeout_s=60, artefact=before)["verdict"] == "passed" and len(ran) == 1


def test_results_run_without_the_review_s_snapshot_are_not_counted_by_the_issuer(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    statuses = iter(["passed", "failed", "passed"])
    monkeypatch.setattr(vs, "_run_check", lambda change, placed, source, node, *, at_base, **kwargs: run_result(
        node + "::test_example", next(statuses)))
    held = standard()
    results = confirmed_checks.run(root, repo_tests.change(root), held, deadline=vs._clock() + 120, artifact="a" * 64)
    assert results[0]["result"] == "passed" and results[0]["bound"] is False
    issued = requirement_evidence.confirmed_evidence(held, results, artifact="a" * 64, base_commit="b" * 40,
                                                     environment={"isolation": "fixture"})
    [statement] = issued["expectations"]
    assert statement["checked"] is False and "not bound to the artefact" in statement["not_counted"]
    assert issued["certificate"]["receipts"] == []


# --- finding 9: the standard is read when the queued review executes ------------------------------------------------


class Server:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function
        return register


def test_finding_9_a_queued_review_enforces_the_standard_current_when_it_runs(tmp_path, monkeypatch):
    from g6.mcp import _metaharness_tools
    from g6.metaharness import jobs

    root = make_repo(tmp_path)
    assert ask(root, dialog=Dialog({"choice": "A", "name": "Ada", "further": "one_more"}))["status"] == "confirmed"
    queued = []

    class Queue:
        def start(self, operation, **kwargs):
            queued.append(operation)
            return {"job_id": "fake", "status": "pending"}

    monkeypatch.setattr(jobs, "Jobs", Queue)
    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    received = []
    monkeypatch.setattr(review_reply, "review", lambda repo, **kwargs: received.append(kwargs) or {"ok": True})
    server = Server()
    _metaharness_tools(server, object(), object)
    server.tools["g6_review_patch"](str(root), TASK)
    assert len(queued) == 1
    (root / "calc.py").write_text((root / "calc.py").read_text() + "\n", encoding="utf-8")
    assert ask(root, dialog=Dialog({"choice": "B", "name": "Ada"}))["status"] == "confirmed"
    current = behaviour.enforced(str(root), TASK)
    assert current.expectations[0].option == "B" and current.supersedes
    queued.pop()()
    used = received[0]["confirmed"]
    assert used.sha256 == current.sha256, "the queued review enforced the superseded A standard"


def test_the_issuer_s_statement_names_the_standard_it_used(tmp_path):
    held = standard()
    issued = requirement_evidence.confirmed_evidence(held, [{"bound": True, "runs": {}, "result": "not_run"}],
                                                     artifact="a" * 64, base_commit="b" * 40, environment={})
    assert issued["standard_sha256"] == held.sha256 and issued["standard_revision"] == held.revision


# --- finding 11: the entry point is the repository's file, by the loader's own rule ---------------------------------


def test_finding_11_a_standard_library_homonym_is_never_run_as_the_repository_s_entry_point(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    source = "def dumps(value):\n    return 'bad'\n"
    (root / "json.py").write_text(source, encoding="utf-8")
    item = candidate(function="json.dumps", args=[{}], outcomes={"A": {"returns": "{}"}, "B": {"returns": "bad"}},
                     evidence=["json.py: dumps is the function under review"])
    reply = ask(root, data=[item])
    assert reply["status"] == "confirmed"
    held = behaviour.enforced(str(root), TASK)
    loaded = []

    def worker(change, placed, text, node, **kwargs):
        # the worker's bootstrap imports json before the repository joins the path: that cache, then the actual
        # compiled check, its loader not replaced
        namespace: dict = {}
        exec(compile(text, "<G6 compiled check>", "exec"), namespace)  # noqa: S102 - G6's own fixed text
        target = namespace["_load"](namespace["BINDINGS"]["function"])
        loaded.append(target)
        try:
            namespace["test_example"]()
            status = "passed"
        except BaseException as exc:
            assert type(exc).__name__ == "Failed"
            status = "failed"
        return {**run_result(node + "::test_example", status), "loaded_outside": {}, "loaded_complete": True}

    monkeypatch.setattr(vs, "_run_check", worker)
    result = confirmed_checks.run(root, repo_tests.change(root), held, deadline=vs._clock() + 120, artifact="a" * 64)
    assert result[0]["result"] != "passed", "the compiled check passed by calling stdlib json.dumps"
    assert loaded == []   # the loader refused the module: nothing of it was used


def _example_check(tmp_path: Path, function: str, text: str, module_file: str) -> dict:
    from g6.harness import verification_templates as vt

    tree = vt.Tree(tmp_path, SimpleNamespace(base_commit="fixture"), frozenset({module_file}), frozenset(),
                   _texts={(module_file, vt.PATCH): text})
    instance = TemplateInstance(id="E1", template="example.v1", requirement_id="R1", intent="change",
                                bindings={"function": function, "cases": [{"args": [{}], "returns": "{}"}]})
    definition = vt.TEMPLATES["example.v1"]
    applied = definition.applies(tree, definition.bindings.model_validate(instance.bindings), vt.PATCH)
    found: dict = {}
    exec(compile(vt.compile_text(definition, applied.data), "<G6 fixed template>", "exec"), found)  # noqa: S102
    return found


@pytest.mark.parametrize("where", ["standard_library", "loaded_before", "installed_elsewhere", "the_tree"])
def test_the_loader_takes_a_module_only_from_the_tree_s_file(tmp_path, monkeypatch, where):
    """The guard is the compiled loader's own: whatever the import returns (a cached module, the standard library,
    another copy on the path), it must be the file G6 resolved the name to."""
    top = tmp_path / "top"
    top.mkdir()
    monkeypatch.chdir(top)
    name = {"standard_library": "json", "loaded_before": "g6_fixture_cached", "installed_elsewhere": "g6_fixture_far",
            "the_tree": "g6_fixture_here"}[where]
    (top / f"{name}.py").write_text("def dumps(value):\n    return '{}'\n", encoding="utf-8")
    if where == "loaded_before":
        cached = SimpleNamespace(__file__=str(tmp_path / "elsewhere" / f"{name}.py"), dumps=lambda value: "{}")
        monkeypatch.setitem(sys.modules, name, cached)
    elif where == "installed_elsewhere":
        far = tmp_path / "site"
        far.mkdir()
        (far / f"{name}.py").write_text("def dumps(value):\n    return '{}'\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(far))
    elif where == "the_tree":
        monkeypatch.syspath_prepend(str(top))
    check = _example_check(top, f"{name}.dumps", "def dumps(value):\n    return '{}'\n", f"{name}.py")
    assert check["BINDINGS"]["function"] == ["module", name, "dumps", f"{name}.py"]
    try:
        if where == "the_tree":
            check["test_example"]()   # the tree's own file: it runs, and passes
            return
        with pytest.raises(check["_Elsewhere"]):
            check["_load"](check["BINDINGS"]["function"])
        with pytest.raises(pytest.skip.Exception):   # the check: no result, never a pass or a failure
            check["test_example"]()
    finally:
        if where in ("installed_elsewhere", "the_tree"):   # the fixture's own module, imported here
            sys.modules.pop(name, None)


# --- finding 5: every run of the confirmed case on the patch reaches the outcome -------------------------------------


def _replayed(tmp_path, monkeypatch, statuses):
    root = make_repo(tmp_path)
    fake_stated(monkeypatch)
    left, seen = iter(statuses), []

    def compiled(change, placed, source, node, *, at_base, **kwargs):
        status = next(left)
        seen.append((at_base, status))
        return run_result(node + "::test_example", status)

    monkeypatch.setattr(vs, "_run_check", compiled)
    return patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=standard()), seen


def test_finding_5_a_failure_on_the_patch_s_replay_decides_a_defect(tmp_path, monkeypatch):
    reply, seen = _replayed(tmp_path, monkeypatch, ["passed", "failed", "failed"])
    assert seen == [(False, "passed"), (True, "failed"), (False, "failed")]
    assessment = reply["assessment"]
    [found] = assessment["confirmed_expectations"]["expectations"]
    assert found["check"]["runs"]["again"]["result"] == "failed"
    assert assessment["outcome"] == patch_review.DEFECT, "confirmed replay failed but review did not detect a defect"
    assert found["check"]["result"] == "failed" and "on_patch" in found["check"]["reproduction"]
    [statement] = assessment["confirmed_expectations"]["evidence"]["expectations"]
    assert statement["checked"] is False


@pytest.mark.parametrize("statuses, outcome", [
    (["passed", "failed", "passed"], patch_review.SUPPORTED),
    (["failed", "failed"], patch_review.DEFECT),
    (["failed", "passed"], patch_review.DEFECT),
    (["passed", "failed", "failed"], patch_review.DEFECT),
    (["passed", "passed"], patch_review.SUPPORTED),
])
def test_every_run_on_the_patch_reaches_the_outcome(tmp_path, monkeypatch, statuses, outcome):
    reply, seen = _replayed(tmp_path, monkeypatch, statuses)
    assert [status for _, status in seen] == statuses
    assert reply["assessment"]["outcome"] == outcome


def test_every_run_result_of_a_confirmed_check_reaches_the_review_s_outcome():
    """Read from the code: every run of the check on the patch (``read(at_base=False)``) is in the list the result is
    decided from (``on_the_patch``), and the result is read from that list alone; the run at the base commit decides
    the control only."""
    tree = ast.parse((ROOT / "src" / "g6" / "harness" / "confirmed_checks.py").read_text(encoding="utf-8"))
    run = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run")
    reads = [n for n in ast.walk(run) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "read"]
    on_patch = [n for n in reads if any(k.arg == "at_base" and isinstance(k.value, ast.Constant)
                                        and k.value.value is False for k in n.keywords)]
    assert len(reads) == 3 and len(on_patch) == 2, [ast.unparse(n) for n in reads]
    assigned = {}
    for node in ast.walk(run):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and node.value in on_patch:
            assigned[node.targets[0].id] = node
    assert set(assigned) == {"on_patch", "again"}
    source = ast.unparse(run)
    assert "on_the_patch = [on_patch]" in source and "on_the_patch.append(again)" in source
    assert "failing = next((found for found in on_the_patch if _failed(found)), None)" in source
    # the outcome reads the result: patch_review turns a failed one into a defect
    review = (ROOT / "src" / "g6" / "harness" / "patch_review.py").read_text(encoding="utf-8")
    assert "if found[\"result\"] == confirmed_checks.FAILED:" in review


# --- the artefact digest reaches the issuer only from the bytes that ran --------------------------------------------


def test_every_place_an_artefact_digest_reaches_the_issuer_comes_from_the_bytes_that_ran():
    """Read from the code. In the confirmed path the issuer's records are built from each result's runs on the patch
    only when the result is bound (its runs were made on copies the executor checked against the review's snapshot);
    every run on the patch passes that snapshot to the executor; the review passes its own snapshot, whose digest is
    the artefact digest. The stated checks' path (``assess``) is the review's in-place runs, each held to the artefact
    digest by its own snapshot (review I4), unchanged here."""
    evidence = ast.parse((ROOT / "src" / "g6" / "harness" / "requirement_evidence.py").read_text(encoding="utf-8"))
    places = {(f.name, ast.unparse(n.func)) for f in ast.walk(evidence) if isinstance(f, ast.FunctionDef)
              for n in ast.walk(f) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") in ("issue_records", "SourceRecord", "Artifact")}
    assert places == {
        # the stated checks' path (slice 2's, unchanged here): the review's runs of its stated checks
        ("records_for", "Artifact"), ("records_for", "SourceRecord"), ("assess", "issue_records"),
        # the confirmed path: records from bound results' runs only
        ("confirmed_evidence", "Artifact"), ("confirmed_evidence", "SourceRecord"),
        ("confirmed_evidence", "issue_records")}, "a new place hands the issuer an artefact digest: bind it"
    confirmed = next(n for n in ast.walk(evidence) if isinstance(n, ast.FunctionDef) and n.name == "confirmed_evidence")
    [gate] = [n.value for n in ast.walk(confirmed) if isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "runs"]
    assert isinstance(gate, ast.IfExp) and ast.unparse(gate.test) == "found.get('bound') is True"
    assert ast.unparse(gate.orelse) == "{}"   # an unbound result's runs: none reach the issuer
    records = [n for n in ast.walk(confirmed) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "SourceRecord"]
    assert len(records) == 1 and "runs[name]" in ast.unparse(records[0])   # each record is one of those runs
    run = next(n for n in ast.walk(ast.parse((ROOT / "src" / "g6" / "harness" / "confirmed_checks.py").read_text(
        encoding="utf-8"))) if isinstance(n, ast.FunctionDef) and n.name == "run")
    [executor] = [n for n in ast.walk(run) if isinstance(n, ast.Call) and ast.unparse(n.func) == "vs._run_check"]
    [spread] = [k.value for k in executor.keywords if k.arg is None and isinstance(k.value, ast.IfExp)]
    assert ast.unparse(spread.test) == "at_base" and ast.unparse(spread.orelse) == "binding"   # every run on the patch
    assert "binding = {'artefact': before} if before is not None else {}" in ast.unparse(run)
    review = (ROOT / "src" / "g6" / "harness" / "patch_review.py").read_text(encoding="utf-8")
    assert "deadline=deadline, artifact=artifact, roots=roots, before=before," in review
    assert "before = repo_tests.snapshot(root)\n        artifact = digest(before)" in review


# --- finding 10: the report keeps the meaning ---------------------------------------------------------------------


@pytest.mark.parametrize("difference", ["kwargs", "returned_type", "args_type"])
def test_finding_10_the_report_tells_apart_confirmed_cases_that_differ(difference):
    [one] = bq.caller_candidates(TASK, [candidate(args=[2], kwargs={"b": 3})])
    held = confirm(one.question("b" * 64), "A", answered_by="person:Ada")
    assessment = {"confirmed_expectations": {"expectations": [{
        "expectation_id": held.expectation_id, "question": held.question.to_dict(),
        "answer": {"option": "A", "answered_by": "person:Ada"}, "check": {"result": "passed"}}]}}
    different = deepcopy(assessment)
    question = different["confirmed_expectations"]["expectations"][0]["question"]
    if difference == "kwargs":
        question["kwargs"] = {"b": 300}
    elif difference == "returned_type":
        question["outcomes"]["A"] = {"returns": "5"}
    else:
        question["args"] = ["2"]
    first, second = patch_report._confirmed(assessment), patch_report._confirmed(different)
    assert first != second, "different confirmed questions and outcomes have the same readable report"
    [[line]] = first
    assert "calc.add(2, b=3)" in line and "returns 5" in line


def test_two_cases_cut_alike_still_read_apart_by_their_question_digest():
    long = "x" * 400
    [one] = bq.caller_candidates(TASK, [candidate(args=[long + "a"])])
    [two] = bq.caller_candidates(TASK, [candidate(args=[long + "b"])])
    lines = []
    for each in (one, two):
        held = confirm(each.question("b" * 64), "A", answered_by="person:Ada")
        lines.append(patch_report._confirmed({"confirmed_expectations": {"expectations": [{
            "expectation_id": held.expectation_id, "question": held.question.to_dict(),
            "answer": {"option": "A", "answered_by": "person:Ada"}, "check": {"result": "passed"}}]}}))
    assert lines[0] != lines[1] and held.question_sha256 in lines[1][0][0]
