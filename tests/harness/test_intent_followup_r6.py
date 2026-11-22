# SPDX-License-Identifier: Apache-2.0
"""Repair 2 of claude/wire-intent-fu2 (Codex review 2). A: a verdict marker anywhere outside code counts, mid-line
included; two that disagree, or one G6 cannot read, make the reply unreadable. B: no check's text decides the rejection
gate. Repair 3 supersedes tracing: only readable compiler custody counts under a rejection; all other checks
still run, without findings, questions or learning. C: callers with slots are never viewed or guarded.
FakeLLM and synthetic repositories only; the runner's boundary is a stand-in in these tests."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from g6.core import confine, semantic
from g6.core.consent_callers import CallerWrapper
from g6.core.llm import FakeLLM, LLMError, OllamaCaller, TimingLLMCaller
from g6.core.stop import STOPPED
from g6.harness import behaviour_runner as br
from g6.harness import candidate_standard as cs
from g6.harness import completion_loop as loop
from g6.harness import confirmed_checks
from g6.harness import outcome_history as history
from g6.harness import patch_review as pr
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt
from g6.harness.confirmed_expectation import ExpectationStandard, Question, confirm
from g6.workforce import repo_tests as rt

TOKEN = "review-intfu2r2"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setenv("G6_CLAUDE", r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setenv("G6_CODEX", r"C:\nonexistent\g6-no-host.exe")


# --- A: every verdict marker counts --------------------------------------------------------------------------------------

def _report(body, marker=""):
    with semantic.request(llm=None):
        return loop.critic_report(body, marker=marker)


@pytest.mark.parametrize("body", [
    "READ: token\nVERDICT: none\nSummary: VERDICT: defect\n",
    "READ: token\nVERDICT: none\nVERDICT - defect\n",
    "READ: token\nVERDICT: none\n**Verdict** — defect\n",
    "READ: token\nVERDICT: maybe\n",
    "READ: token\nVERDICT:\n",
    'READ: token\n{"verdict": "none", "rule": "", "why": "", "read": "token"}\nNote: verdict = defect\n',
], ids=["midline", "dash", "bold_em_dash", "unknown_word", "empty", "json_and_midline"])
def test_any_marker_that_disagrees_or_cannot_be_read_makes_the_reply_unreadable(body):
    assert _report(body, marker="token")["verdict"] == "unreadable"


@pytest.mark.parametrize("body, verdict", [
    ("READ: token\nVERDICT: none\n", "none"),
    ("READ: token\nRULE: R1\nSummary: VERDICT: defect\nWHY: a regression\n", "defect"),
    ("READ: token\nVERDICT: none\nExample: `VERDICT: defect`\n", "none"),
    ("READ: token\nVERDICT: defect\nWHY: the earlier verdict was wrong\n", "defect"),
], ids=["line", "midline_alone", "inline_code", "prose_naming_verdict"])
def test_control_one_readable_marker_is_read(body, verdict):
    assert _report(body, marker="token")["verdict"] == verdict


# --- C: callers that keep state in slots --------------------------------------------------------------------------------

class StopGate(CallerWrapper):
    __slots__ = ("stopped",)

    def __init__(self, inner):
        super().__init__(inner)
        self.stopped = True

    def complete(self, system, user, **kwargs):
        if getattr(self, "stopped", False):
            raise LLMError(STOPPED + ": host stopped")
        return self.inner.complete(system, user, **kwargs)


class SlotLeaf(OllamaCaller):
    __slots__ = ("stopped",)


def test_a_wrapper_with_slots_is_not_viewed_and_no_model_is_asked():
    leaf = FakeLLM(default='{"value":true}')
    caller = StopGate(leaf)
    assert semantic.owned(caller) is None
    with semantic.request(llm=caller):
        got = semantic.decide("refusal", "synthetic fixture", False)
    assert got.value is False and not leaf.calls


def test_a_leaf_with_slots_is_not_guarded():
    assert semantic.owned(SlotLeaf(model="fixture", base_url="http://127.0.0.1:1")) is None


def test_control_wrappers_without_slots_are_still_viewed():
    leaf = OllamaCaller(model="fixture", base_url="http://127.0.0.1:1")
    assert semantic.owned(TimingLLMCaller(leaf, lambda seconds: None)) is not None


# --- B: the gate, by what G6 holds and what it observes --------------------------------------------------------------------

def standard(outcomes=None):
    question = Question(requirement_id="R1", function="calc.add", args=[2, 3],
                        outcomes=outcomes or {"A": {"returns": 5}, "B": {"returns": -1}},
                        scope="this one case: this input, this outcome", cited="Repair addition.",
                        baseline_sha256="b" * 64)
    return ExpectationStandard().with_expectation(
        confirm(question, "A", answered_by="person:Fixture", answered_at="2026-11-19T00:00:00Z"))


GONE = cs.rejections(standard())

SOURCES = {
    "template_spoof": ("TEMPLATE = 'example.v1'\nBINDINGS = {'function': ['module', 'unrelated', 'other'], "
                       "'cases': []}\nfrom calc import add\ndef test_add():\n    assert add(2, 3) == -1\n"),
    "local_import_shadow": ("from calc import add\ndef unrelated_helper():\n    from unrelated import add\n"
                            "def test_add():\n    assert add(2, 3) == -1\n"),
    "called_test_helper": ("from calc import add\ndef test_helper():\n    assert add(2, 3) == -1\n"
                           "def test_add():\n    test_helper()\n"),
    "getattr": "import calc\ndef test_add():\n    assert getattr(calc, 'add')(2, 3) == -1\n",
    "helper": ("from calc import add\ndef check(f, *args):\n    assert f(*args) == -1\n"
               "def test_add():\n    check(add, 2, 3)\n"),
}
OTHER_INPUT = "from calc import add\ndef test_add():\n    assert add(1, 1) == 0\n"



def _check(ident, source, test="test_add"):
    return vs.ProposedCheck(id=ident, requirement_id="R1", intent="preserve", file=f"test_{ident}.py",
                            source=source, test=test)


def _origin(ident):
    return vs.FREE_FORM


def test_free_form_checks_whatever_their_text_run_without_counting():
    checks = [_check(name, source) for name, source in SOURCES.items()]
    kept, skipped, uncounted = cs.screen(GONE, checks, _origin)
    assert kept == checks and not skipped and uncounted == set(SOURCES)


def _example(value):
    return {"function": ["module", "calc", "add", "calc.py"],
            "cases": [{"args": [2, 3], "kwargs": {}, "expect": "returns", "value": value}]}


@pytest.mark.parametrize("value, kept", [(-1, False), (5, True)], ids=["rejected", "confirmed"])
def test_a_check_g6_compiled_is_read_by_the_data_g6_holds(value, kept):
    data = _example(value)
    text = vt.compile_text(vt.REGISTRY["example.v1"], data)   # model_copy keeps the custody object, as G6's compiler
    held = _check("C1", text, test="").model_copy(update={"source": br.ExecutableSource(text, data)})
    out, skipped, uncounted = cs.screen(GONE, [held], _origin)
    assert (bool(out), uncounted) == (kept, set())
    assert all(one["reason"] == cs.REJECTED for one in skipped)


def test_a_compiled_check_is_read_by_its_template_instance_and_a_spoofed_one_is_not_counted():
    text = vt.compile_text(vt.REGISTRY["example.v1"], _example(-1))
    compiled, spoof = _check("C1", text, test=""), _check("C2", text, test="")
    [instance] = vt.template_instances([{"id": "C1", "template": "example.v1", "requirement_id": "R1",
                                         "intent": "change", "bindings": {
                                             "function": "calc.add", "cases": [{"args": [2, 3], "returns": -1}]}}],
                                       ["R1"])
    kept, skipped, uncounted = cs.screen(GONE, [compiled, spoof], _origin, [(compiled, instance)])
    assert kept == [spoof] and [one["check"] for one in skipped] == ["C1"] and uncounted == {"C2"}


class ReachedExecution(Exception):
    pass


@pytest.mark.parametrize("case", list(SOURCES))
def test_the_review_nodes_reach_search_without_counting(monkeypatch, case):
    calc = ModuleType("calc")
    calc.add = lambda a, b: a + b
    monkeypatch.setitem(sys.modules, "calc", calc)
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
    monkeypatch.setattr(confirmed_checks, "run", lambda *a, **k: [])
    captured = []

    def search(change, checks, where, **kwargs):
        captured.append((list(checks), kwargs))
        return vs.Search()

    def done(*args, **kwargs):
        raise ReachedExecution()

    monkeypatch.setattr(vs, "search", search)
    monkeypatch.setattr(pr, "_run_again", done)
    with semantic.request(llm=None), pytest.raises(ReachedExecution):
        pr.review_patch(root, task="Repair addition.", requirements=[{"id": "R1", "text": "Repair addition.",
                                                                       "checks": []}],
                        confirmed=standard(), search={"templates": False},
                        proposed_checks=[{"id": "P1", "requirement_id": "R1", "intent": "change",
                                          "file": "test_calc.py", "source": SOURCES[case], "test": "test_add"}])
    [(checks, kwargs)] = captured
    assert [one.id for one in checks] == ["P1"] and kwargs["uncounted"] == {"P1"} and kwargs["rejected"] == GONE


# --- B, in real runs: ordinary execution without finding authority ----------------------------------------------------------------

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}
BASE = "def add(a, b):\n    return a - b\n"
PATCH = "def add(a, b):\n    return a + b\n"


class _Identity:
    def close(self) -> str:
        return ""


class _StandIn:
    """A backend that 'confines' a run by starting it as usual: ordinary execution is under test, not the boundary."""
    name = "fake-boundary"

    def ready(self, python, *, probe=True):
        return ""

    def started(self, python):
        return False

    def prepare(self, confinement, python):
        return _Identity(), ""

    def start(self, argv, identity, *, network=False, **options):
        return subprocess.Popen(argv, **options)


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(confine, "_select", lambda: (_StandIn(), ""))
    monkeypatch.delenv(confine.ALLOW, raising=False)
    monkeypatch.setattr(rt, "interpreter", lambda repo: sys.executable)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))

    def search(sources, base=BASE, patch=PATCH):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text(base, encoding="utf-8")
        for args in (("init", "-q"), ("add", "."), ("commit", "-q", "-m", "base")):
            subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                           check=True, capture_output=True, env=GIT_ENV)
        (repo / "calc.py").write_text(patch, encoding="utf-8")
        change = rt.change(repo, "HEAD")
        checks = [_check(name, source) for name, source in sources.items()]
        done = vs.search(change, checks, vs.placements(repo, checks), deadline=time.monotonic() + 600,
                         artifact="a" * 64, rejected=GONE, uncounted={one.id for one in checks})
        return {one["check"]: one for one in done.activities}, done
    return search


MODULE_CODE = "from calc import add\nassert add(2, 3) == -1\ndef test_add():\n    pass\n"


def test_the_review_nodes_and_every_route_are_not_counted_in_real_runs(runs):
    found, done = runs({**SOURCES, "module_code": MODULE_CODE, "other_input": OTHER_INPUT})
    for name in [*SOURCES, "module_code", "other_input"]:
        assert found[name]["result"] == vs.ASSERTS_REJECTED, (name, found[name])
    assert not done.defects and not done.outcomes and not done.questions


def test_a_free_form_check_of_a_c_function_is_not_counted(runs):
    found, _ = runs({"c_function": "from calc import add\ndef test_add():\n    assert add(2, 3) == -1\n"},
                    base="import operator\nadd = operator.sub\n")
    assert found["c_function"]["result"] == vs.ASSERTS_REJECTED
    assert found["c_function"]["reason"] == cs.UNREADABLE
