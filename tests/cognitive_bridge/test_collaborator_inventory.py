# SPDX-License-Identifier: Apache-2.0
"""The check-then-act class, removed at its root for what collaborators return (review 4, findings 2 and 3; met in all
four reviews). This lists, from the code, every call in ``g6.cognitive_bridge`` of code a caller handed in (a
collaborator: what a class keeps by reference, a parameter typed as code, a record's callable field) and what is done
with what it returns. It fails on any use that is not through the one taken-and-checked snapshot
(``snapshot.returned(<call>, <check>, ...)``) or a verdict read as ``True`` and nothing else (``codes.affirmed(<call>)``,
``test_approval_inventory.py``), and on any argument that is not a fresh copy (``snapshot.fresh``) or a constant. Each
call is then made, with a spy that fails on any argument it could change."""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import sys
import types
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import (
    BayesianUpdater, ClaimIR, CognitiveAgent, CognitiveAssuranceOrchestrator, CrossMethodConflictGate, EvaluatedTrial,
    Hypothesis, InformationGainSelector, InProcessAssuranceEvaluator, LLMClaimCompiler,
    LLMIdeaGenerator, LLMMetacognitiveCritic, LLMProposer, MetacognitiveMethodSelector, MetacognitivePlanner,
    MethodVariant, MLAcquisitionSelector, Observation, Probe, ProblemSignature, ProfessionalAttestation,
    ScopedAssertion, ToolSpec, distill_blackbox,
)
from g6.cognitive_bridge import snapshot as snap
from g6.cognitive_bridge.snapshot import FrozenMap

PACKAGE = Path(snap.__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_snapshot_inventory", Path(__file__).with_name("test_snapshot_inventory.py"))
_stores = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_stores)

#: Every call of a collaborator in the package, with what is done with its answer: (module, class.function, callee,
#: use). Built from the code by the tests below and compared whole, so a new call is listed here with its check.
EXPECTED = sorted([
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_control", "affirmed"),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_pair", "affirmed"),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "self.reviewer_authenticator", "affirmed"),
    ("assurance.py", "._test", "checker.check", "returned(_verdict)"),
    ("critique.py", "LLMMetacognitiveCritic.propose", "self.llm.complete", "returned(json_reply)"),
    ("engine.py", "CognitiveAgent.run", "self.proposer.propose", "returned(_proposed)"),
    ("engine.py", "CognitiveAgent.run", "self.selector.rank", "returned(_selection)"),
    ("engine.py", "CognitiveAgent.run", "self.updater.update", "returned(_posterior)"),
    ("engine.py", "LLMIdeaGenerator.generate", "self.llm.complete", "returned(json_reply)"),
    ("engine.py", "LLMProposer.propose", "self.llm.complete", "returned(json_reply)"),
    ("engine.py", "MLAcquisitionSelector.rank", "self.predictor.predict", "returned(_scores)"),
    ("engine.py", "_ToolDoor.invoke", "self.tool.call", "returned(_observed)"),
    ("epistemic.py", "CrossMethodConflictGate.reconcile", "self.admit_assertion", "affirmed"),
    ("metacognition.py", "MetacognitiveMethodSelector.__init__", "admit_trial", "affirmed"),
    ("metacognition.py", "MetacognitivePlanner.plan", "self.selector.select", "returned(checked_selection)"),
    ("mixed_methods.py", "LLMClaimCompiler.draft", "self.llm.complete", "returned(json_reply)"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess",
     "(epistemic_gate or CrossMethodConflictGate()).reconcile", "returned(_reconciled)"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess", "evaluator.evaluate", "returned(_dossier)"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.compile", "self.selector.select",
     "returned(checked_selection)"),
    ("xai.py", ".distill_blackbox", "blackbox.predict", "returned(_predictions)"),
    ("xai.py", ".distill_blackbox", "blackbox.predict", "returned(_predictions)"),
])

#: Parameters typed as code that are not a caller's code: the functions that walk a value to copy or read it once, and
#: the one place a check is called (each check is the bridge's own function: test_every_check_is_the_bridges_own).
DATA_WALKERS = {
    ("snapshot.py", "_freeze", "value"),             # the snapshot itself: it reads a caller's value once, to copy it
    ("snapshot.py", "returned", "check"),            # calls the bridge's own check on the fresh copy
    ("xai.py", "_native", "value"),                  # reads a numpy scalar's Python value
    ("codes.py", "claims", "value"),                 # walks a snapshot the bridge holds, to find a claim in it
}


def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE.glob("*.py"))]


def _returned_calls():
    return [node for _, tree in _modules() for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "returned"]


def _checks() -> frozenset[str]:
    """The functions the package uses as a ``returned(...)`` check: each one's parameters are the fresh copy and what
    it is checked against, which are data."""
    return frozenset(ast.unparse(node.args[1]) for node in _returned_calls() if len(node.args) >= 2)


def _kept_by_reference() -> frozenset[str]:
    """The attributes a class keeps by reference: every store in an ``__init__`` that is neither a snapshot nor the
    object's own state (``test_snapshot_inventory``'s rule, which names each one as a collaborator)."""
    return frozenset(a for _, _, a, v in _stores.stores() if not (_stores._calls(v, "snapshot") or _stores._own_state(v)))


def _top(node: ast.AST | None) -> set[str]:
    """The type names an annotation is a union of (``X | None``, ``Optional[X]``), each by its outer name."""
    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _top(ast.parse(node.value, mode="eval").body)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _top(node.left) | _top(node.right)
    if isinstance(node, ast.Subscript):
        outer = ast.unparse(node.value).split(".")[-1]
        if outer in ("Optional", "Union"):
            inner = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return set().union(*(_top(item) for item in inner))
        return {outer}
    return {ast.unparse(node).split(".")[-1]}


def _code_types() -> frozenset[str]:
    """The annotations that name code, not data: ``Any``, ``Callable``, and every class in the package that is not a
    snapshot record, a pydantic model, a mapping or the block (its Protocols, selectors, evaluators and gates)."""
    names = {"Any", "Callable"}
    for _, tree in _modules():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            bases = [ast.unparse(b) for b in cls.bases]
            if "record" in [ast.unparse(d) for d in cls.decorator_list] or any(
                    b in ("BaseModel", "dict") or b.startswith("AIBlock") for b in bases):
                continue
            names.add(cls.name)
    return frozenset(names)


def _callable_fields() -> frozenset[str]:
    """The fields of the package's records that hold code: annotated ``Callable`` or a Protocol that is called."""
    called = {cls.name for _, tree in _modules() for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
              and any(isinstance(f, ast.FunctionDef) and f.name == "__call__" for f in cls.body)}
    found = set()
    for _, tree in _modules():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if "record" not in [ast.unparse(d) for d in cls.decorator_list]:
                continue
            for item in cls.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and (
                        _top(item.annotation) & ({"Callable"} | called)):
                    found.add(item.target.id)
    return frozenset(found)


def _parents(tree):
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing(node, parent):
    functions, cls, up = [], None, parent.get(node)
    while up is not None and cls is None:
        if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(up)
        if isinstance(up, ast.ClassDef) and functions:
            cls = up
        up = parent.get(up)
    return functions, cls


def _code_names(module, functions, kept, code_types, checks):
    """The names that hold code in these (nested) functions: parameters typed as code or named as a kept collaborator,
    and locals assigned from them (never from ``returned(...)``, whose answer is a checked snapshot)."""
    names = set()
    for function in functions:
        params = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
        params += [p for p in (function.args.vararg, function.args.kwarg) if p is not None]
        for param in params:
            if (module, function.name, param.arg) in DATA_WALKERS or function.name in checks:
                continue
            if _top(param.annotation) & code_types or param.arg in kept:
                names.add(param.arg)
    changed = True
    while changed:
        changed = False
        for function in functions:
            for node in ast.walk(function):
                if not isinstance(node, ast.Assign) or (
                        isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "returned"):
                    continue
                used = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
                for target in node.targets:
                    for name in [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]:
                        if used & names and name not in names:
                            names.add(name)
                            changed = True
    return names


def _is_self_kept(node, kept):
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"
            and node.attr in kept)


def _root_names(receiver):
    """The names a call's receiver starts from (``a`` in ``a.b[c].d``, ``f`` in ``f(x).y``; every name in an
    expression such as ``(a or B())``)."""
    node = receiver
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return {node.id} if isinstance(node, ast.Name) else {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def sites():
    """Every call of a collaborator: (module, class.function, callee, use, arguments that are not fresh copies)."""
    kept, code_types, fields, checks = _kept_by_reference(), _code_types(), _callable_fields(), _checks()
    found = []
    for module, tree in _modules():
        parent = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            functions, cls = _enclosing(node, parent)
            if not functions:
                continue
            func = node.func
            receiver = func.value if isinstance(func, ast.Attribute) else func
            code = (_is_self_kept(receiver, kept) or _is_self_kept(func, kept)
                    or (isinstance(func, ast.Attribute) and func.attr in fields)
                    or bool(_root_names(receiver) & _code_names(module, functions, kept, code_types, checks)))
            if not code:
                continue
            up = parent[node]
            if (isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id == "returned"
                    and up.args and up.args[0] is node and len(up.args) >= 2):
                use = f"returned({ast.unparse(up.args[1])})"
            elif (isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id == "affirmed"
                  and up.args == [node] and not up.keywords):
                use = "affirmed"
            else:
                use = "UNCHECKED: " + ast.unparse(up)[:120]
            loose = [ast.unparse(a) for a in [*node.args, *(k.value for k in node.keywords)]
                     if not (isinstance(a, ast.Constant) or (isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                                                             and a.func.id == "fresh"))]
            found.append((module, f"{cls.name if cls else ''}.{functions[0].name}", ast.unparse(func), use, loose))
    return found


def test_the_inventory_finds_the_calls():
    callees = {(m, where, callee) for m, where, callee, _, _ in sites()}
    for site in [("engine.py", "CognitiveAgent.run", "self.selector.rank"),
                 ("engine.py", "CognitiveAgent.run", "self.updater.update"),
                 ("engine.py", "CognitiveAgent.run", "self.proposer.propose"),
                 ("xai.py", ".distill_blackbox", "blackbox.predict"),
                 ("assurance.py", "._test", "checker.check")]:
        assert site in callees, f"the inventory did not find {site}"


def test_every_check_is_the_bridges_own_function():
    # A check is named, never a lambda or a collaborator: one of the package's own functions.
    defined = {node.name for _, tree in _modules() for node in tree.body if isinstance(node, ast.FunctionDef)}
    calls = _returned_calls()
    assert calls, "the inventory found no returned(...) call"
    bad = [ast.unparse(node) for node in calls
           if len(node.args) < 2 or not isinstance(node.args[1], ast.Name) or node.args[1].id not in defined]
    assert bad == [], "a returned(...) check that is not one of the package's own functions: " + "; ".join(bad)


def test_no_collaborators_answer_is_used_unchecked():
    bad = [f"{m}:{where} {callee} -> {use}" for m, where, callee, use, _ in sites() if use.startswith("UNCHECKED")]
    assert bad == [], "a collaborator's answer used other than through returned(...) or affirmed(...): " + "; ".join(bad)


def test_every_collaborator_call_is_listed_with_what_is_done_with_its_answer():
    assert sorted((m, where, callee, use) for m, where, callee, use, _ in sites()) == EXPECTED


def test_a_collaborator_is_handed_only_fresh_copies_and_constants():
    bad = [f"{m}:{where} {callee}({', '.join(loose)})" for m, where, callee, _, loose in sites() if loose]
    assert bad == [], "a collaborator handed something other than a fresh copy: " + "; ".join(bad)


def test_a_kept_collaborator_is_handed_on_only_to_the_door_or_the_executor():
    # A collaborator kept by reference is called (above), compared with None, or handed whole to G6's door (as the
    # registry it calls) or to the executor that does that; nowhere else.
    kept, bad = _kept_by_reference(), []
    for module, tree in _modules():
        parent = _parents(tree)
        for node in ast.walk(tree):
            if not (_is_self_kept(node, kept) and isinstance(node.ctx, ast.Load)):
                continue
            up = parent[node]
            if isinstance(up, ast.Attribute) or (isinstance(up, ast.Call) and up.func is node):
                continue  # a call's receiver: listed by sites()
            if isinstance(up, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in up.ops) and all(
                    isinstance(c, ast.Constant) and c.value is None for c in up.comparators):
                continue
            if isinstance(up, ast.keyword) and up.arg == "registry" and ast.unparse(parent[up].func).endswith(
                    "invoke_checked"):
                continue
            if isinstance(up, ast.Call) and ast.unparse(up.func) == "MixedMethodExecutor" and up.args[0] is node:
                continue
            bad.append(f"{module}:{node.lineno} {ast.unparse(up)[:100]}")
    assert bad == [], "a kept collaborator handed on: " + "; ".join(bad)


# --- Each call made, with a spy: what it is handed cannot be changed --------------------------------------------------

def _frozen(value) -> bool:
    """Whether nothing in ``value`` can be written through its own API: plain immutable data, tuples, frozensets,
    read-only maps and snapshot records of those, or code."""
    if value is None or type(value) in (bool, int, float, str, bytes):
        return True
    if type(value) in (tuple, frozenset):
        return all(_frozen(item) for item in value)
    if type(value) is FrozenMap:
        return all(_frozen(k) and _frozen(v) for k, v in value.items())
    if getattr(type(value), "__snapshot_record__", False):
        return all(_frozen(getattr(value, f.name)) for f in dataclasses.fields(value))
    return callable(value) and not isinstance(value, (list, dict, set, bytearray))


def _hypotheses():
    return [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)]


def _probe(**kw):
    return Probe("p", "measure", "read", {"A": True, "B": False}, .8, **kw)


def _tool():
    return ToolSpec("read", lambda q, a: Observation(True, "actual:source"))


def _model(log, reply):
    class Model:
        def complete(self, *args, **kwargs):
            log.append((args, kwargs))
            return reply
    return Model()


def _agent(log, role):
    class Spy:
        def propose(self, *args, **kwargs):
            if role == "propose":
                log.append((args, kwargs))
            self.steps = getattr(self, "steps", 0) + 1
            return [_probe(arguments={"k": [1]})] if self.steps == 1 else []

        def rank(self, *args, **kwargs):
            if role == "rank":
                log.append((args, kwargs))
            return InformationGainSelector().rank(*args, **kwargs)

        def update(self, *args, **kwargs):
            if role == "update":
                log.append((args, kwargs))
            return BayesianUpdater().update(*args, **kwargs)

        def observe(self, *args, **kwargs):
            if role == "observe":
                log.append((args, kwargs))
            return Observation(True, "actual:source")

    spy = Spy()
    CognitiveAgent(spy, {"read": ToolSpec("read", spy.observe)}, selector=spy, updater=spy).run(
        "measure", _hypotheses(), budget=1, max_steps=2)


class _SpySelector(MetacognitiveMethodSelector):
    def __init__(self, log):
        super().__init__()
        self.log = log

    def select(self, *args, **kwargs):
        self.log.append((args, kwargs))
        return super().select(*args, **kwargs)


def _variant():
    return MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)


def _assess(case3, **kw):
    run = case3["orchestrator"].execute(case3["compiled"], approved=True)
    return case3["orchestrator"].assess(
        case3["compiled"], standard=case3["standard"], run_result=run, verifiers=case3["verifiers"],
        artifacts=case3["artifacts"], documentation=case3["documentation"],
        candidate_actor=case3["candidate_actor"], **{"evaluator": case3["evaluator"], **kw})


def _evaluate(log, case3, **kw):
    evaluator = InProcessAssuranceEvaluator(evaluator_id="outside", **kw)
    common = dict(standard=replace(case3["standard"], risk=2), artifacts=case3["artifacts"],
                  verifiers=case3["verifiers"], documentation=case3["documentation"],
                  candidate_actor=case3["candidate_actor"])
    attestation = ProfessionalAttestation("reviewer", "role", evaluator.review_scope(**common), "sig")
    evaluator.evaluate(attestation=attestation, **common)


def _distill(log, monkeypatch):
    tree_module = types.ModuleType("sklearn.tree")

    class Tree:
        def __init__(self, **kwargs):
            pass

        def fit(self, X, y):
            self.n_features_in_, self.n_outputs_, self.classes_ = len(X[0]), 1, [0, 1]
            self.tree_ = SimpleNamespace(children_left=[-1], children_right=[-1], feature=[-2], threshold=[-2.0],
                                         value=[[[1.0, 0.0]]])
            return self

    tree_module.DecisionTreeClassifier = tree_module.DecisionTreeRegressor = Tree
    package = types.ModuleType("sklearn")
    package.tree = tree_module
    monkeypatch.setitem(sys.modules, "sklearn", package)
    monkeypatch.setitem(sys.modules, "sklearn.tree", tree_module)

    class Box:
        def predict(self, *args, **kwargs):
            log.append((args, kwargs))
            return [0] * len(args[0])

    rows = [[float(i), float(i % 3)] for i in range(20)]
    distill_blackbox(Box(), rows[:12], rows[12:], ["x", "y"])


def _claims_reply():
    return ('{"claims": [{"id": "c", "statement": "s", "evidence_needs": ["formal"], '
            '"models": {"formal": {"formula": "A"}}}]}')


def _trial():
    return EvaluatedTrial("t", "case", "cohort", "d", "u", "k", "v", True, 1.0, "trusted://e", "b" * 64,
                          variant_digest=_variant().definition_digest)


def _spy(log, answer=True):
    def spy(*args, **kwargs):
        log.append((args, kwargs))
        return answer
    return spy


SCENARIOS = {
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_control"):
        lambda log, f: _gate(log, f, "admit_control"),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_pair"):
        lambda log, f: _gate(log, f, "admit_pair"),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "self.reviewer_authenticator"):
        lambda log, f: _evaluate(log, f["case3"], reviewer_authenticator=_spy(log)),
    ("assurance.py", "._test", "checker.check"):
        lambda log, f: f["case3"]["evaluator"].evaluate(
            standard=f["case3"]["standard"], artifacts=f["case3"]["artifacts"],
            verifiers={"checker": replace(f["case3"]["verifiers"]["checker"],
                                          check=lambda *a, **k: log.append((a, k)) or a[0] == b"GOOD")},
            documentation=f["case3"]["documentation"], candidate_actor=f["case3"]["candidate_actor"]),
    ("critique.py", "LLMMetacognitiveCritic.propose", "self.llm.complete"):
        lambda log, f: LLMMetacognitiveCritic(_model(log, '{"candidates": []}')).propose("Assess", "A -> A", []),
    ("engine.py", "CognitiveAgent.run", "self.proposer.propose"): lambda log, f: _agent(log, "propose"),
    ("engine.py", "CognitiveAgent.run", "self.selector.rank"): lambda log, f: _agent(log, "rank"),
    ("engine.py", "CognitiveAgent.run", "self.updater.update"): lambda log, f: _agent(log, "update"),
    ("engine.py", "LLMIdeaGenerator.generate", "self.llm.complete"):
        lambda log, f: LLMIdeaGenerator(_model(log, '{"ideas": []}')).generate("goal", []),
    ("engine.py", "LLMProposer.propose", "self.llm.complete"):
        lambda log, f: LLMProposer(_model(log, '{"probes": []}'), {"read": _tool()}).propose("goal", _hypotheses(), []),
    ("engine.py", "MLAcquisitionSelector.rank", "self.predictor.predict"):
        lambda log, f: MLAcquisitionSelector(SimpleNamespace(predict=_scores_spy(log))).rank(
            {"A": .5, "B": .5}, [_probe()], 1),
    ("engine.py", "_ToolDoor.invoke", "self.tool.call"): lambda log, f: _agent(log, "observe"),
    ("epistemic.py", "CrossMethodConflictGate.reconcile", "self.admit_assertion"):
        lambda log, f: CrossMethodConflictGate(admit_assertion=_spy(log)).reconcile(
            [ScopedAssertion("c", "truth", "a" * 64, "supports", "formal", "source1", "sig1")]),
    ("metacognition.py", "MetacognitiveMethodSelector.__init__", "admit_trial"):
        lambda log, f: MetacognitiveMethodSelector([_trial()], admit_trial=_spy(log)),
    ("metacognition.py", "MetacognitivePlanner.plan", "self.selector.select"):
        lambda log, f: MetacognitivePlanner(_SpySelector(log)).plan(
            [ClaimIR("claim", "A entails A", ("formal",), {"formal": {"formula": "A"}})],
            ProblemSignature("review", "logic", "validity", 1, 2), [_variant()], required_methods=("formal",)),
    ("mixed_methods.py", "LLMClaimCompiler.draft", "self.llm.complete"):
        lambda log, f: LLMClaimCompiler(_model(log, _claims_reply())).draft("Does A entail A?"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess",
     "(epistemic_gate or CrossMethodConflictGate()).reconcile"):
        lambda log, f: _assess(f["case3"], epistemic_gate=_SpyGate(log), scoped_assertions=[
            ScopedAssertion("claim", "truth", "a" * 64, "supports", "formal", "source1", "sig1")]),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess", "evaluator.evaluate"):
        lambda log, f: _assess(f["case3"], evaluator=_SpyEvaluator(log, evaluator_id="outside")),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.compile", "self.selector.select"):
        lambda log, f: CognitiveAssuranceOrchestrator(_SpySelector(log)).compile(
            claim=ClaimIR("claim", "A entails A", ("formal",), {}),
            context=ProblemSignature("review", "logic", "validity", 1, 2), standard=f["case3"]["standard"],
            variants=[_variant()], variant_models={"v": {"formula": "A", "premises": ["A"]}}),
    ("xai.py", ".distill_blackbox", "blackbox.predict"): lambda log, f: _distill(log, f["monkeypatch"]),
}


def _scores_spy(log):
    def predict(*args, **kwargs):
        log.append((args, kwargs))
        return [.1] * len(args[0])
    return predict


class _SpyGate(CrossMethodConflictGate):
    def __init__(self, log):
        super().__init__(admit_assertion=lambda a: True)
        self.log = log

    def reconcile(self, *args, **kwargs):
        self.log.append((args, kwargs))
        return super().reconcile(*args, **kwargs)


class _SpyEvaluator(InProcessAssuranceEvaluator):
    def __init__(self, log, **kwargs):
        super().__init__(**kwargs)
        self.log = log

    def evaluate(self, *args, **kwargs):
        self.log.append((args, kwargs))
        return super().evaluate(*args, **kwargs)


def _gate(log, fixtures, name):
    gate, change, pairs, controls = fixtures["passing_adaptation"]
    setattr(gate, name, _spy(log))
    gate.assess(change, pairs, controls, detected_failures=change.mitigates)


def test_every_listed_call_has_a_spy():
    assert set(SCENARIOS) == {(m, where, callee) for m, where, callee, _ in EXPECTED}


@pytest.mark.parametrize("site", sorted(SCENARIOS), ids=lambda site: f"{site[0]}:{site[1]}:{site[2]}")
def test_each_collaborator_is_handed_only_what_it_cannot_change(site, case3, passing_adaptation, monkeypatch):
    log: list = []
    SCENARIOS[site](log, {"case3": case3, "passing_adaptation": passing_adaptation, "monkeypatch": monkeypatch})
    assert log, f"the spy for {site} was never called"
    bad = [f"{type(value).__name__}: {value!r}"[:120] for args, kwargs in log for value in (*args, *kwargs.values())
           if not _frozen(value)]
    assert bad == [], f"{site} was handed something it can change: " + "; ".join(bad)
