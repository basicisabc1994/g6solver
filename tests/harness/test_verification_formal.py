# SPDX-License-Identifier: Apache-2.0
"""Verification search, part 4: the formal path (``g6.harness.verification_formal``) and ``state_invariant.v1``.

The caller states an abstract state model in model_checking's closed grammar, the operations it abstracts (dotted names
in the tree, called with literal arguments), a constructor for the initial state, and how each state variable the
invariants read is observed on the real state. G6 checks the obligations with ``check_contract`` (vacuous or
conflicting ones are never searched), searches the model (``check_invariant``, or ``prove_invariant``), and compiles
each trace it finds into a replay test from the template's fixed text: the operation calls in order, then the
invariants on the real state. Only a trace that breaks the invariants on the real code is a finding about the code,
and then an unconfirmed one; a model's "holds" leaves the requirement as it was. Every result comes in three parts:
specification, model, correspondence.

Development fixtures only; no model is called. Tests that take ``bwrap_backend`` run real confined runs; the others
replace G6's confined runs with recorded results, or run a compiled replay with plain pytest on G6's own fixtures. The
model checker itself (Z3, on data) runs for real."""
from __future__ import annotations

import ast
import builtins
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

from g6.harness import patch_review, verification_search, verification_templates
from g6.harness.patch_review import review_patch
from g6.invariants import check_envelope
from g6.workforce import repo_tests

pytest.importorskip("z3")

# --- a development fixture: a wallet service; the patch drops the guard on withdrawals ----------------------------------

WALLET_BASE = ("class Wallet:\n    def __init__(self):\n        self.balance = 0\n\n\n"
               "def new_wallet():\n    return Wallet()\n\n\n"
               "def deposit(wallet, amount):\n    wallet.balance += amount\n\n\n"
               "def withdraw(wallet, amount):\n    if amount <= wallet.balance:\n        wallet.balance -= amount\n\n\n"
               "def balance(wallet):\n    return wallet.balance\n")
WALLET_PATCH = WALLET_BASE.replace("    if amount <= wallet.balance:\n        wallet.balance -= amount\n",
                                   "    wallet.balance -= amount\n")
WALLET_TESTS = ("from wallet import balance, deposit, new_wallet\n\n\ndef test_deposit():\n    w = new_wallet()\n"
                "    deposit(w, 5)\n    assert balance(w) == 5\n")
WALLET = {"wallet.py": WALLET_BASE, "test_wallet.py": WALLET_TESTS}
RULE = [{"id": "R1", "text": "a deposit adds to the balance", "checks": ["test_wallet.py::test_deposit"]}]
#: The abstraction: a deposit adds 5, a withdrawal takes 3, whatever the balance (broader than the code may be).
MODEL = {"variables": {"bal": "Int", "act": "Int"}, "init": "(and (= bal 0) (= act 0))",
         "transition": "(or (and (= next_act 1) (= next_bal (+ bal 5))) (and (= next_act 2) (= next_bal (- bal 3))))",
         "invariants": {"never_negative": "(>= bal 0)"}}
BINDINGS = {"constructor": {"call": "wallet.new_wallet"}, "model": MODEL, "action": "act",
            "actions": {"1": {"call": "wallet.deposit", "args": [5]}, "2": {"call": "wallet.withdraw", "args": [3]}},
            "observe": {"bal": {"call": "wallet.balance"}}, "depth": 4, "traces": 6}


def instance(ident="t-wallet", bindings=None, intent="preserve", requirement="R1", **changes):
    return {"id": ident, "template": "state_invariant.v1", "requirement_id": requirement, "intent": intent,
            "bindings": {**(bindings or BINDINGS), **changes}}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, files: dict[str, str], name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    return root


def _patch(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def review(repo, requirements=RULE, **kwargs):
    return review_patch(repo, task="Make the change the requirements state.", requirements=requirements, **kwargs)


def result(passed=(), failed=(), *, output="", seconds=0.25, digest=""):
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    verdict = "failed" if failed else "passed"
    return {"verdict": verdict, "said": "", "passed": len(passed), "failed": len(failed), "not_passed": 0,
            "total": len(outcomes), "deselected": 0, "test_outcomes": outcomes, "collected_ids": sorted(outcomes),
            "omitted_ids": [], "changed": [], "output": output, "seconds": seconds, "python": sys.executable,
            "interpreter_source": "g6", "command": "pytest", "isolation": {"backend": "synthetic-unit-fixture"},
            "failed_ids": list(failed), "setup_errors": {}, "degraded": False, "receipt": "", "rootdir": "",
            **({"source_before": digest} if digest else {}),
            **({"failure_messages": {node: "Failed: trace(s) break the invariant on the real state"
                                     for node in failed}} if failed else {})}


class Tiers:
    """The review's own runs stand in: every in-place run passes what it was asked."""

    def __init__(self, monkeypatch):
        self.calls: list[tuple[str, ...]] = []

        def execute(root, ids, *, timeout_s, change=None, **_):
            self.calls.append(tuple(ids))
            return result(list(ids))

        monkeypatch.setattr(patch_review, "_execute", execute)
        monkeypatch.setattr(patch_review, "_execute_at_base",
                            lambda change, ids, *, timeout_s, **_: {"verdict": "not_run", "said": "not recorded"})


def _traces_of(source: str) -> list[list[int]]:
    """The action sequences a compiled replay holds."""
    [node] = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
              and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["BINDINGS"]]
    return [trace["actions"] for trace in ast.literal_eval(node.value)["traces"]]


class Replays:
    """Stands in for each replay's confined run (``verification_search._run_check``). ``broken(side, actions)`` says
    whether a trace breaks the invariant on that side's real code; the recorded run prints one observation per trace,
    as the compiled replay does, and fails when one breaks."""

    def __init__(self, monkeypatch, broken=lambda side, actions: False, *, error=None):
        self.calls: list[tuple[str, list[list[int]]]] = []

        def run_check(change, placed, source, node, *, at_base, timeout_s, **_):
            side = "base" if at_base else "patch"
            traces = _traces_of(source)
            self.calls.append((side, traces))
            if error and side in error:
                return {"verdict": "collection_error", "said": "could not be collected", "output": "",
                        "seconds": 0.1, "isolation": {"backend": "synthetic-unit-fixture"}}
            outcomes = [{"outcome": "broken" if broken(side, actions) else "holds", "broken":
                         ["never_negative"] if broken(side, actions) else [], "observed": {"bal": 0}}
                        for actions in traces]
            lines = "".join(f"G6-OBSERVATION {json.dumps({'input': i, 'output': o}, sort_keys=True)}\n"
                            for i, o in enumerate(outcomes))
            bad = [i for i, o in enumerate(outcomes) if o["outcome"] == "broken"]
            return result(failed=[node], output=lines, digest=side[0] * 64) if bad else result(
                [node], output=lines, digest=side[0] * 64)

        monkeypatch.setattr(verification_search, "_run_check", run_check)


def _overdraws(actions) -> bool:
    """Whether a sequence of deposits (1: +5) and withdrawals (2: -3) ever takes the balance below 0."""
    total = 0
    for action in actions:
        total += 5 if action == 1 else -3
        if total < 0:
            return True
    return False


def _codes(got) -> set[str]:
    return {reason["code"] for reason in got["assessment"]["reasons"]}


def _requirement(got, ident) -> dict:
    return next(r for r in got["assessment"]["requirements"] if r["id"] == ident)


def _activities(got, kind=None) -> list[dict]:
    return [a for a in got["assessment"]["completion"]["activities"] if kind is None or a["activity"] == kind]


def _instance(got, ident="t-wallet") -> dict:
    return next(a for a in got["assessment"]["completion"]["activities"] if a.get("check") == ident)


def _refused(got, code="G6_E_INPUT_VALIDATION") -> None:
    assert got["ok"] is False and got["completion_state"] == "blocked-escalated", got
    assert got["warning_card"]["code"] == code and check_envelope(got) == []
    assert "assessment" not in got


THREE = {"specification", "model", "correspondence"}


# --- the registry -------------------------------------------------------------------------------------------------------

def test_state_invariant_is_the_sixth_template_a_safety_and_structural_one_with_a_fixed_replay_text():
    definition = verification_templates.REGISTRY["state_invariant.v1"]
    assert definition.family == "safety_structural" and definition.formal is True
    assert definition.text.count(verification_templates.SLOT) == 1 and "TEMPLATE = 'state_invariant.v1'" in \
        definition.text
    # kept apart from the five whose checks compile from an instance's bindings alone; a review takes all six
    assert set(verification_templates.FORMAL_TEMPLATES) == {"state_invariant.v1"}
    assert "state_invariant.v1" not in verification_templates.TEMPLATES and len(verification_templates.REGISTRY) == 6


# --- refusals before anything runs --------------------------------------------------------------------------------------

@pytest.mark.parametrize("changes, said", [
    ({"model": {**MODEL, "init": "(check-sat)"}}, "model"),
    ({"model": {**MODEL, "init": "(set-option :regular-output-channel \"x\") (= bal 0)"}}, "model"),
    ({"model": {**MODEL, "transition": "import os; os.system('x')"}}, "model"),
    ({"model": {**MODEL, "invariants": {"never_negative": "(>= bal 0) ; comment"}}}, "model"),
    ({"model": {**MODEL, "invariants": {"bad name; x": "(>= bal 0)"}}}, "model"),
    ({"model": {**MODEL, "extra": "x"}}, "model"),
    ({"model": {**MODEL, "variables": {"bal": "Int", "act": "Int", "g6_step": "Int"}}}, "g6_"),
    ({"constructor": {"call": "wallet.new_wallet()"}}, "call"),
    ({"actions": {"1": {"call": "wallet.deposit; import os", "args": [5]}}}, "call"),
    ({"actions": {"one": {"call": "wallet.deposit", "args": [5]}}}, "actions"),
    ({"action": "missing"}, "action"),
    ({"action": "bal", "model": {**MODEL, "variables": {"bal": "Bool", "act": "Int"}}}, "action"),
    ({"observe": {"nowhere": {"call": "wallet.balance"}}}, "observe"),
    ({"observe": {"act": {"call": "wallet.balance"}}}, "observe"),
    ({"observe": {"bal": {"call": "wallet.balance", "refusal": "exception"}}}, "exception"),
    ({"search": "everything"}, "search"),
    ({"actions": {"1": {"call": "wallet.deposit", "args": [(5,)]}}}, "JSON"),
])
def test_a_state_model_or_binding_that_carries_code_or_is_malformed_refuses_the_review(tmp_path, monkeypatch,
                                                                                       changes, said):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    tiers, replays = Tiers(monkeypatch), Replays(monkeypatch)
    got = review(root, template_instances=[instance(**changes)])
    _refused(got)
    assert said in got["warning_card"]["message"], got["warning_card"]["message"]
    assert tiers.calls == [] and replays.calls == []


# --- applicability: refused for that instance, before anything runs ------------------------------------------------------

@pytest.mark.parametrize("changes, said", [
    ({"model": {**MODEL, "variables": {**{f"v{i}": "Int" for i in range(64)}, "bal": "Int", "act": "Int"}}},
     "limit"),
    ({"depth": 13}, "depth"),
    ({"traces": 17}, "traces"),
    ({"model": {**MODEL, "variables": {**{f"v{i}": "Int" for i in range(58)}, "bal": "Int", "act": "Int"}}},
     "history"),
    ({"constructor": {"call": "wallet.make_wallet"}}, "no top-level"),
    ({"actions": {"1": {"call": "wallet.deposit", "args": [5, 6]}}}, "arguments"),
    ({"observe": {"bal": {"call": "nomodule.balance"}}}, "no module"),
    ({"model": {**MODEL, "invariants": {"model_only": "(>= act 0)"}}}, "observ"),
    ({"model": {**MODEL, "invariants": {"quantified": "(forall ((y Int)) (>= bal (- y y)))"}}}, "evaluate"),
    ({"model": {**MODEL, "variables": {"bal": "Real", "act": "Int"}}}, "Int or Bool"),
])
def test_a_state_invariant_that_does_not_apply_or_is_over_the_limits_is_refused_and_reported(tmp_path, monkeypatch,
                                                                                            changes, said):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    replays = Replays(monkeypatch)
    got = review(root, template_instances=[instance(**changes)])
    assert got["ok"] is True and got["assessment"]["outcome"] == "supported_within_scope", got
    found = _instance(got)
    assert found["result"] == "refused" and said in found["reason"], found
    assert replays.calls == [] and not _activities(got, "model check")


# --- the obligations first: vacuous or conflicting ones are reported and never searched ----------------------------------

@pytest.mark.parametrize("invariants, word", [
    ({"always": "(or (>= bal 0) (< bal 0))"}, "vacuous"),
    ({"positive": "(> bal 0)", "negative": "(< bal 0)"}, "conflicting"),
])
def test_vacuous_or_conflicting_obligations_are_reported_and_never_searched(tmp_path, monkeypatch, invariants, word):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    replays = Replays(monkeypatch)
    got = review(root, template_instances=[instance(model={**MODEL, "invariants": invariants})])
    found = _instance(got)
    assert set(found["formal"]) == THREE
    contract = found["formal"]["specification"]["contract"]
    assert contract[word] is True, contract
    assert found["formal"]["model"]["verdict"] == "not_searched" and word in found["formal"]["model"]["reason"]
    assert found["formal"]["correspondence"]["result"] == "not_searched"
    assert not _activities(got, "model check") and replays.calls == []
    assert [a for a in _activities(got, "contract check") if a["instance"] == "t-wallet"]
    assert got["assessment"]["outcome"] == "supported_within_scope"
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"


# --- a reproduced trace, a trace that does not reproduce, and one the base breaks too ------------------------------------

def test_a_reproduced_trace_is_an_unconfirmed_counterexample_with_all_three_parts(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    replays = Replays(monkeypatch, lambda side, actions: side == "patch" and _overdraws(actions))
    got = review(root, template_instances=[instance()])
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [("evidence", "unconfirmed_counterexample")]
    assert "state_invariant.v1" in assessment["reasons"][0]["detail"]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"   # a compiled check decides no requirement
    found = _instance(got)
    assert found["result"] == "counterexample" and set(found["formal"]) == THREE, found
    specification, model, correspondence = (found["formal"][part] for part in ("specification", "model",
                                                                                "correspondence"))
    assert (specification["template"], specification["bindings_provenance"], specification["model_provenance"]) == (
        "state_invariant.v1", "stated", "stated")
    assert specification["contract"]["vacuous"] is False and specification["contract"]["conflicting"] is False
    assert model["operation"] == "check_invariant" and model["verdict"] == "violated" and model["depth"] == 4
    assert model["source"] == "stated" and model["traces"][0] == [2]
    assert correspondence["result"] == "reproduced" and 0 in correspondence["traces_reproduced"]
    # each model run is an activity, with its verdict, depth and trace, its seconds and no model call
    runs = [a for a in _activities(got, "model check") if a["instance"] == "t-wallet"]
    assert len(runs) == len(model["traces"]) == 6   # the instance's limit of traces, each a model run
    assert all(a["model_calls"] == 0 and isinstance(a["seconds"], float) and a["depth"] == 4 for a in runs)
    assert runs[0]["verdict"] == "violated" and runs[0]["actions"] == [2]
    assert [a["actions"] for a in runs] == model["traces"] and len({tuple(t) for t in model["traces"]}) == 6
    # the replay ran on the patch and at the base commit, as a compiled check
    assert [side for side, _ in replays.calls] == ["patch", "base"]
    [defect] = assessment["completion"]["defects_reproduced"]["proposed_checks"]
    assert defect["check"] == "t-wallet" and defect["result"] == "counterexample"
    assert set(defect["formal"]) == THREE and defect["reproduction"]["command"].startswith("python -m pytest")
    assert "TEMPLATE = 'state_invariant.v1'" in defect["reproduction"]["text"]
    assert got["completion_state"] == "qualified-draft" and check_envelope(got) == []


def test_a_trace_that_does_not_reproduce_is_model_only_and_nobody_is_accused(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_BASE + "\n# a comment\n"})
    Tiers(monkeypatch)
    Replays(monkeypatch)   # the real code keeps the balance at or above zero on every trace
    got = review(root, template_instances=[instance()])
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert got["assessment"]["reasons"] == []
    found = _instance(got)
    assert found["result"] == "not_refuted"
    assert found["formal"]["model"]["verdict"] == "violated"   # the model breaks it: the abstraction is too broad
    correspondence = found["formal"]["correspondence"]
    assert (correspondence["result"], correspondence["reason"]) == ("not_reproduced", "model_only")
    assert "nobody is accused" in correspondence["detail"]
    assert got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"] == []


def test_a_preserve_trace_the_base_commit_breaks_too_accuses_nobody(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    Replays(monkeypatch, lambda side, actions: _overdraws(actions))
    got = review(root, template_instances=[instance()])
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    found = _instance(got)
    assert found["result"] == "fails_on_base_too" and found["formal"]["correspondence"]["result"] == "reproduced"
    [question] = got["assessment"]["completion"]["open_questions"]
    assert question["check"] == "t-wallet" and question["result"] == "fails_on_base_too"


def test_a_change_instance_is_replayed_on_the_patch_only(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    replays = Replays(monkeypatch, lambda side, actions: _overdraws(actions))
    got = review(root, template_instances=[instance(intent="change")])
    assert [side for side, _ in replays.calls] == ["patch"]
    assert _instance(got)["result"] == "counterexample" and "unconfirmed_counterexample" in _codes(got)


def test_a_replay_that_cannot_run_is_not_replayable_and_counted_never_a_finding(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    Replays(monkeypatch, lambda side, actions: True, error=("patch",))
    got = review(root, template_instances=[instance()])
    found = _instance(got)
    assert found["result"] == "check_error" and found["formal"]["correspondence"]["result"] == "not_replayable"
    assert found["formal"]["correspondence"]["reason"]
    assert got["assessment"]["reasons"] == [] and got["assessment"]["outcome"] == "supported_within_scope"


# --- induction: a result about the model only ---------------------------------------------------------------------------

def test_k_inductive_leaves_the_requirement_unchanged_and_is_marked_about_the_model_only(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    replays = Replays(monkeypatch)
    deposits_only = {**MODEL, "transition": "(and (= next_act 1) (= next_bal (+ bal 5)))"}
    plain = review(root)
    got = review(root, template_instances=[instance(model=deposits_only, search="induction")])
    found = _instance(got)
    assert found["formal"]["model"]["operation"] == "prove_invariant"
    assert (found["formal"]["model"]["verdict"], found["formal"]["model"]["k"]) == ("k_inductive", 1)
    assert "about the model only" in found["formal"]["model"]["note"]
    assert found["formal"]["correspondence"]["result"] == "not_replayed" and replays.calls == []
    for key in ("outcome", "reasons", "unknowns"):
        assert got["assessment"][key] == plain["assessment"][key], key
    requirement = _requirement(got, "R1")
    assert requirement["status"] == _requirement(plain, "R1")["status"] == "passed_on_stated_checks"
    assert requirement["formal"] == [{"check": "t-wallet", "verdict": "k_inductive", "note": found["formal"]["model"][
        "note"]}]


def test_a_trace_an_induction_step_found_is_not_a_counterexample_and_is_not_replayed(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    replays = Replays(monkeypatch)
    # never breaks within two steps from the initial state, and is not inductive (an unreachable state can step out)
    slow = {**MODEL, "init": "(and (= bal 10) (= act 0))"}
    got = review(root, template_instances=[instance(model=slow, search="induction", depth=2)])
    model = _instance(got)["formal"]["model"]
    assert model["verdict"] == "not_inductive"
    assert _instance(got)["formal"]["correspondence"]["result"] == "not_replayable" and replays.calls == []
    assert got["assessment"]["reasons"] == []


# --- no compiled or model text runs in G6's process --------------------------------------------------------------------

def _bindings_segment(source: str) -> str:
    [node] = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
              and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["BINDINGS"]]
    return ast.get_source_segment(source, node.value)


def test_no_compiled_or_model_text_runs_in_g6s_process_and_the_replay_holds_caller_text_only_in_its_literal(
        tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    seen = []
    replays = Replays(monkeypatch, lambda side, actions: side == "patch" and _overdraws(actions))
    original = verification_search._run_check

    def recording(change, placed, source, node, **kwargs):
        seen.append(source)
        return original(change, placed, source, node, **kwargs)

    monkeypatch.setattr(verification_search, "_run_check", recording)
    executed = []
    real_exec, real_eval = builtins.exec, builtins.eval
    monkeypatch.setattr(builtins, "exec", lambda code, *a, **k: (executed.append(code), real_exec(code, *a, **k))[1])
    monkeypatch.setattr(builtins, "eval", lambda code, *a, **k: (executed.append(code), real_eval(code, *a, **k))[1])
    got = review(root, template_instances=[instance()])
    assert _instance(got)["result"] == "counterexample"
    assert "wallet" not in sys.modules   # the repository's code was never imported here
    for code in executed:
        text = code if isinstance(code, str) else getattr(code, "co_filename", "")
        assert "BINDINGS" not in str(text) and "wallet" not in str(text), text
    [source] = seen[:1]
    assert replays.calls and source.isascii()
    segment = _bindings_segment(source)
    rest = source.replace(segment, "")
    assert rest == verification_templates.REGISTRY["state_invariant.v1"].text.replace(verification_templates.SLOT,
                                                                                       "")
    names = {t.string for t in tokenize.generate_tokens(io.StringIO(rest).readline) if t.type == tokenize.NAME}
    assert not names & {"wallet", "deposit", "withdraw", "balance", "new_wallet", "bal", "act"}
    data = ast.literal_eval(segment)
    assert data["invariants"] == {"never_negative": [">=", "bal", 0]}


# --- the compiled replay does what it says (plain pytest on G6's own fixture, unconfined) --------------------------------

def _plain_run(tree: Path, placed: str, source: str) -> subprocess.CompletedProcess:
    (tree / placed).parent.mkdir(parents=True, exist_ok=True)
    (tree / placed).write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TMP", "TEMP", "HOME", "USERPROFILE")}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir=.", placed],
                          cwd=tree, env={**env, "PYTHONPATH": str(tree)}, capture_output=True, text=True, timeout=120)


def test_the_compiled_replay_holds_on_the_base_and_breaks_on_the_planted_defect(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    seen = []

    def run_check(change, placed, source, node, *, at_base, timeout_s, **_):
        seen.append((placed, source))
        return result([node])

    monkeypatch.setattr(verification_search, "_run_check", run_check)
    review(root, template_instances=[instance()])
    placed, source = seen[0]
    patched = _plain_run(root, placed, source)
    assert patched.returncode == 1 and "break the invariant" in patched.stdout, patched.stdout + patched.stderr
    lines = [json.loads(line.partition(" ")[2]) for line in patched.stdout.splitlines()
             if line.startswith("G6-OBSERVATION ")]
    assert lines[0] == {"input": 0, "output": {"outcome": "broken", "broken": ["never_negative"],
                                               "observed": {"bal": -3}}}
    base = _patch(_repo(tmp_path, WALLET, "base"), {})
    held = _plain_run(base, placed, source)
    assert held.returncode == 0, held.stdout + held.stderr
    assert all(json.loads(line.partition(" ")[2])["output"]["outcome"] == "holds" for line in held.stdout.splitlines()
               if line.startswith("G6-OBSERVATION "))


# --- access_rule.v1's multi-step form, through the state_invariant path --------------------------------------------------

# A multi-user service: an owner shares a document with a user, and may revoke it. The patch, meant to keep a record of
# who was ever shared a document, checks that record when a user reads: a revoked user can still read.
DOCS_BASE = ("class Forbidden(Exception):\n    pass\n\n\nclass Docs:\n    def __init__(self):\n        self.shared = set()\n"
             "        self.ever = set()\n\n\ndef new_docs():\n    return Docs()\n\n\n"
             "def share(docs, owner, doc, user):\n    docs.shared.add((doc, user))\n    docs.ever.add((doc, user))\n\n\n"
             "def revoke(docs, owner, doc, user):\n    docs.shared.discard((doc, user))\n\n\n"
             "def read(docs, user, doc):\n    if (doc, user) not in docs.shared:\n        raise Forbidden(user)\n"
             "    return 'text'\n")
DOCS_PATCH = DOCS_BASE.replace("    if (doc, user) not in docs.shared:\n", "    if (doc, user) not in docs.ever:\n")
DOCS_TESTS = ("from docs import new_docs, read, share\n\n\ndef test_shared_read():\n    d = new_docs()\n"
              "    share(d, 'ann', 'plan', 'bob')\n    assert read(d, 'bob', 'plan') == 'text'\n")
DOCS = {"docs.py": DOCS_BASE, "test_docs.py": DOCS_TESTS}
DOCS_RULE = [{"id": "R1", "text": "a shared user can read", "checks": ["test_docs.py::test_shared_read"]},
             {"id": "R2", "text": "a revoked user cannot read", "checks": []}]
STEPS = {"constructor": {"call": "docs.new_docs"}, "operation": "docs.read", "args": ["bob", "plan"], "who": "bob",
         "resource": "plan", "refusal": "exception", "exception": "docs.Forbidden",
         "steps": {"share": {"call": "docs.share", "args": ["ann", "plan", "bob"], "effect": "allow"},
                   "revoke": {"call": "docs.revoke", "args": ["ann", "plan", "bob"], "effect": "refuse"}},
         "initially": "refused", "depth": 3, "traces": 12}


def access(ident="t-access", bindings=None, intent="preserve", requirement="R2", **changes):
    return {"id": ident, "template": "access_rule.v1", "requirement_id": requirement, "intent": intent,
            "bindings": {**(bindings or STEPS), **changes}}


def _service(side, actions) -> bool:
    """Whether a trace (1: revoke, 2: share, by the steps' names in order) breaks the rule on that side's real code:
    the base commit reads the current shares, the patch every share ever made."""
    shared = ever = False
    for action in actions:
        shared = action == 2 or (shared and action != 1)
        ever = ever or action == 2
    allowed = ever if side == "patch" else shared
    return allowed != shared


def test_the_multi_step_access_rule_catches_a_read_after_a_revoke_through_the_state_invariant_path(tmp_path,
                                                                                                  monkeypatch):
    root = _patch(_repo(tmp_path, DOCS), {"docs.py": DOCS_PATCH})
    Tiers(monkeypatch)
    replays = Replays(monkeypatch, _service)
    got = review(root, DOCS_RULE, template_instances=[access()])
    found = _instance(got, "t-access")
    assert (found["template"], found["result"]) == ("access_rule.v1", "counterexample"), found
    specification = found["formal"]["specification"]
    assert (specification["template"], specification["form"], specification["through"]) == (
        "access_rule.v1", "multi_step", "state_invariant.v1")
    traces = found["formal"]["model"]["traces"]
    reproduced = [traces[i] for i in found["formal"]["correspondence"]["traces_reproduced"]]
    assert [2, 1] in reproduced   # share, then revoke: the revoked user still reads
    assert all(_service("patch", t) and not _service("base", t) for t in reproduced)
    assert [side for side, _ in replays.calls] == ["patch", "base"]
    assert "unconfirmed_counterexample" in _codes(got) and got["assessment"]["outcome"] == "insufficient_evidence"
    assert _requirement(got, "R2")["status"] == "not_established"   # a compiled check decides no requirement
    # the model G6 built from the steps, and the replay's fixed text, are state_invariant.v1's
    [defect] = got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    assert "TEMPLATE = 'state_invariant.v1'" in defect["reproduction"]["text"]


def test_the_single_step_access_rule_is_unchanged_and_needs_no_model(tmp_path, monkeypatch):
    root = _patch(_repo(tmp_path, DOCS), {"docs.py": DOCS_PATCH})
    Tiers(monkeypatch)
    single = {"id": "t-one", "template": "access_rule.v1", "requirement_id": "R2", "intent": "preserve",
              "bindings": {"operation": "docs.read", "refusal": "exception", "exception": "docs.Forbidden", "cases": [
                  {"who": "bob", "resource": "plan", "args": [{"x": 1}, "bob", "plan"], "expected": "refused"},
                  {"who": "bob", "resource": "plan", "args": [{"x": 1}, "bob", "plan"], "expected": "allowed"}]}}
    seen = []
    monkeypatch.setattr(verification_search, "_run_check", lambda change, placed, source, node, **_: (
        seen.append(source), result([node]))[1])
    got = review(root, DOCS_RULE, template_instances=[single])
    assert seen and all("TEMPLATE = 'access_rule.v1'" in source for source in seen)
    assert not _activities(got, "model check") and "formal" not in _instance(got, "t-one")


@pytest.mark.parametrize("changes, said", [
    ({"steps": {}}, "steps"),
    ({"steps": {"share; x": {"call": "docs.share", "args": [], "effect": "allow"}}}, "steps"),
    ({"steps": {"share": {"call": "docs.share", "args": [], "effect": "grant"}}}, "effect"),
    ({"exception": None}, "exception"),
    ({"initially": "maybe"}, "initially"),
    ({"operation": "docs.read()"}, "operation"),
])
def test_an_invalid_multi_step_access_rule_refuses_the_review(tmp_path, monkeypatch, changes, said):
    root = _patch(_repo(tmp_path, DOCS), {"docs.py": DOCS_PATCH})
    tiers = Tiers(monkeypatch)
    got = review(root, DOCS_RULE, template_instances=[access(**changes)])
    _refused(got)
    assert said in got["warning_card"]["message"], got["warning_card"]["message"]
    assert tiers.calls == []


def test_confined_the_multi_step_access_rule_catches_the_revoked_reader_on_the_real_service(tmp_path, bwrap_backend):
    root = _patch(_repo(tmp_path, DOCS), {"docs.py": DOCS_PATCH})
    got = review(root, DOCS_RULE, template_instances=[access()])
    found = _instance(got, "t-access")
    assert found["result"] == "counterexample", found
    traces = found["formal"]["model"]["traces"]
    assert [2, 1] in [traces[i] for i in found["formal"]["correspondence"]["traces_reproduced"]]
    clean = _patch(_repo(tmp_path, DOCS, "clean"), {"NOTES.txt": "no code changed\n"})
    quiet = review(clean, DOCS_RULE, template_instances=[access()])
    assert _instance(quiet, "t-access")["formal"]["correspondence"]["reason"] == "model_only"


# --- the MCP surface ------------------------------------------------------------------------------------------------------

def test_over_mcp_a_state_invariant_keeps_the_envelope_and_codes(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    import asyncio

    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    Tiers(monkeypatch)
    Replays(monkeypatch, lambda side, actions: side == "patch" and _overdraws(actions))
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def finished(client, arguments) -> dict:
        job = data(await client.call_tool("g6_review_patch", {"repo": str(root), "task": "Keep withdrawals safe.",
                                                              "requirements": RULE, **arguments}))
        for _ in range(400):
            got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
            if got.get("status") in ("completed", "failed"):
                assert got["status"] == "completed", got
                return got["result"]
            await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    async def flow():
        async with Client(server) as client:
            searched = await finished(client, {"template_instances": [instance()]})
            refused = await finished(client, {"template_instances": [instance(
                model={**MODEL, "init": "(check-sat)"})]})
        return searched, refused

    searched, refused = asyncio.run(flow())
    assert searched["completion_state"] == "qualified-draft" and searched["warning_card"]["code"] == "G6_E_PATCH_REVIEW"
    assert check_envelope(searched) == [] and "unconfirmed_counterexample" in _codes(searched)
    assert refused["completion_state"] == "blocked-escalated"
    assert refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


# --- real confined runs (Linux bubblewrap) ------------------------------------------------------------------------------

def test_confined_a_trace_the_patch_breaks_is_reproduced_and_the_base_alone_is_model_only(tmp_path, bwrap_backend):
    root = _patch(_repo(tmp_path, WALLET), {"wallet.py": WALLET_PATCH})
    got = review(root, template_instances=[instance()])
    found = _instance(got)
    assert found["result"] == "counterexample", found
    assert found["formal"]["correspondence"]["result"] == "reproduced"
    assert [(r["stage"], r["code"]) for r in got["assessment"]["reasons"]] == [("evidence", "unconfirmed_counterexample")]
    assert got["artifact_digest"] == patch_review.digest(repo_tests.snapshot(root))
    clean = _patch(_repo(tmp_path, WALLET, "clean"), {"NOTES.txt": "no code changed\n"})
    quiet = review(clean, template_instances=[instance()])
    correspondence = _instance(quiet)["formal"]["correspondence"]
    assert (correspondence["result"], correspondence["reason"]) == ("not_reproduced", "model_only")
    assert quiet["assessment"]["outcome"] == "supported_within_scope", quiet["assessment"]["reasons"]
    assert re.search(r"nobody is accused", correspondence["detail"])
