# SPDX-License-Identifier: Apache-2.0
"""The class failure_read_as_success, removed at its root for a yes the bridge reads (follow-up review 2's finding 8:
an approval read by truthiness; met before as "a verdict compared with ``is True``"). Every approval, allow or
confirmation ``g6.cognitive_bridge`` reads is ``codes.affirmed(...)``, which is ``True`` and nothing else. This lists,
from the code, every such read: a parameter that carries a person's approval (named ``approv*``, ``confirm*`` or
``consent*``), a call of an admission or authentication hook (``admit*``, ``*authenticat*``), a G6 door reply's ``ok``
and a step's confirmed prerequisite (``identifiable``). It fails on any read that is not the one argument of
``affirmed(...)``, and on any comparison with ``True`` outside ``affirmed``. Each read is then made with ``"false"``,
``1``, ``"yes"``, an object whose ``__bool__`` is true, and ``True``: only ``True`` says yes."""
from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAgent, CognitiveAssuranceOrchestrator, CrossMethodConflictGate, EvaluatedTrial, Hypothesis,
    InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodRouter, MethodVariant, MixedMethodExecutor,
    Observation, Probe, ProfessionalAttestation, ScopedAssertion, StaticProposer, ToolSpec, codes, engine,
)
from g6.core import door

PACKAGE = Path(codes.__file__).resolve().parent
_PARAM = re.compile(r"^(approv|confirm|consent)")
_HOOK = re.compile(r"^admit|authenticat")
_FIELDS = ("ok", "identifiable")

#: Every read of an approval, an allow or a confirmation in the package: (module, class.function, what is read, how).
EXPECTED = sorted([
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_control(fresh(x))", "affirmed"),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_pair(fresh(x))", "affirmed"),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "self.reviewer_authenticator(fresh(attestation))",
     "affirmed"),
    ("engine.py", "._credited", "reply.get('ok')", "affirmed"),
    ("engine.py", "CognitiveAgent._observe", "reply.get('ok')", "affirmed"),
    ("epistemic.py", "CrossMethodConflictGate.reconcile", "self.admit_assertion(fresh(row))", "affirmed"),
    ("metacognition.py", "MetacognitiveMethodSelector.__init__", "admit_trial(fresh(t))", "affirmed"),
    ("mixed_methods.py", "MixedMethodExecutor._run", "reply.get('ok')", "affirmed"),
    ("mixed_methods.py", "MixedMethodExecutor.execute", "(parent or {}).get('result', {}).get('answer', {}).get("
     "'identifiable')", "affirmed"),
    ("mixed_methods.py", "MixedMethodExecutor.execute", "approved", "affirmed"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.execute", "approved", "affirmed"),
])


def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE.glob("*.py"))]


def _enclosing(node, parent):
    functions, cls, up = [], None, parent.get(node)
    while up is not None and cls is None:
        if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(up)
        if isinstance(up, ast.ClassDef) and functions:
            cls = up
        up = parent.get(up)
    return functions, cls


def _is_read(node, functions) -> bool:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and _PARAM.match(node.id):
        params = {a.arg for f in functions for a in [*f.args.posonlyargs, *f.args.args, *f.args.kwonlyargs]}
        return node.id in params
    if isinstance(node, ast.Call):
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if _HOOK.search(callee) and not callee.startswith("_"):
            return True
        return (callee == "get" and isinstance(node.func, ast.Attribute) and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value in _FIELDS)
    return (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and isinstance(node.slice, ast.Constant)
            and node.slice.value in _FIELDS)


def reads():
    """Every read of a yes in the package, with how it is read: ``affirmed`` when it is ``affirmed(<it>)``."""
    found = []
    for module, tree in _modules():
        parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            functions, cls = _enclosing(node, parent)
            if not functions or not _is_read(node, functions):
                continue
            up = parent[node]
            affirmed = (isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id == "affirmed"
                        and up.args == [node] and not up.keywords)
            found.append((module, f"{cls.name if cls else ''}.{functions[0].name}", ast.unparse(node),
                          "affirmed" if affirmed else "UNCHECKED: " + ast.unparse(up)[:120]))
    return found


def test_the_inventory_finds_the_reads():
    where = {(m, w) for m, w, _, _ in reads()}
    for site in [("mixed_methods.py", "MixedMethodExecutor.execute"),
                 ("orchestration.py", "CognitiveAssuranceOrchestrator.execute"),
                 ("epistemic.py", "CrossMethodConflictGate.reconcile")]:
        assert site in where, f"the inventory did not find {site}"


def test_every_yes_is_read_through_affirmed():
    bad = [f"{m}:{w} {expr} -> {use}" for m, w, expr, use in reads() if use != "affirmed"]
    assert bad == [], "a yes read other than through codes.affirmed: " + "; ".join(bad)


def test_every_read_is_listed():
    assert sorted(reads()) == EXPECTED


def test_nothing_is_compared_with_true_outside_affirmed():
    bad = []
    for module, tree in _modules():
        parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]:
            operands = [node.left, *node.comparators]
            if not any(isinstance(o, ast.Constant) and o.value is True for o in operands):
                continue
            functions, _ = _enclosing(node, parent)
            if (module, [f.name for f in functions][:1]) != ("codes.py", ["affirmed"]):
                bad.append(f"{module}:{node.lineno} {ast.unparse(node)}")
    assert bad == [], "compared with True outside codes.affirmed: " + "; ".join(bad)


def test_affirmed_is_true_and_nothing_else():
    assert codes.affirmed(True) is True
    for value in VALUES[:-1]:
        assert codes.affirmed(value) is False, value


# --- Each read, made with each value ----------------------------------------------------------------------------------

class _Truthy:
    def __bool__(self):
        return True

    def __repr__(self):
        return "Truthy()"


VALUES = ["false", 1, "yes", _Truthy(), True]
IDS = ["false-text", "one", "yes", "truthy", "True"]


class _Registry:
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def invoke(self, name, op, params=None, **kwargs):
        self.calls.append((name, op))
        return Success({"completion_state": "qualified-draft", "answer": self.answers.get(op, {"valid": True})})


def _plan():
    return MethodRouter().plan([ClaimIR("c", "claim", ("formal",), {"formal": {"formula": "P"}})])


def _approved_executor(value, f):
    registry = _Registry()
    out = MixedMethodExecutor(registry).execute(_plan(), approved=value)
    return bool(registry.calls) and out["completion_state"] == "qualified-draft"


def _approved_orchestrator(value, f):
    c = f["case3"]
    registry = _Registry()
    # since follow-up 2 of PR #8 an orchestrator runs only a compilation it issued: this one compiles its own
    orch = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=registry)
    compiled = orch.compile(claim=c["compiled"].original_claim, context=c["compiled"].context,
                            standard=c["standard"], variants=[MethodVariant("v", "formal", "SMT", "formal",
                                                                            "check_validity", 1)],
                            variant_models={"v": {"formula": "A", "premises": ["A"]}})
    out = orch.execute(compiled, approved=value)
    return bool(registry.calls) and out["completion_state"] == "qualified-draft"


def _identified(value, f):
    registry = _Registry({"identify": {"identifiable": value}})
    plan = MethodRouter().plan([ClaimIR("c", "effect", ("causal",), {"causal": {
        "graph": "digraph {t -> o}", "treatment": "t", "outcome": "o", "data": [{"t": 1, "o": 2}]}})])
    MixedMethodExecutor(registry).execute(plan, approved=True)
    return ("causal", "estimate_effect") in registry.calls


def _door_ok_executor(value, f):
    executor = MixedMethodExecutor(_Registry())
    real = executor._invoke
    f["monkeypatch"].setattr(executor, "_invoke", lambda *a: {**real(*a), "ok": value})
    return executor.execute(_plan(), approved=True)["steps"][0]["completion_state"] == "qualified-draft"


def _agent(value, f):
    real = door.invoke_checked
    f["monkeypatch"].setattr(door, "invoke_checked", lambda *a, **k: {**real(*a, **k), "ok": value})
    out = CognitiveAgent(StaticProposer([Probe("p", "measure", "read", {"A": True, "B": False}, .8)]), {
        "read": ToolSpec("read", lambda q, a: Observation(True, "source:actual"))}).run(
        "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    return bool(out["history"])


def _credited(value, f):
    try:
        engine._credited({"ok": value, "request_id": "r"}, {"step": 1})
    except ValueError:
        return False
    return True


def _assertion_admitted(value, f):
    row = ScopedAssertion("c", "p", "a" * 64, "supports", "formal", "source:1", "receipt:1")
    return CrossMethodConflictGate(admit_assertion=lambda a: value).reconcile([row])["unadmitted_evidence_refs"] == []


def _trial_admitted(value, f):
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    trial = EvaluatedTrial("t", "case", "cohort", "d", "u", "k", "v", True, 1.0, "trusted://e", "b" * 64,
                           variant_digest=variant.definition_digest)
    return MetacognitiveMethodSelector([trial], admit_trial=lambda t: value).select(
        f["context"], [variant], required_methods=["formal"]).admitted_trials == 1


def _review_authenticated(value, f):
    c = f["case3"]
    evaluator = InProcessAssuranceEvaluator(evaluator_id="outside", reviewer_authenticator=lambda a: value)
    common = dict(standard=replace(c["standard"], risk=2), artifacts=c["artifacts"], verifiers=c["verifiers"],
                  documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    attestation = ProfessionalAttestation("reviewer", "role", evaluator.review_scope(**common), "sig")
    return evaluator.evaluate(attestation=attestation, **common).payload["professional_review"]["authenticated"]


def _gate(hook):
    def run(value, f):
        gate, change, pairs, controls = f["passing_adaptation"]
        setattr(gate, hook, lambda x: value)
        reasons = gate.assess(change, pairs, controls, detected_failures=change.mitigates)["blocked_reasons"]
        return not any("did not authenticate" in reason for reason in reasons)
    return run


#: Each listed read, made with a value: whether that value was taken as yes.
SCENARIOS = {
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_control(fresh(x))"): _gate("admit_control"),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_pair(fresh(x))"): _gate("admit_pair"),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "self.reviewer_authenticator(fresh(attestation))"):
        _review_authenticated,
    ("engine.py", "._credited", "reply.get('ok')"): _credited,
    ("engine.py", "CognitiveAgent._observe", "reply.get('ok')"): _agent,
    ("epistemic.py", "CrossMethodConflictGate.reconcile", "self.admit_assertion(fresh(row))"): _assertion_admitted,
    ("metacognition.py", "MetacognitiveMethodSelector.__init__", "admit_trial(fresh(t))"): _trial_admitted,
    ("mixed_methods.py", "MixedMethodExecutor._run", "reply.get('ok')"): _door_ok_executor,
    ("mixed_methods.py", "MixedMethodExecutor.execute",
     "(parent or {}).get('result', {}).get('answer', {}).get('identifiable')"): _identified,
    ("mixed_methods.py", "MixedMethodExecutor.execute", "approved"): _approved_executor,
    ("orchestration.py", "CognitiveAssuranceOrchestrator.execute", "approved"): _approved_orchestrator,
}


def test_every_listed_read_has_a_scenario():
    assert set(SCENARIOS) == {(m, w, expr) for m, w, expr, _ in EXPECTED}


@pytest.mark.parametrize("value", VALUES, ids=IDS)
@pytest.mark.parametrize("site", sorted(SCENARIOS), ids=lambda s: f"{s[0]}:{s[1]}:{s[2][:30]}")
def test_only_true_is_a_yes(site, value, case3, passing_adaptation, monkeypatch):
    from g6.cognitive_bridge import ProblemSignature

    f = {"case3": case3, "passing_adaptation": passing_adaptation, "monkeypatch": monkeypatch,
         "context": ProblemSignature("d", "u", "k", 1, 2)}
    taken = SCENARIOS[site](value, f)
    assert taken is (value is True), f"{site}: {value!r} was {'taken' if taken else 'refused'} as yes"
