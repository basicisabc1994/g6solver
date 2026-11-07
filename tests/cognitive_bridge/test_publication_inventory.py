# SPDX-License-Identifier: Apache-2.0
"""The class guard_covers_less_than_it_guards, removed at its root for what leaves the bridge (review 2 of PR #8's
second follow-up, finding 2): ``ExplicitTree`` published a node's ``value`` as given in ``explain_one``, ``predict``
and ``to_dict``, so a caller's ``{"verified": true, "reliability_label": "verified"}`` reached all three. Now a claim is
refused where the data enters (a record's data field runs the claim rule when the record is made, ``snapshot._field``),
and every public function and method that returns data out of the package returns what the claim rule passed
(``codes.unclaimed``, or a maker, ``codes.refusal`` and ``codes.settled``, which apply it).

This lists from the code every public function and method of the package (``g6.cognitive_bridge.__all__``, properties
too) that returns data rather than a record, and checks that each of its returns is what the claim rule passed; one
that returns a leaf (text, a flag, a number), which the rule reads as itself, returns one. Then it plants a claim (G6's
``verified`` flag, a ``reliability_label``, a ``completion_state`` that is not a result) in each input that can reach
an output: in every data field of every record type, where the record is made; as a key, in each input whose text
becomes a key of an output (a hypothesis id, a belief map); and in each value a caller or a collaborator hands in that
an output can carry (a tool's, a model's, a predictor's, a black box's and a registry's answer, a run, a trace, the raw
arguments), and fails on any output that carries it."""
from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
import textwrap
import types
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from returns.result import Success

import g6.cognitive_bridge as bridge
from g6.cognitive_bridge import (
    DOCUMENT_FIELDS, AdaptationPromotionGate, BayesianUpdater, CandidateDossier, ClaimIR, CognitiveAgent,
    CognitiveAssuranceOrchestrator, CompositionalFailureModel, Criterion, CritiqueReport, CrossMethodConflictGate,
    DomainStandard, EvidenceArtifact, ExplicitTree, FrozenVerifier, Hypothesis, InformationGainSelector,
    InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MetacognitivePlanner, MethodRouter, MethodVariant,
    MLAcquisitionSelector, MixedMethodExecutor, Observation, Probe, ProblemSignature, StaticProposer, ToolSpec, TreeNode,
    check_beliefs, codes, distill_blackbox, evaluated_trace_rows, expected_information_gain, sklearn_payload,
)
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.cognitive_bridge.snapshot import _kinds, view

_spec = importlib.util.spec_from_file_location("_boundary", Path(__file__).with_name("test_boundary_inventory.py"))
_boundary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_boundary)
MADE, _record_classes = _boundary.MADE, _boundary._record_classes

#: A claim of each kind the rule finds: G6's flag, a reliability label, and a completion_state that is not a result.
CLAIMS = {"verified": {"verified": True}, "reliability_label": {"reliability_label": "verified"},
          "completion_state": {"completion_state": "verified"}}


def published():
    """Every public function and method of the package (``__all__``), properties included, with its source."""
    for name in bridge.__all__:
        obj = getattr(bridge, name)
        if inspect.isfunction(obj):
            yield name, obj
        elif inspect.isclass(obj) and not issubclass(obj, BaseModel):
            for attr, member in vars(obj).items():
                member = member.fget if isinstance(member, property) else member
                member = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
                if not attr.startswith("_") and inspect.isfunction(member):
                    try:
                        inspect.getsource(member)
                    except (OSError, TypeError):
                        continue  # made by dataclasses: a record's own
                    yield f"{name}.{attr}", member


def _node(fn):
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


def _annotation(fn) -> str:
    node = _node(fn)
    if node.returns is None:
        return ""
    text = ast.unparse(node.returns)
    return text[1:-1] if isinstance(node.returns, ast.Constant) else text


#: What a returned value's annotation says it is, beside data.
LEAVES = ("str", "bool", "int", "float")


def _returns(fn) -> str:
    """``record`` (a record, or a list or tuple of records), ``leaf`` (text, a flag, a number) or ``data``, by its
    return annotation."""
    annotation, records = _annotation(fn), set(_record_classes())
    if annotation in records or any(annotation == f"{outer}[{r}{rest}]" for r in records
                                    for outer in ("list", "tuple") for rest in ("", ", ...")):
        return "record"
    return "leaf" if annotation in LEAVES else "data"


def publications():
    return {name: fn for name, fn in published() if _returns(fn) == "data"}


EXPECTED_PUBLICATIONS = {
    "AdaptationPromotionGate.assess", "BayesianUpdater.update", "CandidateDossier.to_dict", "CognitiveAgent.run",
    "CognitiveAssuranceOrchestrator.assess", "CognitiveAssuranceOrchestrator.execute", "CognitiveBridgeBlock.infer",
    "CognitiveBridgeBlock.screen_raw", "CompiledResearch.to_dict", "CompositionalFailureModel.map",
    "CritiqueReport.to_dict", "CrossMethodConflictGate.reconcile", "DomainStandard.manifest",
    "EvidenceArtifact.manifest", "ExplicitTree.explain_one", "ExplicitTree.predict", "ExplicitTree.to_dict",
    "FrozenVerifier.manifest", "InformationGainSelector.rank", "MLAcquisitionSelector.rank", "MethodPlan.to_dict",
    "MetacognitivePlanner.plan", "MixedMethodExecutor.execute", "SelectionDecision.to_dict", "check_beliefs",
    "distill_blackbox", "evaluated_trace_rows", "sklearn_payload",
}


def test_the_publications_are_listed_from_the_code():
    assert set(publications()) == EXPECTED_PUBLICATIONS


# --- 1. Every publication returns what the claim rule passed -----------------------------------------------------------

def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8")))
            for path in sorted(Path(codes.__file__).resolve().parent.glob("*.py"))]


def _all_functions():
    found = []
    for _, tree in _modules():
        def visit(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append(child)
                visit(child)
        visit(tree)
    return found


def _own_returns(fn):
    parent = {c: p for p in ast.walk(fn) for c in ast.iter_child_nodes(p)}
    out = []
    for node in [n for n in ast.walk(fn) if isinstance(n, ast.Return)]:
        up = parent[node]
        while not isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            up = parent[up]
        if up is fn:
            out.append(node)
    return out


#: Calls that hand on what they are given, by the position of what they hand on: a result wrapped for G6's registry, a
#: record issued, and the boundary's conversion (which adds nothing to what it converts).
WRAPPERS = {"ok": 0, "Success": 0, "issue": 1, "plain": 0, "_plain": 0}
#: The claim rule, and the makers that apply it.
RULE = {"unclaimed", "settled", "refusal", "only_fields", "returned"}


def _callee(call) -> str:
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")


def _through(fn, value, names, records=()) -> bool:
    """Whether ``value`` (returned by ``fn``) is what the claim rule passed: a call to one of ``names``, a wrapper's
    argument that is, a name every assignment in ``fn`` binds to one, both branches of a conditional, null, or a tuple
    whose items are (or, where the annotation names a record, are that record)."""
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if isinstance(value, ast.IfExp):
        return _through(fn, value.body, names, records) and _through(fn, value.orelse, names, records)
    if isinstance(value, ast.Tuple):
        return all(index < len(records) and records[index] or _through(fn, item, names)
                   for index, item in enumerate(value.elts))
    if isinstance(value, ast.Name):
        bound = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == value.id for t in n.targets)]
        return bool(bound) and all(_through(fn, v, names) for v in bound)
    if not isinstance(value, ast.Call):
        return False
    callee = _callee(value)
    if callee in names:
        return True
    at = WRAPPERS.get(callee)
    return at is not None and len(value.args) > at and _through(fn, value.args[at], names)


def _rule_closure() -> set[str]:
    """The rule, and every function in the package whose every return is what it passed, until nothing new is."""
    names, changed = set(RULE), True
    while changed:
        changed = False
        for fn in _all_functions():
            returns = _own_returns(fn)
            if fn.name not in names and returns and all(r.value is not None and _through(fn, r.value, names)
                                                       for r in returns):
                names.add(fn.name)
                changed = True
    return names


@pytest.mark.parametrize("name", sorted(EXPECTED_PUBLICATIONS))
def test_every_publication_returns_what_the_claim_rule_passed(name):
    fn = publications()[name]
    node, names = _node(fn), _rule_closure()
    annotation = _annotation(fn)
    records = tuple(item.strip() in _record_classes() for item in annotation[6:-1].split(",")) \
        if annotation.startswith("tuple[") else ()
    bad = [ast.unparse(r)[:100] for r in _own_returns(node) if r.value is None or not _through(node, r.value, names,
                                                                                                 records)]
    assert bad == [], f"{name} returns what the claim rule did not pass: {bad}"


def _leaf_calls():
    research = MADE["CompiledResearch"]()
    case = _case()
    return {
        "CandidateDossier.intact": lambda: MADE["CandidateDossier"]().intact(),
        "CompiledResearch.changed_since_compiled": lambda: research.changed_since_compiled(),
        "CompiledResearch.completion_state": lambda: research.completion_state,
        "DomainStandard.digest": lambda: MADE["DomainStandard"]().digest,
        "DomainStandard.risk_tier": lambda: MADE["DomainStandard"]().risk_tier,
        "EvidenceArtifact.digest": lambda: MADE["EvidenceArtifact"]().digest,
        "InProcessAssuranceEvaluator.review_scope": lambda: case["evaluator"].review_scope(
            standard=case["standard"], artifacts=case["artifacts"], verifiers=case["verifiers"],
            documentation=case["documentation"], candidate_actor="candidate"),
        "MethodVariant.definition_digest": lambda: MADE["MethodVariant"]().definition_digest,
        "ProblemSignature.risk_tier": lambda: MADE["ProblemSignature"]().risk_tier,
        "SelectionDecision.completion_state": lambda: MADE["SelectionDecision"]().completion_state,
        "expected_information_gain": lambda: expected_information_gain({"A": .5, "B": .5}, PROBE),
    }


def test_every_publication_of_a_leaf_returns_that_leaf():
    leaves = {name: fn for name, fn in published() if _returns(fn) == "leaf"}
    calls = _leaf_calls()
    assert set(leaves) == set(calls)
    for name, call in calls.items():
        assert type(call()).__name__ == _annotation(leaves[name]), name


# --- 2. A claim in every data field of every record is refused where the record is made -------------------------------

def _data_fields():
    return sorted((name, field) for name, cls in _record_classes().items() for field, kind in _kinds(cls).items()
                  if kind[0] == "data")


@pytest.mark.parametrize("mark", sorted(CLAIMS))
@pytest.mark.parametrize("name,field", _data_fields())
def test_a_claim_in_every_data_field_of_every_record_is_refused_where_it_is_made(name, field, mark):
    made = MADE[name]()
    for planted in (CLAIMS[mark], [CLAIMS[mark]], {"k": [CLAIMS[mark]]}):
        with pytest.raises(ValueError, match="claims a state") as excinfo:
            replace(made, **{field: planted})
        assert f"{name}.{field}" in str(excinfo.value)


def test_the_data_fields_are_the_records_own():
    # the listing is not vacuous: it finds the field review 2 planted in, and the data fields of the other records
    fields = _data_fields()
    assert ("TreeNode", "value") in fields and ("Probe", "arguments") in fields and len(fields) >= 20


# --- 3. A claim in each other input that can reach an output -----------------------------------------------------------

ROWS = [[float(i), float(i % 3)] for i in range(20)]
PROBE = Probe("p", "q", "read", {"A": True, "B": False}, .8)
HISTORY = {"expected_information_bits": .5, "cost": 1, "novelty_preference": .1, "assumed_match_probability": .8,
           "measured_utility": .3, "evaluator_ref": "evaluator"}
_VARIANT = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
_CONTEXT = ProblemSignature("d", "u", "k", 1, 2)


class _Registry:
    """A registry whose every op answers ``answer`` (a claim planted in it, or a plain valid one)."""

    def __init__(self, answer=None):
        self.answer = {"valid": True} if answer is None else answer

    def invoke(self, name, op, params=None, **kwargs):
        return Success({"completion_state": "qualified-draft", "answer": self.answer})


def _case(answer=None):
    criterion = Criterion("c", "formal obligation", "checker", "1:1", required_methods=("formal",))
    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64, (criterion,))
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=_Registry(answer))
    compiled = orchestrator.compile(
        claim=ClaimIR("claim", "A entails A", ("formal",), {}), context=ProblemSignature("review", "logic", "validity",
                                                                                          1, 2),
        standard=standard, variants=[_VARIANT], variant_models={"v": {"formula": "A", "premises": ["A"]}})
    return dict(standard=standard, orchestrator=orchestrator, compiled=compiled,
                evaluator=InProcessAssuranceEvaluator(evaluator_id="outside"),
                artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "store:e", "claim")},
                verifiers={"checker": FrozenVerifier("checker", "outside", "b" * 64, lambda b: b == b"GOOD",
                                                     (b"GOOD",), (b"BAD",))},
                documentation={name: "Explicit limits for " + name for name in DOCUMENT_FIELDS},
                candidate_actor="candidate")


def _assessed(run=lambda run: run, documentation=None):
    case = _case()
    executed = case["orchestrator"].execute(case["compiled"], approved=True)
    return case["orchestrator"].assess(
        case["compiled"], standard=case["standard"], run_result=run(executed), evaluator=case["evaluator"],
        verifiers=case["verifiers"], artifacts=case["artifacts"],
        documentation=case["documentation"] if documentation is None else documentation,
        candidate_actor="candidate")


def _beliefs(key):
    return {key: .5, "B": .5}, Probe("p", "q", "read", {key: True, "B": False}, .8)


class _Tool:
    def __init__(self, answer):
        self.answer = answer

    def __call__(self, question, arguments):
        return self.answer


def _agent(*, key="A", tool_answer=None, proposal=None):
    tools = {"read": ToolSpec("read", _Tool(Observation(True, "source") if tool_answer is None else tool_answer))}
    probe = Probe("p", "q", "read", {key: True, "B": False}, .8)
    proposer = (SimpleNamespace(propose=lambda *a: proposal) if proposal is not None else StaticProposer([probe]))
    return CognitiveAgent(proposer, tools, allow=("tool:read",)).run(
        "objective", [Hypothesis(key, "a", .5), Hypothesis("B", "b", .5)], budget=1)


def _block(op="update", key="a", **planted):
    request = {"op": op, "hypotheses": [{"id": key, "statement": "first", "prior": .5},
                                        {"id": "b", "statement": "second", "prior": .5}],
               "probes": [{"id": "p", "question": "q", "tool": "read", "positive_if": {key: True, "b": False},
                           "assumed_match_probability": .8, "arguments": planted.get("arguments", {})}],
               "budget": 1, "probe_id": "p", "observed_positive": True}
    if op == "plan_methods":
        request = {"op": op, "claims": [{"id": "c", "statement": "A", "evidence_needs": ["formal"],
                                         "models": {"formal": planted.get("model", {"formula": "A"})}}]}
    return CognitiveBridgeBlock().infer(request)


class _FakeTree:
    """A fitted tree with one leaf, as scikit-learn's estimators expose one, for a surrogate run here offline."""

    def __init__(self, **kwargs):
        pass

    def fit(self, X, y):
        self.n_features_in_, self.n_outputs_ = len(X[0]), 1
        labels = sorted(set(y), key=repr)
        if type(self).__name__ == "DecisionTreeClassifier":
            self.classes_ = labels
        self.tree_ = SimpleNamespace(children_left=[-1], children_right=[-1], feature=[-2], threshold=[-2.],
                                     value=[[[1.0] * len(labels)]] if hasattr(self, "classes_") else [[[0.0]]])
        return self


def _sklearn(monkeypatch):
    tree = types.ModuleType("sklearn.tree")
    tree.DecisionTreeClassifier = type("DecisionTreeClassifier", (_FakeTree,), {})
    tree.DecisionTreeRegressor = type("DecisionTreeRegressor", (_FakeTree,), {})
    package = types.ModuleType("sklearn")
    package.tree = tree
    monkeypatch.setitem(sys.modules, "sklearn", package)
    monkeypatch.setitem(sys.modules, "sklearn.tree", tree)


def _distilled(monkeypatch, *, names=("x", "y"), answer=None):
    _sklearn(monkeypatch)
    box = SimpleNamespace(predict=lambda rows: [0 for _ in rows] if answer is None else [answer for _ in rows])
    return distill_blackbox(box, ROWS[:12], ROWS[12:], list(names))


def _payload(**changes):
    return {**dict(MADE["CandidateDossier"]().payload), **changes}


#: Each publication's inputs that can reach its output, beside a record's data fields (planted above): ``key`` where
#: the planted text becomes a key, ``value`` where the planted claim is handed in. Each call takes the plant and
#: monkeypatch, and returns the output.
PLACES = {
    "AdaptationPromotionGate.assess": {
        ("value", "a detected failure"): lambda plant, mp: _gate(detected=["false_confidence", plant])},
    "BayesianUpdater.update": {("key", "a belief map"): lambda plant, mp: BayesianUpdater().update(*_beliefs(plant),
                                                                                                    True)},
    "CandidateDossier.to_dict": {("value", "a payload's limitations"): lambda plant, mp: CandidateDossier.from_payload(
        _payload(limitations=[plant])).to_dict()},
    "CognitiveAgent.run": {
        ("key", "a hypothesis id"): lambda plant, mp: _agent(key=plant),
        ("value", "a tool's answer"): lambda plant, mp: _agent(tool_answer=plant),
        ("value", "a proposer's answer"): lambda plant, mp: _agent(proposal=[plant])},
    "CognitiveAssuranceOrchestrator.assess": {
        ("value", "the run handed back"): lambda plant, mp: _assessed(run=lambda run: {**run, "extra": plant}),
        ("value", "a documentary section"): lambda plant, mp: _assessed(
            documentation={name: plant for name in DOCUMENT_FIELDS})},
    "CognitiveAssuranceOrchestrator.execute": {("value", "a registry's answer"): lambda plant, mp: (
        lambda case: case["orchestrator"].execute(case["compiled"], approved=True))(_case(answer=plant))},
    "CognitiveBridgeBlock.infer": {
        ("key", "a hypothesis id"): lambda plant, mp: _block(key=plant),
        ("value", "a probe's arguments"): lambda plant, mp: _block(arguments=plant),
        ("value", "a claim's model"): lambda plant, mp: _block(op="plan_methods", model=plant)},
    "CognitiveBridgeBlock.screen_raw": {("value", "the raw arguments"): lambda plant, mp: CognitiveBridgeBlock(
        ).screen_raw({"op": "update", "extra": plant})},
    "CompiledResearch.to_dict": {("value", "a variant's model"): lambda plant, mp: CognitiveAssuranceOrchestrator(
        MetacognitiveMethodSelector()).compile(
            claim=ClaimIR("c", "s", ("formal",), {}), context=_CONTEXT, variants=[_VARIANT],
            standard=DomainStandard("d", "1", "AU", "a", "a" * 64, (Criterion("c", "d", "checker", "1:1"),)),
            variant_models={"v": {"formula": "A", "extra": plant}}).to_dict()},
    "CompositionalFailureModel.map": {("key", "a finding's code"): lambda plant, mp: CompositionalFailureModel().map(
        [replace(MADE["FailureModeFinding"](), code=plant)],
        [replace(MADE["MitigationHypothesis"](), failure_code=plant)])},
    "CritiqueReport.to_dict": {("key", "an objective and a failure mode"): lambda plant, mp: CritiqueReport(
        (replace(MADE["CritiqueCandidate"](), failure_mode=plant),), plant).to_dict()},
    "CrossMethodConflictGate.reconcile": {("key", "a proposition"): lambda plant, mp: CrossMethodConflictGate(
        lambda row: True).reconcile([replace(MADE["ScopedAssertion"](), proposition_id=plant)])},
    "DomainStandard.manifest": {("key", "a criterion's id"): lambda plant, mp: DomainStandard(
        "d", "1", "AU", "a", "a" * 64, (Criterion(plant, "d", "checker", "1:1"),)).manifest()},
    "EvidenceArtifact.manifest": {("key", "an artifact's id"): lambda plant, mp: replace(
        MADE["EvidenceArtifact"](), id=plant).manifest()},
    "ExplicitTree.explain_one": {("key", "a feature and a sample's key"): lambda plant, mp: ExplicitTree(
        (plant,), TreeNode(feature=0, threshold=.5, left=TreeNode(value=1), right=TreeNode(value=2)), "regression",
        "t").explain_one({plant: 0.0})},
    "ExplicitTree.predict": {("key", "a feature and a sample's key"): lambda plant, mp: ExplicitTree(
        (plant,), TreeNode(feature=0, threshold=.5, left=TreeNode(value=1), right=TreeNode(value=2)), "regression",
        "t").predict([{plant: 0.0}])},
    "ExplicitTree.to_dict": {("key", "a feature"): lambda plant, mp: ExplicitTree(
        (plant,), TreeNode(value=1), "regression", plant).to_dict()},
    "FrozenVerifier.manifest": {("key", "a checker's id"): lambda plant, mp: replace(
        MADE["FrozenVerifier"](), id=plant).manifest()},
    "InformationGainSelector.rank": {
        ("key", "a belief map"): lambda plant, mp: InformationGainSelector().rank(_beliefs(plant)[0],
                                                                                  [_beliefs(plant)[1]], 1),
        ("key", "a probe id"): lambda plant, mp: InformationGainSelector().rank({"A": .5, "B": .5},
                                                                                [replace(PROBE, id=plant)], 1)},
    "MLAcquisitionSelector.rank": {
        ("key", "a belief map"): lambda plant, mp: MLAcquisitionSelector(SimpleNamespace(
            predict=lambda rows: [1.0 for _ in rows])).rank(_beliefs(plant)[0], [_beliefs(plant)[1]], 1),
        ("value", "a predictor's answer"): lambda plant, mp: MLAcquisitionSelector(SimpleNamespace(
            predict=lambda rows: [plant for _ in rows])).rank({"A": .5, "B": .5}, [PROBE], 1)},
    "MethodPlan.to_dict": {("key", "a claim's id"): lambda plant, mp: MethodRouter().plan(
        [ClaimIR(plant, "s", ("formal",), {"formal": {"formula": "A"}})]).to_dict()},
    "MetacognitivePlanner.plan": {("key", "a claim's id and a variant's id"): lambda plant, mp: MetacognitivePlanner(
        MetacognitiveMethodSelector()).plan([ClaimIR(plant, "s", ("formal",), {"formal": {"formula": "A"}})],
                                            _CONTEXT, [replace(_VARIANT, id=plant)], required_methods=["formal"])},
    "MixedMethodExecutor.execute": {("value", "a registry's answer"): lambda plant, mp: MixedMethodExecutor(
        _Registry(plant)).execute(MethodRouter().plan([ClaimIR("c", "s", ("formal",), {"formal": {"formula": "A"}})]),
                                  approved=True)},
    "SelectionDecision.to_dict": {("key", "a variant's id"): lambda plant, mp: MetacognitiveMethodSelector().select(
        _CONTEXT, [replace(_VARIANT, id=plant)], required_methods=["formal"]).to_dict()},
    "check_beliefs": {("key", "a hypothesis id"): lambda plant, mp: check_beliefs(
        [Hypothesis(plant, "a", .5), Hypothesis("B", "b", .5)])},
    "distill_blackbox": {
        ("key", "a feature name"): lambda plant, mp: _distilled(mp, names=(plant, "y")),
        ("value", "a black box's answer"): lambda plant, mp: _distilled(mp, answer=plant)},
    "evaluated_trace_rows": {
        ("key", "a trace's own field"): lambda plant, mp: evaluated_trace_rows([{**HISTORY, plant: True}]),
        ("value", "a trace's own field"): lambda plant, mp: evaluated_trace_rows([{**HISTORY, "extra": plant}])},
    "sklearn_payload": {
        ("key", "the estimator and a trace's own field"): lambda plant, mp: sklearn_payload(
            [{**HISTORY, plant: True}], estimator=plant),
        ("value", "a trace's own field"): lambda plant, mp: sklearn_payload([{**HISTORY, "extra": plant}])},
}


def _gate(*, detected):
    from g6.cognitive_bridge import ControlTrial, GuardChange, PairedTrial

    change = GuardChange("old", "a" * 64, "b" * 64, "c" * 64, ("src/g6/cognitive_bridge/method_adapter.py",),
                         "cognitive_bridge", "candidate", "outside", ("false_confidence",))
    bound = ({"candidate_digest": change.code_digest, "baseline_digest": change.baseline_digest}
             if "candidate_digest" in PairedTrial.__dataclass_fields__ else {})
    pairs = tuple(PairedTrial(str(i), change.suite_digest, False, True, True, "holdout", f"receipt:{i}", **bound)
                  for i in range(6))
    suite = {"suite_digest": change.suite_digest} if "suite_digest" in ControlTrial.__dataclass_fields__ else {}
    controls = (ControlTrial("positive", True, "positive", "receipt:p", **suite),
                ControlTrial("negative", True, "negative", "receipt:n", **suite))
    gate = AdaptationPromotionGate(admit_pair=lambda x: x in pairs, admit_control=lambda x: x in controls,
                                   min_cases=6, min_discordant=6, max_evaluations=1)
    return gate.assess(change, pairs, controls, detected_failures=detected)


def test_every_publication_has_its_inputs_planted_here():
    assert set(PLACES) == EXPECTED_PUBLICATIONS


_CASES = [(name, where, mark) for name, places in sorted(PLACES.items()) for where in sorted(places)
          for mark in sorted(CLAIMS)]


@pytest.mark.parametrize("name,where,mark", _CASES, ids=[f"{n}:{w[1]}:{m}" for n, w, m in _CASES])
def test_no_claim_planted_in_an_input_reaches_an_output(name, where, mark, monkeypatch):
    from g6.core import door

    monkeypatch.setattr(door, "allowed_sensitive", lambda: frozenset())
    plant = mark if where[0] == "key" else CLAIMS[mark]
    try:
        out = PLACES[name][where](plant, monkeypatch)
    except ValueError:
        return  # refused where it entered, or where it would have left
    out = out.value if hasattr(out, "is_fail") else out  # the block's answer, inside G6's Result
    assert codes.claims(view(out)) == [], f"{name} published a claim planted in {where[1]}: {out!r}"[:600]


def test_the_planted_tree_review_2_built_is_refused_where_it_is_made():
    with pytest.raises(ValueError, match="TreeNode.value"):
        TreeNode(value={"verified": True, "reliability_label": "verified"})
