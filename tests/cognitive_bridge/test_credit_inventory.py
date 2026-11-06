# SPDX-License-Identifier: Apache-2.0
"""The class untrusted_input_given_authority, removed at its root for what collaborators answer (met in PR #8's
reviews 2 and 3, and again in follow-up review 1's findings 1, 2, 3 and 5), with failure_read_as_success's instance
(finding 4). A collaborator's answer enters the bridge in one place, ``snapshot.returned(<call>, <check>, ...)``; this
lists, from the code, every check an answer goes through and holds every one to two rules:

* credit binds its subject: an answer a decision credits is bound to what its collaborator was handed (a dossier to the
  evidence, by the digest it names; a reconciliation to the assertions, by theirs; a method selection to the context,
  by re-checking each row's eligibility and the budget), so an answer for another subject is never credited;
* an answer carries only its format's fields and claims nothing (``codes.only_fields``): an answer with a field G6
  reads as a state (``reliability_label``), G6's ``verified`` flag outside a warning card, or a field its format does
  not define, never reaches an output. Each answer is planted with them, through the public call that takes it.

``test_claim_inventory.py`` extends this: it plants a claim at each one place of each answer, and lists the tool replies
and the makers too.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
import types
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import (
    BayesianUpdater, CandidateDossier, ClaimIR, CognitiveAgent, CognitiveAssuranceOrchestrator,
    CrossMethodConflictGate, EvidenceArtifact, FrozenVerifier, Hypothesis, InformationGainSelector,
    InProcessAssuranceEvaluator, LLMClaimCompiler, LLMIdeaGenerator, LLMMetacognitiveCritic, LLMProposer,
    MetacognitiveMethodSelector, MetacognitivePlanner, MethodVariant, MLAcquisitionSelector, Observation, Probe,
    ProblemSignature, ScopedAssertion, ToolSpec, distill_blackbox,
)
from g6.cognitive_bridge import assurance, epistemic, metacognition

_spec = importlib.util.spec_from_file_location("_collaborators", Path(__file__).with_name(
    "test_collaborator_inventory.py"))
_collaborators = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_collaborators)

STATES = ("qualified-draft", "blocked-escalated")


def _claims(value, in_card=False) -> list[str]:
    """This test's own reading of a claim (not the code's): a ``reliability_label`` anywhere, a ``verified`` flag that
    is not a false one inside a warning card, a ``completion_state`` that is not one of the bridge's two states, or the
    field this test plants as one no format defines."""
    found = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in ("reliability_label", "planted_field") or (key == "completion_state" and item not in STATES) or (
                    key == "verified" and not (in_card and item is False)):
                found.append(key)
            found += _claims(item, in_card or key == "warning_card")
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += _claims(item, in_card)
    return found


# --- Planting a claim into an answer ----------------------------------------------------------------------------------

#: What each kind of planting adds: at the top of an answer (to every mapping it has), or only inside its mappings.
MARKS = {"top-verified": {"verified": True, "reliability_label": "verified", "planted_field": "planted"},
         "top-blocked": {"reliability_label": "blocked-escalated"},
         "nested": {"verified": True, "reliability_label": "verified"}}


def _plant(value, mode, depth=0):
    marks = MARKS[mode]
    if isinstance(value, CandidateDossier):
        return CandidateDossier.from_payload(_plant(dict(value.payload), mode, depth))  # intact: the digest is remade
    if getattr(type(value), "__snapshot_record__", False):
        return replace(value, **{f.name: _plant(getattr(value, f.name), mode, depth)
                                 for f in dataclasses.fields(value) if f.init})
    if isinstance(value, Mapping):
        out = {k: _plant(v, mode, depth + 1) for k, v in value.items()}
        if mode != "nested" or depth >= 1:
            out.update(marks)
        return out
    if isinstance(value, (list, tuple)):
        return type(value)(_plant(v, mode, depth) for v in value) if isinstance(value, list) else tuple(
            _plant(v, mode, depth) for v in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return json.dumps(_plant(parsed, mode, depth)) if isinstance(parsed, (dict, list)) else value
    return value


def _planter(mode):
    """The answer with ``mode``'s marks planted; an answer with no mapping in it (a verdict, a score, a record of
    scalars) is replaced at the top by the marks themselves, a tuple of them element by element."""
    def plant(answer):
        if mode is None:
            return answer
        planted = _plant(answer, mode)
        if mode != "nested" and planted == answer:
            marks = MARKS[mode]
            planted = tuple(dict(marks) for _ in answer) if isinstance(answer, (list, tuple)) else dict(marks)
        return planted
    return plant


# --- Each check, reached through the public call that takes its answer ------------------------------------------------

def _case(case3):
    run = case3["orchestrator"].execute(case3["compiled"], approved=True)
    return lambda **kw: case3["orchestrator"].assess(
        case3["compiled"], standard=case3["standard"], run_result=run, verifiers=case3["verifiers"],
        artifacts=case3["artifacts"], documentation=case3["documentation"],
        candidate_actor=case3["candidate_actor"], **{"evaluator": case3["evaluator"], **kw})


def _dossier_site(plant, f):
    class Evaluator(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            return plant(super().evaluate(**kwargs))
    return _case(f["case3"])(evaluator=Evaluator(evaluator_id="outside"))


def _report_site(plant, f):
    class Gate(CrossMethodConflictGate):
        def reconcile(self, assertions):
            return plant(super().reconcile(assertions))
    assertion = ScopedAssertion("claim", "truth", "a" * 64, "supports", "formal", "source1", "sig1")
    return _case(f["case3"])(scoped_assertions=[assertion], epistemic_gate=Gate(admit_assertion=lambda a: True))


def _variant():
    return MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)


def _context():
    return ProblemSignature("review", "logic", "validity", 1, 2)


class _Selector(MetacognitiveMethodSelector):
    def __init__(self, plant):
        super().__init__()
        self.plant = plant

    def select(self, *args, **kwargs):
        return self.plant(super().select(*args, **kwargs))


def _compile_site(plant, f):
    return CognitiveAssuranceOrchestrator(_Selector(plant)).compile(
        claim=ClaimIR("claim", "A entails A", ("formal",), {}), context=_context(), standard=f["case3"]["standard"],
        variants=[_variant()], variant_models={"v": {"formula": "A", "premises": ["A"]}}).to_dict()


def _plan_site(plant, f):
    return MetacognitivePlanner(_Selector(plant)).plan(
        [ClaimIR("claim", "A entails A", ("formal",), {"formal": {"formula": "A"}})], _context(), [_variant()],
        required_methods=("formal",))


def _verdict_site(plant, f):
    c = f["case3"]
    checker = replace(c["verifiers"]["checker"], check=lambda content: plant(content == b"GOOD"))
    return c["evaluator"].evaluate(standard=c["standard"], artifacts=c["artifacts"], verifiers={"checker": checker},
                                   documentation=c["documentation"], candidate_actor=c["candidate_actor"]).to_dict()


def _model(plant, reply):
    return SimpleNamespace(complete=lambda *args, **kwargs: plant(reply))


def _hypotheses():
    return [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)]


def _tool(call=None):
    return ToolSpec("read", call or (lambda q, a: Observation(True, "actual:source")))


def _probe():
    return Probe("p", "measure", "read", {"A": True, "B": False}, .8, arguments={"k": [1]})


def _agent(plant, role):
    """The agent's run with one collaborator (``role``) answering through ``plant``; the run's own output."""
    class Spy:
        steps = 0

        def propose(self, *args, **kwargs):
            self.steps += 1
            answer = (_probe(),) if self.steps == 1 else ()
            return plant(answer) if role == "propose" else answer

        def rank(self, *args, **kwargs):
            answer = tuple(InformationGainSelector().rank(*args, **kwargs))
            return plant(answer) if role == "rank" else answer

        def update(self, *args, **kwargs):
            answer = BayesianUpdater().update(*args, **kwargs)
            return plant(answer) if role == "update" else answer

    spy = Spy()
    observe = (lambda q, a: plant(Observation(True, "actual:source"))) if role == "observe" else None
    return CognitiveAgent(spy, {"read": _tool(observe)}, selector=spy, updater=spy).run(
        "measure", _hypotheses(), budget=1, max_steps=2)


def _scores_site(plant, f):
    predictor = SimpleNamespace(predict=lambda rows: plant(tuple(.1 for _ in rows)))
    return MLAcquisitionSelector(predictor).rank({"A": .5, "B": .5}, [_probe()], 1)


def _predictions_site(plant, f):
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
    f["monkeypatch"].setitem(sys.modules, "sklearn", package)
    f["monkeypatch"].setitem(sys.modules, "sklearn.tree", tree_module)
    rows = [[float(i), float(i % 3)] for i in range(20)]
    tree, report = distill_blackbox(SimpleNamespace(predict=lambda X: plant(tuple(0 for _ in X))), rows[:12],
                                    rows[12:], ["x", "y"])
    return {"tree": tree.to_dict(), "report": report}


_CLAIMS_REPLY = ('{"claims": [{"id": "c", "statement": "s", "evidence_needs": ["formal"], '
                 '"models": {"formal": {"formula": "A"}}}]}')
_CRITIC_REPLY = json.dumps({"candidates": [{
    "failure_mode": "f", "assumption_at_risk": "a", "counterexample_or_refutation": "c", "suggested_method": "formal",
    "evidence_required": "e", "potential_domain_standard_gap": ""}]})
_IDEAS_REPLY = json.dumps({"ideas": [{"id": "i", "statement": "s", "cross_domain_analogy": "a",
                                      "falsifying_question": "q"}]})
_PROBES_REPLY = json.dumps({"probes": [{"id": "p", "question": "q", "tool": "read", "arguments": {"k": [1]},
                                        "positive_if": {"A": True, "B": False}, "novelty": 0.1}]})

#: Every check a collaborator's answer goes through, from the code (test_every_check_is_listed), with each public call
#: that takes an answer through it: (name, run(plant, fixtures) -> the call's output).
SITES = {
    "_dossier": [("assess's evaluator", _dossier_site)],
    "_reconciled": [("assess's conflict gate", _report_site)],
    "checked_selection": [("compile's selector", _compile_site), ("plan's selector", _plan_site)],
    "_verdict": [("the evaluator's checker", _verdict_site)],
    "json_reply": [("LLMProposer", lambda p, f: [asdict(x) for x in LLMProposer(_model(p, _PROBES_REPLY),
                                                                             {"read": _tool()}).propose(
                 "goal", _hypotheses(), [])]),
             ("LLMIdeaGenerator", lambda p, f: [asdict(x) for x in LLMIdeaGenerator(_model(p, _IDEAS_REPLY)).generate(
                 "goal", [])]),
             ("LLMClaimCompiler", lambda p, f: LLMClaimCompiler(_model(p, _CLAIMS_REPLY)).draft("Does A?").to_dict()),
             ("LLMMetacognitiveCritic", lambda p, f: LLMMetacognitiveCritic(_model(p, _CRITIC_REPLY)).propose(
                 "Assess", "A -> A", []).to_dict())],
    "_proposed": [("the agent's proposer", lambda p, f: _agent(p, "propose"))],
    "_selection": [("the agent's selector", lambda p, f: _agent(p, "rank"))],
    "_posterior": [("the agent's updater", lambda p, f: _agent(p, "update"))],
    "_observed": [("the agent's tool", lambda p, f: _agent(p, "observe"))],
    "_scores": [("the acquisition selector's predictor", _scores_site)],
    "_predictions": [("the surrogate's black box", _predictions_site)],
}
_ALL = [(check, name, run) for check, sites in sorted(SITES.items()) for name, run in sites]


def test_every_check_is_listed():
    assert set(SITES) == set(_collaborators._checks())


@pytest.fixture
def fixtures(case3, monkeypatch):
    return {"case3": case3, "monkeypatch": monkeypatch}


@pytest.mark.parametrize("check,name,run", _ALL, ids=[f"{c}:{n}" for c, n, _ in _ALL])
def test_each_site_takes_an_honest_answer(check, name, run, fixtures):
    # The harness reaches each check: unplanted, the call runs and its output claims nothing.
    assert _claims(run(_planter(None), fixtures)) == []


@pytest.mark.parametrize("mode", sorted(MARKS))
@pytest.mark.parametrize("check,name,run", _ALL, ids=[f"{c}:{n}" for c, n, _ in _ALL])
def test_no_claim_a_collaborator_plants_reaches_an_output(check, name, run, mode, fixtures):
    try:
        out = run(_planter(mode), fixtures)
    except (TypeError, ValueError):
        return  # refused: the answer is not its format, or claims a state
    assert _claims(out) == [], f"{name}'s planted {mode} answer reached the output: {_claims(out)}"


# --- Credit binds its subject: an answer for another subject is never credited ---------------------------------------

def _evidence(case3, **change):
    """The evidence an assessment is handed, with one member changed: each member of a dossier's subject."""
    accepts = FrozenVerifier("checker", "outside", "b" * 64, lambda b: b.startswith(b"GOOD"), (b"GOOD",), (b"BAD",))
    artifact = EvidenceArtifact("e", b"GOOD", "report", "candidate", "store:e", "claim")
    out = {"standard": case3["standard"], "artifacts": {"c": artifact}, "verifiers": {"checker": accepts},
           "documentation": dict(case3["documentation"]), "candidate_actor": "candidate"}
    member = change.get("member")
    if member == "content":
        out["artifacts"] = {"c": replace(artifact, content=b"GOOD-other")}
    elif member == "source_ref":
        out["artifacts"] = {"c": replace(artifact, source_ref="store:other")}
    elif member == "verifier":
        out["verifiers"] = {"checker": replace(accepts, version_sha256="c" * 64)}
    elif member == "documentation":
        out["documentation"]["methodology"] = "another methodology, with its limits"
    elif member == "candidate":
        out["artifacts"] = {"c": replace(artifact, claimed_by="other")}
        out["candidate_actor"] = "other"
    return out


@pytest.mark.parametrize("member", ["content", "source_ref", "verifier", "documentation", "candidate"])
def test_a_dossier_is_credited_only_for_the_evidence_it_assessed(case3, member):
    other = _evidence(case3, member=member)
    cached = InProcessAssuranceEvaluator(evaluator_id="outside").evaluate(**other)
    assert cached.payload["completion_state"] == "qualified-draft"

    class Cached(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            return cached

    run = case3["orchestrator"].execute(case3["compiled"], approved=True)
    assess = case3["orchestrator"].assess
    own = assess(case3["compiled"], run_result=run, evaluator=Cached(evaluator_id="outside"), **other)
    assert own["completion_state"] == "qualified-draft", "the dossier is not credited even for its own evidence"
    submitted = _evidence(case3)
    out = assess(case3["compiled"], run_result=run, evaluator=Cached(evaluator_id="outside"), **submitted)
    assert out["completion_state"] == "blocked-escalated", f"a dossier for other {member} was credited"
    assert "not for the evidence submitted" in out["reason"]


def _assertions():
    one = ScopedAssertion("claim", "truth", "a" * 64, "supports", "formal", "source:1", "receipt:1")
    two = replace(one, method="graph", evidence_ref="source:2", evaluator_receipt="receipt:2")
    against = replace(one, stance="refutes", method="causal", evidence_ref="source:3", evaluator_receipt="receipt:3")
    return one, two, against


@pytest.mark.parametrize("cached_for,submitted", [("one", "one,against"), ("one,two", "one"), ("one", "two")],
                         ids=["a subset", "a superset", "another"])
def test_a_reconciliation_is_credited_only_for_the_assertions_it_reconciled(case3, cached_for, submitted):
    rows = dict(zip(("one", "two", "against"), _assertions()))
    gate = CrossMethodConflictGate(admit_assertion=lambda a: True)
    cached = gate.reconcile([rows[k] for k in cached_for.split(",")])
    assert cached["completion_state"] == "qualified-draft"

    class Cached(CrossMethodConflictGate):
        def reconcile(self, assertions):
            return cached

    assess = _case(case3)
    assert assess(scoped_assertions=[rows[k] for k in reversed(cached_for.split(","))],
                  epistemic_gate=Cached())["completion_state"] == "qualified-draft", "not credited for its own set"
    out = assess(scoped_assertions=[rows[k] for k in submitted.split(",")], epistemic_gate=Cached())
    assert out["completion_state"] == "blocked-escalated", f"a report for {cached_for} was credited for {submitted}"
    assert "not for the assertions submitted" in out["reason"]


def _two_methods():
    graph = metacognition.ENGINE_SPECS["graph"]
    return [MethodVariant("f", "formal", "SMT", "formal", "check_validity", 1),
            MethodVariant("g", "graph", "annotated-graph", graph["component"], graph["op"], 1)]


_EDITS = ("cost", "obligation", "variant", "coverage", "requirements", "refused")


@pytest.mark.parametrize("member", ["budget", "evidence", "portfolio", *_EDITS])
def test_a_selection_is_taken_only_when_eligible_under_the_context_it_is_for(case3, member):
    variants, context, eligible = [_variant()], _context(), _context()
    required = ["formal"]
    if member == "budget":
        context = replace(context, budget=0.5)
    elif member == "evidence":
        variants = [replace(_variant(), expects=("dataset",))]
        eligible = replace(context, available_evidence=("dataset",))
    elif member == "portfolio":  # each row affordable alone, the two together not (the member a per-row check misses)
        variants, required = _two_methods(), ["formal", "graph"]
        context = replace(context, budget=1.5)
    decision = MetacognitiveMethodSelector().select(eligible, variants, required_methods=required)
    assert not decision.missing
    if member in ("cost", "obligation", "variant"):
        row = dict(decision.ordered_variants[0])
        row.update({"cost": {"expected_cost": 0.5}, "obligation": {"obligation": "waived"},
                    "variant": {"variant_id": "unlisted"}}[member])
        decision = replace(decision, ordered_variants=(row,))
    elif member == "coverage":  # says nothing is missing, and meets no method
        decision = replace(decision, ordered_variants=())
    elif member == "requirements":  # a selection for other methods than the ones handed
        decision = replace(decision, requirements=("graph",))
    elif member == "refused":  # says a method is missing, and is not refused
        decision = replace(decision, missing=("formal",))

    class Cached:
        def select(self, *args, **kwargs):
            return decision

    def plan(where):  # the planner only advises, and still takes no selection that is not for its context
        return MetacognitivePlanner(Cached()).plan([ClaimIR("claim", "A entails A", tuple(required), {
            "formal": {"formula": "A"}})], where, variants, required_methods=required)

    def compile_(where):
        return CognitiveAssuranceOrchestrator(Cached()).compile(
            claim=ClaimIR("claim", "A entails A", tuple(required), {}), context=where,
            standard=case3["standard"], variants=variants,
            variant_models={"v": {"formula": "A", "premises": ["A"]}, "f": {"formula": "A", "premises": ["A"]},
                            "g": {"program": "p.", "atom": "p"}})

    with pytest.raises(ValueError):
        compile_(context)
    with pytest.raises(ValueError):
        plan(context)
    if member not in _EDITS:  # the same selection is taken in the context it is for
        assert compile_(eligible).completion_state == "qualified-draft"
        plan(eligible)


# --- Unreadable or malformed answers: refused, never read as a pass ---------------------------------------------------

def _honest_dossier(case3):
    c = case3
    return c["evaluator"].evaluate(standard=c["standard"], artifacts=c["artifacts"], verifiers=c["verifiers"],
                                   documentation=c["documentation"], candidate_actor=c["candidate_actor"])


@pytest.mark.parametrize("edit", ["blockers-but-qualified", "missing-field", "blockers-not-a-list",
                                  "state-not-a-state", "subject-not-a-digest"])
def test_a_malformed_dossier_is_refused_or_not_credited(case3, edit):
    payload = json.loads(json.dumps(_honest_dossier(case3).payload))
    if edit == "blockers-but-qualified":  # the member the old pattern missed: a blocker read as a pass
        payload["blockers"] = ["c: artifact failed verifier"]
    elif edit == "missing-field":
        del payload["checks"]
    elif edit == "blockers-not-a-list":
        payload["blockers"] = "none"
    elif edit == "state-not-a-state":
        payload["completion_state"] = "verified"
    else:
        payload["subject_sha256"] = 5

    class Malformed(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            return CandidateDossier.from_payload(payload)

    try:
        out = _case(case3)(evaluator=Malformed(evaluator_id="outside"))
    except (TypeError, ValueError):
        return
    assert out["completion_state"] == "blocked-escalated", f"a dossier with {edit} was credited"


@pytest.mark.parametrize("edit", ["state-not-a-state", "not-a-mapping", "subject-not-a-digest", "no-subject",
                                  "blocked-finding-but-qualified", "unadmitted-but-qualified", "no-finding"])
def test_a_malformed_reconciliation_is_refused_or_not_credited(case3, edit):
    one, _, against = _assertions()
    gate = CrossMethodConflictGate(admit_assertion=lambda a: a != against)
    submitted = [one]
    report = json.loads(json.dumps(gate.reconcile(submitted)))
    if edit == "state-not-a-state":
        report["completion_state"] = "verified"
    elif edit == "not-a-mapping":
        report = list(report.items())
    elif edit == "subject-not-a-digest":
        report["subject_sha256"] = ["not", "a", "digest"]
    elif edit == "no-subject":
        del report["subject_sha256"]
    else:  # the members the old pattern missed: a report whose own findings block, read as a pass by its top state
        rows = {"blocked-finding-but-qualified": [one, replace(against, evidence_ref="source:4")],
                "unadmitted-but-qualified": [one, against], "no-finding": [against]}[edit]
        honest = json.loads(json.dumps(gate.reconcile(rows)))
        assert honest["completion_state"] == "blocked-escalated"
        report = {k: v for k, v in honest.items() if k not in ("code", "reason", "warning_card")}
        report["completion_state"] = "qualified-draft"
        submitted = rows

    class Malformed(CrossMethodConflictGate):
        def reconcile(self, assertions):
            return report

    try:
        out = _case(case3)(scoped_assertions=submitted, epistemic_gate=Malformed())
    except (TypeError, ValueError):
        return
    assert out["completion_state"] == "blocked-escalated", f"a report with {edit} was credited"


def test_a_selection_row_with_a_missing_field_is_refused():
    decision = MetacognitiveMethodSelector().select(_context(), [_variant()], required_methods=["formal"])
    row = {k: v for k, v in decision.ordered_variants[0].items() if k != "ranking_score"}
    with pytest.raises(ValueError):
        replace(decision, ordered_variants=(row,))


# --- The formats are the makers' own: what each reference maker writes is exactly its format's fields ----------------

def test_each_reference_maker_writes_exactly_its_formats_fields(case3):
    from g6.cognitive_bridge.codes import MADE_FIELDS

    passing = _honest_dossier(case3).payload
    blocked = case3["evaluator"].evaluate(
        standard=case3["standard"], artifacts={}, verifiers=case3["verifiers"],
        documentation=case3["documentation"], candidate_actor=case3["candidate_actor"]).payload
    assert blocked["completion_state"] == "blocked-escalated"
    for payload in (passing, blocked):
        assert set(assurance.PAYLOAD_FIELDS) <= set(payload) <= set(assurance.PAYLOAD_FIELDS) | set(MADE_FIELDS)
    one, _, against = _assertions()
    for rows in ([one], [one, against], []):
        for admit in (lambda a: True, lambda a: a != against):
            report = CrossMethodConflictGate(admit_assertion=admit).reconcile(rows)
            assert set(epistemic.REPORT_FIELDS) <= set(report) <= set(epistemic.REPORT_FIELDS) | set(MADE_FIELDS)
            # the state a report's findings give is the state the gate gives it: one rule, read by both
            assert epistemic.findings_state(json.loads(json.dumps(report))) == report["completion_state"]
    extra = replace(_variant(), id="w", representation="SMT-2")
    for context in (_context(), replace(_context(), budget=0)):
        decision = MetacognitiveMethodSelector().select(context, [_variant(), extra], required_methods=["formal"],
                                                        max_optional=1)
        assert all(set(row) == set(metacognition.ROW_FIELDS) for row in decision.ordered_variants)
