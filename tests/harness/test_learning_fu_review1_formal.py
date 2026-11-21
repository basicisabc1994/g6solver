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


def test_retained_formal_check_does_not_invent_a_base_replay(monkeypatch):
    memory_stores(monkeypatch)
    instance = vt.TemplateInstance(
        id="wallet-safety", template="state_invariant.v1",
        requirement_id="R", intent="preserve",
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
    data = {
        "constructor": {"call": ["module", "wallet", "new_wallet"], "args": [], "kwargs": {}},
        "actions": {"1": {"call": ["module", "wallet", "withdraw"], "args": [1], "kwargs": {}}},
        "observe": {"bal": {"call": ["module", "wallet", "balance"], "args": [], "kwargs": {},
                            "refusal": None, "error": None}},
        "sorts": {"bal": "Int"}, "invariants": {"safe": [">=", "bal", 0]},
        "traces": [{"actions": [1], "ghost": {}}],
    }
    [instance] = vt.template_instances([instance], {"R"})
    one, placed, origin = vt.compiled_check(
        Path.cwd(), instance, data, [], keep_observations=True,
        judge=functools.partial(vf._judge, 1),
    )
    change = SimpleNamespace(repo=Path.cwd(), files=[], sources=(), base_commit="b" * 40)
    mode = {"base": "holds"}

    def run(change, placed, source, node, *, at_base, **kwargs):
        wallet = types.ModuleType("wallet")
        wallet.new_wallet = lambda: {"bal": 0}
        wallet.balance = lambda state: state["bal"]
        def withdraw(state, amount):
            if at_base and mode["base"] == "not_replayable":
                raise ValueError("this operation is no longer applicable")
            state["bal"] = 0 if at_base else state["bal"] - amount
        wallet.withdraw = withdraw
        output = io.StringIO()
        namespace = {}
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
        artifact = digest({"source": source, "base_mode": mode["base"], "at_base": at_base})
        ran["source_before"] = artifact
        binding = repo_tests._learning_binding(
            ran, [node], repository=h.repository_id(change.repo),
            check_digests={placed: hashlib.sha256(source.encode()).hexdigest()},
        )
        body = {
            "schema": receipt_store.SCHEMA,
            "source_before": artifact, "source_after": artifact,
            "tests": {"requested": [node], "collected": [node]},
            "result": ran, **binding,
        }
        ran["receipt"] = receipt_store._record(body)
        return ran

    monkeypatch.setattr(vs, "_run_check", run)
    first = vs.search(change, [one], {one.id: placed}, deadline=vs._clock() + 60,
                      artifact="a" * 64, origins={one.id: origin})
    assert first.activities[0]["result"] == "counterexample", first.activities
    for check, node, patch, base, classification, label, differs in first.outcomes:
        h.record_check(change, check, node, patch, base, classification,
                       origin=label, refuted_by_difference=differs)
    assert len(h.read(change.repo).retained) == 1

    mode["base"] = "not_replayable"
    change.base_commit = "d" * 40
    reference = vs.search(change, [one], {one.id: placed}, deadline=vs._clock() + 60,
                          artifact="a" * 64, origins={one.id: origin})
    assert reference.activities[0]["result"] == "check_error", reference.activities
    retained, origins, skipped = h.retained_checks(change, "R")
    assert len(retained) == 1 and not skipped
    assert retained[0]._instance == origin.instance
    repeated = vs.search(change, retained, vs.placements(change.repo, retained),
                         deadline=vs._clock() + 60, artifact="a" * 64, origins=origins)
    assessment = assessment_for(repeated.defects)
    assessment["scope"] = {"base_commit": change.base_commit, "import_roots": []}
    calls = resolution.build(assessment, repo=str(change.repo), task="Keep balance")
    assert repeated.activities[0]["result"] == "check_error", {
        "retained_result": repeated.activities[0]["result"],
        "semantic_result": reference.activities[0]["result"],
        "reproduction_calls": len(calls),
    }
    # Repair 1: the retained origin is the original's in every field that judges it, and no settling call is made.
    restored = origins[retained[0].id]
    assert (restored.keep_observations, restored.patch_only) == (origin.keep_observations, origin.patch_only)
    assert (restored.judge.func, restored.judge.args) == (origin.judge.func, origin.judge.args)
    assert calls == []


SOURCE = "def test_kept():\n    assert True\n"


def _retained(monkeypatch, judging):
    row = {"classification": "counterexample", "finding": "R", "modules": [],
           "check": {"file": "test_kept.py", "source": SOURCE, "test": "test_kept", "sha256": digest(SOURCE),
                     "intent": "preserve", "judging": judging}}
    monkeypatch.setattr(h, "read", lambda root: h.History("c" * 64, (row,)))
    return h.retained_checks(SimpleNamespace(repo=Path.cwd(), files=[]), "R")


@pytest.mark.parametrize("judging", [
    {"judge": {"call": "builtins.eval", "args": ["1"]}, "keep_observations": True, "patch_only": False},
    {"judge": {"call": "", "args": []}, "keep_observations": True, "patch_only": False},
    {"judge": {"call": "g6.harness.verification_formal._judge", "args": [{"nested": 1}]},
     "keep_observations": True, "patch_only": False},
    {"judge": None, "keep_observations": "yes", "patch_only": False},
    "not a description",
], ids=["unlisted-judge", "unnamed-judge", "non-plain-arguments", "malformed-flag", "malformed"])
def test_a_retained_check_whose_judge_cannot_be_restored_is_never_run_as_evidence(monkeypatch, judging):
    checks, origins, skipped = _retained(monkeypatch, judging)
    assert checks == [] and origins == {}
    assert [s["result"] for s in skipped] == ["skipped"] and skipped[0]["reason"] == h.UNJUDGED


def test_a_compiled_check_with_an_unnamed_judge_is_described_as_unrestorable(monkeypatch):
    monkeypatch.setattr(vt, "compile_text", lambda definition, data: SOURCE)
    monkeypatch.setattr(vs, "placements", lambda root, checks: {c.id: c.file for c in checks})
    one = vt.TemplateInstance(id="e", template="example.v1", requirement_id="R", intent="preserve", bindings={})
    check, _, _ = vt.compiled_check(Path.cwd(), one, {}, [], judge=lambda intent, patch, base: {})
    assert check._judging["judge"] == {"call": "", "args": []}
    plain, _, _ = vt.compiled_check(Path.cwd(), one, {}, [])
    assert plain._judging is None   # classify's way needs nothing restored
