# SPDX-License-Identifier: Apache-2.0
"""Repair 1 of claude/wire-intent-fu2 (Codex review 1). A: the verdict scan reads only text outside all CommonMark
code, blocks and inline spans, and prose quoting never changes how a later object is read. B: the rejection gate sits
where checks are about to run, after every source (the proposer included), and reads the typed data G6 holds of a
check it compiled; since repair 3 every other check runs without counting under a rejection, never reading its text.
C: G6 sends through its own form of a caller (the leaf guarded, views of the wrappers) and never changes the caller it
is handed; a cycle is refused before anything unwraps it. FakeLLM and synthetic inputs only."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import semantic
from g6.core.llm import EmptyRetry, FakeLLM, NoModel, OllamaCaller, TimingLLMCaller
from g6.harness import candidate_standard as cs
from g6.harness import completion_loop as loop
from g6.harness import outcome_history as history
from g6.harness import patch_review as pr
from g6.harness import verification_proposer as vp
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt
from g6.harness.confirmed_expectation import ExpectationStandard, Question, confirm
from g6.reasoning.budget import Budget, MeteredCaller

TOKEN = "review-intfu2r1"
PROTOCOL = dict(verdict="defect", rule="R1", why="a regression", test="", error="", read=TOKEN)


@pytest.fixture(autouse=True)
def online_reader(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)


# --- A: the verdict scan ---------------------------------------------------------------------------------------------------

def _report(body):
    with semantic.request(llm=None):
        return loop.critic_report(body, marker=TOKEN)


@pytest.mark.parametrize("quote", ['Context: " ', 'Context: [" ', "Context: ' \" ", 'a "b" c " '])
def test_a_prose_quote_cannot_hide_a_conflicting_json_verdict(quote):
    said = f"READ: {TOKEN}\n{quote}" + json.dumps(PROTOCOL) + "\nVERDICT: none\n"
    assert _report(said)["verdict"] == "unreadable"


def test_control_a_prose_quote_before_an_agreeing_object_reads_it():
    said = f'READ: {TOKEN}\nContext: " ' + json.dumps(PROTOCOL) + "\nVERDICT: defect\n"
    got = _report(said)
    assert (got["verdict"], got["why"]) == ("defect", "a regression"), got


@pytest.mark.parametrize("span", ["`{}`", "``{}``", "` {} `"])
def test_inline_code_cannot_supply_a_verdict(span):
    said = f"READ: {TOKEN}\nVERDICT: none\nExample: " + span.format(json.dumps(PROTOCOL)) + "\n"
    assert _report(said)["verdict"] == "none"


@pytest.mark.parametrize("label", ["`[codex]`", "> quoted `[codex]` label"])
def test_an_inline_progress_label_cannot_discard_an_earlier_verdict(label):
    said = (f"READ: {TOKEN}\nVERDICT: defect\nWHY: a regression\nExample label: {label}\n"
            f"READ: {TOKEN}\nVERDICT: none\n")
    assert _report(said)["verdict"] == "unreadable"


def test_control_a_progress_label_outside_code_still_cuts():
    said = (f"READ: {TOKEN}\nVERDICT: none\n[codex] working\n"
            f"READ: {TOKEN}\nVERDICT: defect\nWHY: a regression\n")
    assert _report(said)["verdict"] == "defect"


# --- B: the rejection gate -------------------------------------------------------------------------------------------------

def standard():
    question = Question(requirement_id="R1", function="calc.add", args=[2, 3],
                        outcomes={"A": {"returns": 5}, "B": {"returns": -1}},
                        scope="this one case: this input, this outcome", cited="Repair addition.",
                        baseline_sha256="b" * 64)
    return ExpectationStandard().with_expectation(
        confirm(question, "A", answered_by="person:Fixture", answered_at="2026-11-19T00:00:00Z"))


def _kept(check):
    kept, skipped = cs.gate({"rejected": cs.rejections(standard())}, [check], "proposed check", "caller")
    assert all(one["reason"] == cs.REJECTED for one in skipped)
    return bool(kept)


def _proposed(body, head="from calc import add\n"):
    return vs.ProposedCheck(id="P1", requirement_id="R1", intent="preserve", file="test_calc.py",
                            source=head + "def test_add():\n" + body, test="test_add")


@pytest.mark.parametrize("body, head", [
    ("    args = (2, 3)\n    assert add(*args) == -1\n", "from calc import add\n"),
    ("    fn = add\n    assert fn(2, 3) == -1\n", "from calc import add\n"),
    ("    a, b = 2, 3\n    assert add(a, b) == -1\n", "from calc import add\n"),
    ("    assert add(**{'a': 2, 'b': 3}) == -1\n", "from calc import add\n"),
    ("    assert list(map(add, [2], [3])) == [-1]\n", "from calc import add\n"),
    ("    assert add(2, 3) == -1\n", "from calc import *\n"),
    ("    assert c.add(2, 3) == -1\n", "import calc as c\n"),
], ids=["starred", "alias", "variables", "double_starred", "passed_as_value", "star_import", "module_alias"])
def test_a_call_of_the_rejected_function_in_any_form_runs_without_counting(body, head):
    # Repair 3: no check's text or observed calls decide; unreadable checks run without counting.
    check = _proposed(body, head)
    kept, skipped, uncounted = cs.screen(cs.rejections(standard()), [check], lambda ident: vs.FREE_FORM)
    assert kept == [check] and not skipped and uncounted == {"P1"}


@pytest.mark.parametrize("body", ["    assert add(2, 3) == 5\n", "    assert add(1, 1) == 2\n",
                                  "    x = 4\n    assert x == 4\n"])
def test_control_readable_calls_of_kept_outcomes_and_other_code_stay(body):
    assert _kept(_proposed(body))


def _compiled(template, data, ident="C1"):
    # repair 2: G6's custody is the data its compiler holds (ExecutableSource), never the text's TEMPLATE
    from g6.harness.behaviour_runner import ExecutableSource

    source = vt.compile_text(vt.REGISTRY[template], data)
    check = vs.ProposedCheck(id=ident, requirement_id="R1", intent="change", file="test_g6_c.py", source=source)
    return check.model_copy(update={"source": ExecutableSource(source, data)})


@pytest.mark.parametrize("template, data, kept", [
    ("example.v1", {"function": ["module", "calc", "add", "calc.py"],
                    "cases": [{"args": [2, 3], "kwargs": {}, "expect": "returns", "value": -1}]}, False),
    ("example.v1", {"function": ["module", "calc", "add", "calc.py"],
                    "cases": [{"args": [2, 3], "kwargs": {}, "expect": "returns", "value": 5}]}, True),
    ("differential.v1", {"function": ["module", "calc", "add"], "mode": "outputs",
                         "calls": [{"args": [2, 3], "kwargs": {}}]}, False),
    ("differential.v1", {"function": ["module", "calc", "add"], "mode": "outputs",
                         "calls": [{"args": [4, 3], "kwargs": {}}]}, True),
], ids=["example_rejected", "example_kept", "differential_rejected_call", "differential_other_call"])
def test_compiled_checks_are_read_by_their_typed_bindings(template, data, kept):
    assert _kept(_compiled(template, data)) is kept


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_retained_compiled_checks_keep_the_rejection_gate(monkeypatch, template):
    data = {"function": ["module", "calc", "add", "calc.py"]}
    if template == "example.v1":
        data["cases"] = [{"args": [2, 3], "kwargs": {}, "expect": "returns", "value": -1}]
    else:
        data.update(mode="outputs", calls=[{"args": [2, 3], "kwargs": {}}])
    source = vt.compile_text(vt.REGISTRY[template], data)
    row = {"modules": [], "finding": "old-R1",
           "check": {"file": "test_g6_retained.py", "source": source,
                     "sha256": hashlib.sha256(source.encode()).hexdigest(), "test": "", "observation": data,
                     "refuted_by_difference": template == "differential.v1"}}
    monkeypatch.setattr(history, "read", lambda root: SimpleNamespace(retained=[row]))
    retained, _, stale = history.retained_checks(SimpleNamespace(repo=Path.cwd(), files=["calc.py"]), "R1")
    assert len(retained) == 1 and not stale
    kept, _ = cs.gate({"rejected": cs.rejections(standard())}, retained, "retained check", "retained")
    assert not kept


class ReachedExecution(BaseException):
    pass


@pytest.mark.parametrize("mode", ["free_form", "templates_first"])
def test_model_checks_are_gated_where_checks_run(monkeypatch, mode):
    root = Path.cwd()
    change = SimpleNamespace(root=root, base="HEAD", base_commit="b" * 40, files=["calc.py"], sources=["calc.py"],
                             submodule_changes=[], submodules=[], unseen=[], outside_links=[])
    repo = pr.repo_tests
    for name, value in (("checked_repo", lambda value: root), ("snapshot", lambda *a, **k: {}),
                        ("change", lambda *a, **k: change), ("test_related", lambda *a, **k: []),
                        ("related_texts", lambda *a, **k: ({}, {})), ("loadable_changes", lambda *a, **k: {}),
                        ("ignored_inputs", lambda *a, **k: SimpleNamespace(loadable=[], other=[], complete=True,
                                                                           named_like_modules=[])),
                        ("derive_tests", lambda *a, **k: ([], 0)), ("diff_text", lambda *a, **k: "")):
        monkeypatch.setattr(repo, name, value)
    for name in ("_import_roots", "_runner_changes", "_test_data", "_patch_imports_missing", "_at_base"):
        monkeypatch.setattr(pr, name, lambda *a, **k: [])
    monkeypatch.setattr(pr, "_check_shas", lambda *a, **k: {})
    monkeypatch.setattr(history, "retained_checks", lambda *a, **k: ([], {}, []))
    monkeypatch.setattr(history, "check_sources", lambda *a, **k: {})
    monkeypatch.setattr(vs, "bind_execution", lambda change, before, **k: before)
    monkeypatch.setattr(vp, "boundary", lambda root: "")
    monkeypatch.setattr(vp, "context", lambda *a, **k: SimpleNamespace(text="fixture", sources=()))
    tree = vt.Tree(root, change, frozenset({"calc.py"}), frozenset({"calc.py"}),
                   _texts={("calc.py", side): "def add(a, b):\n    return a + b\n" for side in (vt.BASE, vt.PATCH)})
    monkeypatch.setattr(vt.Tree, "read", classmethod(lambda cls, *a, **k: tree))

    def reply(system, user):
        stage = json.loads(json.loads(user)["input"])["stage"]
        if stage == "example.v1":
            return json.dumps({"instances": [{"requirement_id": "R1", "intent": "change", "bindings": {
                "function": "calc.add", "cases": [{"args": [2, 3], "returns": -1}]}}]})
        if stage == vp.FREE:
            return json.dumps({"checks": [{"requirement_id": "R1", "intent": "change", "file": "test_add.py",
                                           "source": "from calc import add\ndef test_add():\n"
                                                     "    assert add(2, 3) == -1\n"}]})
        return json.dumps({"questions": []} if stage == vp.SCENARIOS else {"instances": []})

    captured, counted = [], []

    def search(change, checks, where, **kwargs):
        # Repair 3: a free-form check still runs, without finding or learning authority.
        captured.extend(checks)
        counted.extend(one.id for one in checks if not kwargs.get("rejected") or one.id not in kwargs["uncounted"])
        return vs.Search()

    from g6.harness import confirmed_checks

    def done(*args, **kwargs):
        raise ReachedExecution()

    monkeypatch.setattr(confirmed_checks, "run", lambda *a, **k: [])
    monkeypatch.setattr(pr, "_run_again", done)
    monkeypatch.setattr(vs, "search", search)
    llm = FakeLLM(reply)
    with semantic.request(llm=None), pytest.raises(ReachedExecution):
        pr.review_patch(root, task="Repair addition.", requirements=[{"id": "R1", "text": "Repair addition.",
                                                                       "checks": []}],
                        confirmed=standard(), search={"propose": True, "proposer": mode}, llm=llm)
    assert llm.calls and not counted, counted   # a compiled check reaching the runs would be counted


# --- C: callers ------------------------------------------------------------------------------------------------------------

def _proposer(caller, **extra):
    return vp.Proposer(Path.cwd(), SimpleNamespace(), [], vs.search_config({"propose": True}), llm=caller,
                       deadline=time.monotonic() + 100, room=20, **extra)


@pytest.mark.parametrize("skip", ["fixture skip", ""])
def test_the_proposer_never_rewrites_the_callers_backend(monkeypatch, skip):
    monkeypatch.setattr(vp, "boundary", lambda root: "fixture: no boundary")
    leaf = OllamaCaller(model="fixture", base_url="http://127.0.0.1:1")
    caller = TimingLLMCaller(leaf, lambda seconds: None)
    state = dict(vars(leaf))
    _proposer(caller, skip=skip)
    assert caller._inner is leaf and type(leaf) is OllamaCaller and vars(leaf) == state


def test_an_emptyretry_cycle_is_refused_before_anything_unwraps_it():
    caller = EmptyRetry(NoModel())
    caller._inner = caller
    finished, results = threading.Event(), []

    def build():
        try:
            results.append(_proposer(caller))
        except Exception as exc:
            results.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=build, daemon=True)
    worker.start()
    try:
        returned = finished.wait(2)
    finally:
        caller._inner = NoModel()   # releases a walk that never ends, so the test leaves no spinning worker
        worker.join(timeout=2)
    assert returned and results[0].ready == semantic.FALLBACK_UNSAFE


def test_the_semantic_reader_sends_through_its_own_form_and_never_rewrites_the_caller(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy",
                 "no_proxy", "REQUEST_METHOD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(OllamaCaller, "complete", lambda self, system, user, **kwargs: '{"value":true}')
    leaf = OllamaCaller(model="fixture", base_url="http://127.0.0.1:1")
    caller = TimingLLMCaller(leaf, lambda seconds: None)
    with semantic.request(llm=caller):
        got = semantic.decide("refusal", "synthetic fixture", False)
    assert got.value is True and caller._inner is leaf and type(leaf) is OllamaCaller


def test_a_meter_the_caller_supplies_still_counts_through_g6s_form():
    backend = FakeLLM(default='{"value":true}')
    meter = MeteredCaller(backend, Budget(max_model_calls=1))
    with semantic.request(llm=meter, budget=8):
        semantic.decide("refusal", "first distinct text", False)
        semantic.decide("refusal", "second distinct text", False)
    assert len(backend.calls) == 1 and meter.inner is backend
