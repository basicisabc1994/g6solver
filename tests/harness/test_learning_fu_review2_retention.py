"""Review 2 reproductions; durable stores and repository execution stay in memory."""
import functools
import hashlib
import io
import sys
import types
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.harness import outcome_history as h, resolution
from g6.harness import verification_formal as vf
from g6.harness import verification_search as vs, verification_templates as vt
from g6.workforce import receipt_store, repo_tests
from tests.harness.test_learning_review4 import memory_stores, assessment_for
from tests.harness.test_proposed_checks import result


def formal_case(monkeypatch, *, intent="preserve"):
    memory_stores(monkeypatch)
    instance = vt.TemplateInstance(
        id="wallet-safety", template="state_invariant.v1",
        requirement_id="R", intent=intent,
        bindings={
            "model": {
                "variables": {"bal": "Int", "act": "Int"},
                "init": "(and (= bal 0) (= act 0))",
                "transition": "(and (= next_bal (- bal 1)) (= next_act 1))",
                "invariants": {"safe": "(>= bal 0)"},
            },
            "action": "act", "depth": 1,
            "constructor": {"call": "wallet.new_wallet"},
            "actions": {"1": {"call": "wallet.withdraw", "args": [1]}},
            "observe": {"bal": {"call": "wallet.balance"}},
        },
    )
    [instance] = vt.template_instances([instance], {"R"})
    data = {
        "constructor": {"call": ["module", "wallet", "new_wallet"], "args": [], "kwargs": {}},
        "actions": {"1": {"call": ["module", "wallet", "withdraw"], "args": [1], "kwargs": {}}},
        "observe": {"bal": {"call": ["module", "wallet", "balance"], "args": [], "kwargs": {},
                            "refusal": None, "error": None}},
        "sorts": {"bal": "Int"}, "invariants": {"safe": [">=", "bal", 0]},
        "traces": [{"actions": [1], "ghost": {}}],
    }
    check, placed, origin = vt.compiled_check(
        Path.cwd(), instance, data, [], keep_observations=True,
        patch_only=intent == "change", judge=functools.partial(vf._judge, 1),
    )
    change = SimpleNamespace(repo=Path.cwd(), files=[], sources=(), base_commit="b" * 40)
    mode = {"base": "holds", "calls": []}

    def run(change, placed, source, node, *, at_base, **kwargs):
        mode["calls"].append(at_base)
        wallet = types.ModuleType("wallet")
        wallet.new_wallet = lambda: {"bal": 0}
        wallet.balance = lambda state: state["bal"]

        def withdraw(state, amount):
            if at_base and mode["base"] == "not_replayable":
                raise ValueError("operation unavailable at this base")
            state["bal"] = 0 if at_base else state["bal"] - amount

        wallet.withdraw = withdraw
        output, namespace = io.StringIO(), {}
        with monkeypatch.context() as local:
            local.setitem(sys.modules, "wallet", wallet)
            exec(compile(source, placed, "exec"), namespace)
            with redirect_stdout(output):
                try:
                    namespace["test_replay"](SimpleNamespace(disabled=nullcontext))
                except pytest.fail.Exception as exc:
                    ran = result(failed=[node], messages={node: "Failed: " + str(exc)})
                else:
                    ran = result([node])
        ran.update(output=output.getvalue(),
                   probe_observations=repo_tests.probe_observations(output.getvalue()),
                   completed=ran["verdict"] == "passed")
        artifact = digest({"source": source, "base": mode["base"], "at_base": at_base})
        ran["source_before"] = artifact
        binding = repo_tests._learning_binding(
            ran, [node], repository=h.repository_id(change.repo),
            check_digests={placed: hashlib.sha256(source.encode()).hexdigest()},
        )
        ran["receipt"] = receipt_store._record({
            "schema": receipt_store.SCHEMA,
            "source_before": artifact, "source_after": artifact,
            "tests": {"requested": [node], "collected": [node]},
            "result": ran, **binding,
        })
        return ran

    monkeypatch.setattr(vs, "_run_check", run)
    return change, check, placed, origin, mode


def search(change, checks, origins):
    return vs.search(change, checks, vs.placements(change.repo, checks),
                     deadline=vs._clock() + 60, artifact="a" * 64, origins=origins)


def retain(change, found, *, legacy=False):
    for check, node, patch, base, classification, label, differs in found.outcomes:
        if legacy:
            # The pre-repair writer had template-instance custody but no judge descriptor.
            check = SimpleNamespace(**check.model_dump(), _instance=check._instance)
        h.record_check(change, check, node, patch, base, classification,
                       origin=label, refuted_by_difference=differs)


def test_legacy_formal_row_is_restored_exactly_or_skipped(monkeypatch):
    change, check, placed, origin, mode = formal_case(monkeypatch)
    first = search(change, [check], {check.id: origin})
    assert first.activities[0]["result"] == "counterexample"
    retain(change, first, legacy=True)
    assert len(h.read(change.repo).retained) == 1

    mode["base"] = "not_replayable"
    change.base_commit = "d" * 40
    reference = search(change, [check], {check.id: origin})
    assert reference.activities[0]["result"] == "check_error"

    checks, origins, skipped = h.retained_checks(change, "R")
    if not checks:
        assert skipped and skipped[0]["reason"]
        return
    repeated = search(change, checks, origins)
    calls = resolution.build(assessment_for(repeated.defects), repo=str(change.repo), task="Keep balance")
    assert repeated.activities[0]["result"] == "check_error", {
        "retained": repeated.activities[0]["result"],
        "original": reference.activities[0]["result"],
        "resolution_calls": len(calls),
    }


def test_patch_only_formal_finding_is_not_silently_discarded(monkeypatch):
    change, check, placed, origin, mode = formal_case(monkeypatch, intent="change")
    first = search(change, [check], {check.id: origin})
    assert first.activities[0]["result"] == "counterexample"
    assert mode["calls"] == [False]
    assert first.outcomes[0][3] is None
    retain(change, first)
    history = h.read(change.repo)
    assert len(history.retained) == 1, {
        "finding": first.activities[0]["result"],
        "rows": len(history.rows),
        "retained": len(history.retained),
        "rejected": history.rejected,
    }


SOURCE = "def test_kept():\n    assert True\n"


@pytest.mark.parametrize("instance,runs", [
    ({"template": "relation.v1", "requirement_id": "R", "intent": "preserve", "bindings": {}}, True),
    ({"template": "state_invariant.v1", "requirement_id": "R", "intent": "preserve", "bindings": {}}, False),
    ({"template": "no_such_template.v1", "requirement_id": "R", "intent": "preserve", "bindings": {}}, False),
    (None, True),
], ids=["generic-template", "formal-template", "unreadable-instance", "free-form"])
def test_a_row_without_a_judging_descriptor_runs_only_when_its_kind_is_judged_generically(monkeypatch, instance, runs):
    row = {"classification": "counterexample", "finding": "R", "modules": [],
           "check": {"file": "test_kept.py", "source": SOURCE, "test": "test_kept", "sha256": digest(SOURCE),
                     "intent": "preserve", **({"template_instance": instance} if instance else {})}}
    monkeypatch.setattr(h, "read", lambda root: h.History("c" * 64, (row,)))
    checks, origins, skipped = h.retained_checks(SimpleNamespace(repo=Path.cwd(), files=[]), "R")
    assert bool(checks) is runs
    if not runs:
        assert skipped[0]["result"] == "skipped" and skipped[0]["reason"] == h.UNJUDGED


def test_a_patch_only_counterexample_needs_its_own_judge_to_be_admitted_without_a_base(monkeypatch):
    change, check, placed, origin, mode = formal_case(monkeypatch, intent="change")
    first = search(change, [check], {check.id: origin})
    one = first.outcomes[0][0]
    one._judging = {**one._judging, "judge": {"call": "builtins.eval", "args": []}}   # one it cannot restore
    retain(change, first)
    assert not h.read(change.repo).rows
