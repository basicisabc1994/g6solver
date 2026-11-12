# SPDX-License-Identifier: Apache-2.0
"""Review 3 of PR #8's fourth follow-up: the inventories its findings showed the last ones could not see.

1. Entries are found by what reaches a copier, not by a parameter's annotation: every parameter of every public
   method or function (the block and the input models included) that is handed to a copier is classified here, as
   bounded (with one-over cases, in repair 2's table or the block's below) or as bounded by the walker's budget alone.
2. Every place the package reads an attribute mapping (``getattr``, ``hasattr``, ``vars``, ``__dict__``,
   ``object.__getattribute__``) is listed with why no unread key or foreign method can answer it.
3. One tally per detachment, a collection taken once, a record's fields read once, one budget for a declared bound,
   and one native scalar per array item: each with a case the last inventory did not have.
"""
import ast
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

import g6.cognitive_bridge as bridge
from g6.cognitive_bridge import (
    ClaimIR, CognitiveBridgeBlock, CognitiveInput, EvidenceArtifact, Hypothesis, MethodPlan, MethodRouter,
    MethodVariant, MixedMethodExecutor, snapshot,
)
from g6.cognitive_bridge.codes import INPUT, NotPlainData
from tests.cognitive_bridge import test_followup4_repair2_inventory as repair2

PACKAGE = Path(snapshot.__file__).resolve().parent
#: The functions that copy, round-trip or encode a caller's value, and the block's one read of its arguments.
COPIERS = {"snapshot", "fresh", "plain", "text", "_prompt_value", "canonical_json", "_arguments", "_walk"}


# --- 1. Entries by what reaches a copier ----------------------------------------------------------------------------

def reaching() -> set[tuple[str, str]]:
    """Every (entry, parameter) of the public API whose parameter is handed to a copier, from the code: each public
    function, and each public class's ``__init__`` and public methods with source, no class left out."""
    found = set()

    def scan(name, function):
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        except (OSError, TypeError):
            return
        fn = tree.body[0]
        parameters = {arg.arg for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]} - {"self", "cls"}
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            if isinstance(call.func, ast.Name) and call.func.id in COPIERS and call.args:
                for node in ast.walk(call.args[0]):
                    if isinstance(node, ast.Name) and node.id in parameters:
                        found.add((name, node.id))

    for name in bridge.__all__:
        obj = getattr(bridge, name)
        if inspect.isfunction(obj):
            scan(name, obj)
        elif inspect.isclass(obj):
            for attr, member in vars(obj).items():
                member = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
                if (attr == "__init__" or not attr.startswith("_")) and inspect.isfunction(member):
                    scan(f"{name}.{attr}", member)
    return found


#: The block's entries: its arguments are the MCP door's plain JSON, which no annotation of a record type names.
BLOCK = {("CognitiveBridgeBlock.infer", "data"), ("CognitiveBridgeBlock.screen_raw", "raw")}

#: Parameters that reach a copier with no declared bound handed to it: each is bounded by the walker's own budget
#: (100,000 reaches, 4 MB) and depth, and, where its entry has a cap of its own for it (a question's length, a
#: surrogate's rows), by that entry's check before the copy, which repair 1's inventory lists. A new parameter must
#: be classified before this passes.
BUDGET_ONLY: set[tuple[str, str]] = {
    ('AdaptationPromotionGate.__init__', 'alpha'),
    ('AdaptationPromotionGate.__init__', 'max_evaluations'),
    ('AdaptationPromotionGate.__init__', 'min_cases'),
    ('AdaptationPromotionGate.__init__', 'min_discordant'),
    ('AdaptationPromotionGate.assess', 'change'),
    ('AdaptationPromotionGate.assess', 'controls'),
    ('AdaptationPromotionGate.assess', 'detected_failures'),
    ('AdaptationPromotionGate.assess', 'pairs'),
    ('BayesianUpdater.update', 'positive'),
    ('BayesianUpdater.update', 'probe'),
    ('CandidateDossier.from_payload', 'payload'),
    ('CognitiveAgent.__init__', 'allow'),
    ('CognitiveAgent.__init__', 'tools'),
    ('CognitiveAgent.run', 'budget'),
    ('CognitiveAgent.run', 'max_steps'),
    ('CognitiveAgent.run', 'objective'),
    ('CognitiveAssuranceOrchestrator.assess', 'artifacts'),
    ('CognitiveAssuranceOrchestrator.assess', 'attestation'),
    ('CognitiveAssuranceOrchestrator.assess', 'candidate_actor'),
    ('CognitiveAssuranceOrchestrator.assess', 'documentation'),
    ('CognitiveAssuranceOrchestrator.assess', 'scoped_assertions'),
    ('CognitiveAssuranceOrchestrator.assess', 'standard'),
    ('CognitiveAssuranceOrchestrator.assess', 'verifiers'),
    ('CognitiveAssuranceOrchestrator.compile', 'context'),
    ('CognitiveAssuranceOrchestrator.compile', 'max_supplemental'),
    ('CognitiveAssuranceOrchestrator.compile', 'standard'),
    ('CognitiveAssuranceOrchestrator.execute', 'allow'),
    ('CognitiveAssuranceOrchestrator.execution_artifact', 'actor'),
    ('CognitiveAssuranceOrchestrator.execution_artifact', 'claim_id'),
    ('CompositionalFailureModel.map', 'findings'),
    ('CompositionalFailureModel.map', 'proposals'),
    ('CrossMethodConflictGate.reconcile', 'assertions'),
    ('ExplicitTree.explain_one', 'sample'),
    ('ExplicitTree.predict', 'samples'),
    ('InProcessAssuranceEvaluator.__init__', 'evaluator_id'),
    ('InProcessAssuranceEvaluator.evaluate', 'artifacts'),
    ('InProcessAssuranceEvaluator.evaluate', 'attestation'),
    ('InProcessAssuranceEvaluator.evaluate', 'candidate_actor'),
    ('InProcessAssuranceEvaluator.evaluate', 'documentation'),
    ('InProcessAssuranceEvaluator.evaluate', 'standard'),
    ('InProcessAssuranceEvaluator.evaluate', 'verifiers'),
    ('InProcessAssuranceEvaluator.review_scope', 'artifacts'),
    ('InProcessAssuranceEvaluator.review_scope', 'candidate_actor'),
    ('InProcessAssuranceEvaluator.review_scope', 'documentation'),
    ('InProcessAssuranceEvaluator.review_scope', 'standard'),
    ('InProcessAssuranceEvaluator.review_scope', 'verifiers'),
    ('InformationGainSelector.__init__', 'min_gain'),
    ('InformationGainSelector.__init__', 'novelty_weight'),
    ('InformationGainSelector.rank', 'budget'),
    ('InformationGainSelector.rank', 'probes'),
    ('LLMClaimCompiler.__init__', 'max_claims'),
    ('LLMClaimCompiler.draft', 'context'),
    ('LLMClaimCompiler.draft', 'question'),
    ('LLMIdeaGenerator.__init__', 'max_ideas'),
    ('LLMIdeaGenerator.generate', 'objective'),
    ('LLMIdeaGenerator.generate', 'observations'),
    ('LLMMetacognitiveCritic.__init__', 'max_items'),
    ('LLMMetacognitiveCritic.propose', 'evidence_summary'),
    ('LLMMetacognitiveCritic.propose', 'model_summary'),
    ('LLMMetacognitiveCritic.propose', 'objective'),
    ('LLMProposer.__init__', 'max_probes'),
    ('LLMProposer.__init__', 'tools'),
    ('LLMProposer.propose', 'history'),
    ('LLMProposer.propose', 'objective'),
    ('MLAcquisitionSelector.__init__', 'min_gain'),
    ('MLAcquisitionSelector.rank', 'budget'),
    ('MLAcquisitionSelector.rank', 'probes'),
    ('MetacognitiveMethodSelector.__init__', 'exploration_weight'),
    ('MetacognitiveMethodSelector.__init__', 'learn_from'),
    ('MetacognitiveMethodSelector.__init__', 'seed'),
    ('MetacognitiveMethodSelector.__init__', 'trials'),
    ('MetacognitiveMethodSelector.select', 'context'),
    ('MetacognitiveMethodSelector.select', 'max_optional'),
    ('MetacognitivePlanner.plan', 'context'),
    ('MixedMethodExecutor.__init__', 'allow'),
    ('StaticProposer.__init__', 'probes'),
    ('StaticProposer.propose', 'history'),
    ('StaticProposer.propose', 'objective'),
    ('detect_failure_modes', 'dossier'),
    ('distill_blackbox', 'X_holdout'),
    ('distill_blackbox', 'X_train'),
    ('distill_blackbox', 'feature_names'),
    ('distill_blackbox', 'max_depth'),
    ('distill_blackbox', 'min_samples_leaf'),
    ('distill_blackbox', 'seed'),
    ('distill_blackbox', 'task'),
    ('evaluated_trace_rows', 'history'),
    ('expected_information_gain', 'probe'),
    ('extract_tree', 'feature_names'),
    ('extract_tree', 'provenance'),
    ('sklearn_payload', 'estimator'),
    ('sklearn_payload', 'history'),
    ('sklearn_payload', 'seed'),
}


def test_every_parameter_that_reaches_a_copier_is_classified():
    bounded = set(repair2.CARRIES) | set(repair2.NOT_BOUNDED) | BLOCK
    found = reaching()
    assert BLOCK <= found, "the block's arguments reach the boundary's one read of them"
    assert found - bounded == BUDGET_ONLY, (
        f"unclassified: {sorted(found - bounded - BUDGET_ONLY)}; stale: {sorted(BUDGET_ONLY - found)}")
    # a bounded parameter is found by this rule too, except where a record reaches the copier inside an expression
    assert {entry for entry in repair2.CARRIES if entry not in found} <= {
        ("CompiledResearch.changed_since_compiled", "plan")}


def _claim(**changes):
    return {**dict(id="c", statement="s", evidence_needs=["formal"], models={"formal": {"formula": "A"}}), **changes}


def _block_plants():
    """One-over plants for the block's plain JSON: name -> (arguments, what is watched, its text, the refusal)."""
    formula = "A" * 250_001
    spare = "B" * 250_001
    claims = [_claim(id=str(i)) for i in range(21)]
    hypotheses = [{"id": str(i), "statement": "s", "prior": 1 / 33} for i in range(33)]
    return {
        "a needed model": ({"op": "plan_methods", "claims": [_claim(models={"formal": {"formula": formula}})]},
                           None, formula, "250 kB"),
        "a model no need names": ({"op": "plan_methods", "claims": [_claim(models={
            "formal": {"formula": "A"}, "graph": {"program": spare}})]}, None, spare, "250 kB"),
        "twenty-one claims": ({"op": "plan_methods", "claims": claims}, claims, None, "max_length bound of 20"),
        "thirty-three hypotheses": ({"op": "rank", "hypotheses": hypotheses}, hypotheses, None,
                                    "max_length bound of 32"),
    }


@pytest.mark.parametrize("route", ["infer", "screen_raw", "typed", "model_validate"])
@pytest.mark.parametrize("name", sorted(_block_plants()))
def test_the_blocks_one_over_arguments_are_refused_before_any_detachment_or_encoding(name, route, monkeypatch):
    raw, planted, text, refusal = _block_plants()[name]
    with repair2.observed(monkeypatch, planted, text) as seen:
        if route == "model_validate":
            with pytest.raises(ValueError, match=refusal):
                CognitiveInput.model_validate(raw)
        else:
            given = CognitiveInput.model_construct(**raw) if route == "typed" else raw
            out = getattr(CognitiveBridgeBlock(), "screen_raw" if route == "screen_raw" else "infer")(given).unwrap()
            assert out["code"] == INPUT and refusal in out["reason"]
    assert seen == [], f"{name} ({route}): {seen[0]} before its bound refused it"


def test_a_claim_and_a_compilation_read_one_model_rule():
    """Every model a claim carries is under the cap, needed or not, as every model a compilation is handed is."""
    spare = "B" * 250_001
    claim = ClaimIR("c", "s", ("formal",), {"formal": {"formula": "A"}, "graph": {"program": spare}})
    for copy in (snapshot.snapshot, lambda value: MethodRouter().plan([value])):
        with pytest.raises(ValueError, match="250 kB"):
            copy(claim)


# --- 2. Every read of an attribute mapping --------------------------------------------------------------------------

_OWN_TYPE = "the built-in type's own descriptors, read to avoid a lookup a metaclass can answer"
_DETACHED = "a copy the walker made (object.__new__ and its own writes): its attribute mapping is the bridge's"
_ESTIMATOR = ("a collaborator's object, by design: an estimator's attributes are its own code, and each value read "
              "from one goes to the boundary's reader (test_read_inventory)")
ATTRIBUTE_READS = {
    ("adaptation.py", "AdaptationPromotionGate.__init__", "getattr(self, name)"): "the gate's own attributes, which "
                                                                                   "its __init__ has just set",
    ("assurance.py", "DomainStandard.manifest", "getattr(c, f.name)"): "a Criterion of a DomainStandard the boundary "
                                                                       "made: " + _DETACHED,
    ("block.py", "_admit_input", "getattr(rule, bound, None)"): "pydantic's own field metadata, of the bridge's schema",
    ("block.py", "CognitiveBridgeBlock._held", "getattr(self, '_screened', None)"): "the block's own attribute",
    ("codes.py", "_type_mro", "type.__dict__"): _OWN_TYPE,
    ("codes.py", "_has_member", "type.__dict__"): _OWN_TYPE,
    ("codes.py", "_named.read", "type.__dict__"): _OWN_TYPE,
    ("snapshot.py", "_admit_array", "type.__dict__"): _OWN_TYPE,
    ("snapshot.py", "_walk", "type.__dict__"): _OWN_TYPE,
    ("snapshot.py", "_attributes", "type.__dict__"): _OWN_TYPE,
    ("snapshot.py", "_attributes", "object.__getattribute__(value, '__dict__')"): "the one reader of an instance's "
        "attribute mapping: an exact dict, its keys read as text by native iteration, before any lookup",
    ("snapshot.py", "_kinds", "cls.__dict__"): "a record class's own namespace (the bridge's class)",
    ("snapshot.py", "record", "cls.__dict__"): "a record class's own namespace, where the class is made",
    ("snapshot.py", "record.__post_init__", "getattr(bounded, field.name)"): _DETACHED,
    ("snapshot.py", "_renewed", "getattr(value, field.name)"): _DETACHED,
    ("snapshot.py", "view", "getattr(value, field.name)"): _DETACHED,
    ("xai.py", "_feature_names", "hasattr(estimator, 'tree_')"): _ESTIMATOR,
    ("xai.py", "_feature_names", "hasattr(estimator, 'n_features_in_')"): _ESTIMATOR,
    ("xai.py", "_feature_names", "getattr(estimator, 'n_outputs_', 1)"): _ESTIMATOR,
    ("xai.py", "extract_tree", "hasattr(estimator, 'classes_')"): _ESTIMATOR,
    ("xai.py", "extract_tree.item", "getattr(tree, name)"): _ESTIMATOR,
}


def attribute_reads() -> set[tuple[str, str, str]]:
    found = set()
    for path in sorted(PACKAGE.glob("*.py")):
        def visit(node, owner):
            for child in ast.iter_child_nodes(node):
                name = owner
                if isinstance(child, (ast.FunctionDef, ast.ClassDef)):
                    name = ".".join(filter(None, [owner, child.name]))
                if isinstance(child, ast.Call) and ast.unparse(child.func) in (
                        "getattr", "hasattr", "vars", "setattr", "object.__getattribute__"):
                    found.add((path.name, name, ast.unparse(child)))
                elif isinstance(child, ast.Attribute) and child.attr == "__dict__":
                    found.add((path.name, name, ast.unparse(child)))
                visit(child, name)
        visit(ast.parse(path.read_text(encoding="utf-8")), "")
    return found


def test_every_read_of_an_attribute_mapping_is_listed_with_why():
    found = attribute_reads()
    assert found == set(ATTRIBUTE_READS), (
        f"unlisted: {sorted(found - set(ATTRIBUTE_READS))}; stale: {sorted(set(ATTRIBUTE_READS) - found)}")
    assert all(ATTRIBUTE_READS.values())


class _Shown(dict):
    """A mapping that answers for itself: nothing of it may run."""
    ran: list = []

    def __len__(self):
        _Shown.ran.append("__len__")
        return 0

    def __iter__(self):
        _Shown.ran.append("__iter__")
        return iter(())

    def get(self, *args):
        _Shown.ran.append("get")

    def __getitem__(self, key):
        _Shown.ran.append("__getitem__")
        raise KeyError(key)

    def __contains__(self, key):
        _Shown.ran.append("__contains__")
        return False


def _records():
    from tests.cognitive_bridge.test_boundary_inventory import MADE
    return MADE


@pytest.mark.parametrize("name", sorted(_records()))
@pytest.mark.parametrize("how", ["subclass", "foreign-key", "missing"])
def test_every_record_type_is_read_by_the_one_reader_of_its_attribute_mapping(name, how):
    """Every record type (from the code), through snapshot and fresh: a mapping subclass, a foreign key shadowing its
    first field, and a missing field are each the boundary's refusal, and nothing of the caller's runs."""
    record = _records()[name]()
    fields = dict(object.__getattribute__(record, "__dict__"))
    first = next(iter(fields))
    seen = []
    _Shown.ran.clear()
    if how == "subclass":
        forged = _Shown(fields)
    elif how == "foreign-key":
        forged = {repair2._Key(first, seen): fields.pop(first), **fields}
    else:
        forged = {key: value for key, value in fields.items() if key != first}
    object.__setattr__(record, "__dict__", forged)
    seen.clear()
    for copy in (snapshot.snapshot, lambda value: snapshot.fresh([value])):
        with pytest.raises(NotPlainData):
            copy(record)
    assert seen == [] and _Shown.ran == []


# --- 3. The roots' own cases ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("make", [
    lambda: {f"h{i}": Hypothesis(f"h{i}", "s", .5) for i in range(33)},
    lambda: [[Hypothesis(f"a{i}", "s", .5) for i in range(20)], {"more": [Hypothesis(f"b{i}", "s", .5)
                                                                           for i in range(13)]}],
    lambda: {"a": [MethodVariant(f"v{i}", "formal", "SMT", "formal", "check_validity", 1) for i in range(60)],
             "b": tuple(MethodVariant(f"w{i}", "formal", "SMT", "formal", "check_validity", 1) for i in range(41))},
], ids=["a mapping", "a list and a nested mapping", "two collections in a mapping"])
def test_one_tally_counts_a_declared_type_across_the_whole_detachment(make):
    with pytest.raises(NotPlainData, match="32 competing hypotheses|at most 100"):
        snapshot.snapshot(make())


def test_the_tally_is_one_detachments_and_a_record_being_made_or_published_spends_none():
    hypotheses = [Hypothesis(str(i), "s", .5) for i in range(32)]
    for _ in range(3):  # three detachments, each within the cap
        assert len(snapshot.snapshot(hypotheses)) == 32
    plan = MethodPlan(tuple(ClaimIR(str(i), "s", ("formal",), {}) for i in range(21)), (), ())
    assert len(plan.claims) == 21  # made, as repair 2 ruled; every copy of it is refused
    with pytest.raises(NotPlainData, match="20 typed claims"):
        snapshot.fresh(plan)


def test_a_collection_is_counted_and_copied_from_one_take_of_it(monkeypatch):
    """Whatever the caller's list becomes while it is copied, what was counted is what is copied (a list, and a
    mapping handed with a bound)."""
    hypotheses = [Hypothesis(str(i), "s", 1 / 32) for i in range(32)]
    beliefs = {f"h{i}": 1 / 32 for i in range(32)}
    real = snapshot._walk
    state = {"done": False}

    def growing(value, *args, **kwargs):
        if not state["done"] and value is hypotheses[0]:
            state["done"] = True
            hypotheses.append(Hypothesis("late", "s", 0.0))
        elif not state["done"] and type(value) is str and value == "h0":
            state["done"] = True
            beliefs["late"] = 0.0
        return real(value, *args, **kwargs)

    monkeypatch.setattr(snapshot, "_walk", growing)
    assert len(snapshot.snapshot(hypotheses)) == 32 and state["done"]
    state["done"] = False
    assert len(snapshot.snapshot(beliefs, bound=bridge.engine._admit_hypotheses)) == 32 and state["done"]


def test_a_declared_size_is_measured_by_the_copy_pass_under_its_one_budget(monkeypatch):
    """Follow-up 5 replaced a bound that read each model again: the copy pass measures a declared size as it copies,
    so one model named by many keys, or one claim's models many times over, is refused within the one budget."""
    monkeypatch.setattr(snapshot, "MAX_ITEMS", 2000)
    model = {"formula": "A", "premises": ["A"] * 300}
    claim = ClaimIR("c", "s", ("formal",), {name: model for name in ("formal", "graph", "causal", "predictive")})
    reaches = [0]
    real = snapshot._walk

    def counting(*args, **kwargs):
        reaches[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(snapshot, "_walk", counting)
    for value, shape in (({f"v{i}": model for i in range(50)}, bridge.mixed_methods.MODELS), ([claim] * 20, None)):
        reaches[0] = 0
        with pytest.raises(NotPlainData, match="size exceeds"):
            snapshot.snapshot(value, shape=shape)
        assert reaches[0] <= snapshot.MAX_ITEMS + 1


def test_bytes_are_hashed_only_in_a_field_a_record_declares_as_bytes(monkeypatch):
    blob = b"\x01" * 64
    hashed = []
    real = snapshot.hashlib.sha256

    def observed(data=b"", *args, **kwargs):
        if data is blob:
            hashed.append("hashed")
        return real(data, *args, **kwargs)

    monkeypatch.setattr(snapshot.hashlib, "sha256", observed)
    for value in (blob, [blob], {"held": blob}, {"deep": [{"held": blob}]}):
        for read in (snapshot.view, snapshot.canonical_json, lambda v: snapshot._admit_json(v, cap=100, error="e")):
            try:
                read(value)
            except ValueError:
                pass
    assert hashed == [], "bytes outside a record's bytes field were hashed"
    artifact = EvidenceArtifact("e", blob, "report", "candidate", "s", "claim")
    assert snapshot.view(artifact)["content"]["length"] == 64


@pytest.mark.parametrize("code", ["T{<i:x:<d:y:}", "2i", "ii", "Zd", "(2,)i", ""])
def test_a_buffer_format_that_is_not_one_native_scalar_is_refused(code, monkeypatch):
    """The admission reads the buffer's item format (here through a stand-in view): only one native scalar, or one
    text or bytes item, is one value each."""
    class View:
        format, shape, nbytes = code, (1,), 8

        def release(self):
            pass

    monkeypatch.setattr(snapshot, "memoryview", lambda value: View(), raising=False)
    import array
    with pytest.raises(NotPlainData, match="not one native scalar"):
        snapshot._admit_array(array.array("b", [0]), where="a structured array")


@pytest.mark.parametrize("code", ["b", "B", "?", "d", "<f", "q", "O", "3w", "5s"])
def test_one_native_scalar_per_item_is_admitted(code, monkeypatch):
    class View:
        format, shape, nbytes = code, (1,), 8

        def release(self):
            pass

    monkeypatch.setattr(snapshot, "memoryview", lambda value: View(), raising=False)
    import array
    buffer, convert = snapshot._admit_array(array.array("b", [0]), where="an array")
    assert buffer.format == code and convert is array.array.tolist


def test_the_executor_answers_every_unreadable_plan_as_tampered():
    """A plan whose claim has no fields, a foreign key, or a mapping subclass: the executor answers, never raises."""
    for forged in ({}, {repair2._Key("id", []): "c"}, _Shown()):
        claim = ClaimIR("c", "s", ("formal",), {})
        object.__setattr__(claim, "__dict__", forged)
        plan = object.__new__(MethodPlan)
        object.__setattr__(plan, "__dict__", {"claims": (claim,), "steps": (), "missing": (),
                                              "translation_status": "candidate_unreviewed"})
        out = MixedMethodExecutor(None).execute(plan, approved=True)
        assert out["code"] == "G6_E_COGNITIVE_PLAN_TAMPERED"


if __name__ == "__main__":  # prints the parameters to classify in BUDGET_ONLY
    for row in sorted(reaching() - set(repair2.CARRIES) - set(repair2.NOT_BOUNDED) - BLOCK):
        sys.stdout.write(f"    {row!r},\n")
