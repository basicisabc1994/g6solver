# SPDX-License-Identifier: Apache-2.0
"""Review 2 of PR #8's fourth follow-up: three inventories, each listed from the code.

1. The copier is the admitter. Every public entry's parameter that carries a bounded record, collection or artifact
   (found from the entry's annotations, from the bounds the record types declare, and from every ``bound=`` or ``cap=``
   a copier is handed in the package) has a one-over case here; each must refuse before any detachment, round trip or
   encoding of what was planted is seen. An entry or a bound added without a case fails.
2. The converter that is measured is the one that runs: an instance attribute, a subclass method and a metaclass hook
   for every slot the array admission reads.
3. A raw mapping's keys pass the one text-key reader before anything looks one up: every schema field of every input
   model at every route, every bounded record field, and every keyed read in an admission function.
"""
import ast
import dataclasses
import inspect
import re
import sys
import typing
from array import array
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.cognitive_bridge import (
    BayesianUpdater, ClaimIR, CognitiveAgent, CognitiveAssuranceOrchestrator, CognitiveBridgeBlock, Criterion,
    DomainStandard, Hypothesis, InformationGainSelector, LLMProposer, MLAcquisitionSelector,
    MetacognitiveMethodSelector, MetacognitivePlanner, MethodPlan, MethodRouter, MethodVariant, MixedMethodExecutor,
    Probe, ProblemSignature, StaticProposer, ToolSpec, check_beliefs, expected_information_gain,
)
from g6.cognitive_bridge import block, mixed_methods, snapshot, xai
from g6.cognitive_bridge.codes import INPUT, NotPlainData
from tests.cognitive_bridge.test_boundary_inventory import _record_classes, entries

PACKAGE = Path(snapshot.__file__).resolve().parent
SOURCES = {path.name: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(PACKAGE.glob("*.py"))}


def _functions():
    """Every function in the package, with its module and qualified name."""
    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.ClassDef)):
                name = ".".join(filter(None, [owner, child.name]))
                if isinstance(child, ast.FunctionDef):
                    yield name, child
            yield from visit(child, name)
    for module, tree in SOURCES.items():
        for name, node in visit(tree, ""):
            yield module, name, node


# --- 1. The copier is the admitter ------------------------------------------------------------------------------------

@contextmanager
def observed(monkeypatch, planted, text=None):
    """What happened to ``planted`` before the refusal: each completed ``snapshot._walk`` of it (a detachment) and
    each ``json.dumps`` of a value that holds ``text`` (a round trip or an encoding)."""
    seen = []
    dumps = snapshot.json.dumps

    def holds(value):
        if type(value) is str:
            return value == text
        if type(value) is dict:
            return any(holds(item) for item in value.values())
        return type(value) is list and any(holds(item) for item in value)

    def encode(value, *args, **kwargs):
        if text is not None and holds(value):
            seen.append("encoded")
        return dumps(value, *args, **kwargs)

    def profile(frame, event, answer):
        if (event == "return" and answer is not None and frame.f_code is snapshot._walk.__code__
                and frame.f_locals["value"] is planted):
            seen.append("detached")

    monkeypatch.setattr(snapshot.json, "dumps", encode)
    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        yield seen
    finally:
        sys.setprofile(previous)


def test_the_probe_sees_an_admitted_detachment_and_an_admitted_encoding(monkeypatch):
    """The instrument is not blind: a collection at its bound is detached, and a model at its bound is encoded."""
    hypotheses = [Hypothesis(str(i), "s", 1 / 32) for i in range(32)]
    with observed(monkeypatch, hypotheses) as seen:
        check_beliefs(hypotheses)
    assert seen == ["detached"]
    formula = "A" * 249_000
    claims = [ClaimIR("c", "s", ("formal",), {"formal": {"formula": formula}})]
    with observed(monkeypatch, claims, formula) as seen:
        MethodRouter().plan(claims)
    assert "detached" in seen and "encoded" in seen


_CONTEXT = ProblemSignature("review", "logic", "validity", 1, 2)
_VARIANT = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
_STANDARD = DomainStandard("review", "1", "AU", "rubric", "a" * 64,
                           (Criterion("c", "formal obligation", "checker", "1:1", required_methods=("formal",)),))
_PROBE = Probe("p", "q", "read", {"A": True, "B": False}, .8)
_BELIEFS = {"A": .5, "B": .5}
_HYPOTHESES = [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)]


def _claim(**changes):
    fields = dict(id="claim", statement="A entails A", evidence_needs=("formal",),
                  models={"formal": {"formula": "A"}})
    return ClaimIR(**{**fields, **changes})


class _NoModel:
    def complete(self, *args, **kwargs):
        raise AssertionError("the model was called before the bound refused")


def _compiled(orchestrator):
    return orchestrator.compile(claim=_claim(models={}), context=_CONTEXT, standard=_STANDARD, variants=[_VARIANT],
                                variant_models={"v": {"formula": "A", "premises": ["A"]}})


def _with_plan(plan):
    """An orchestrator and a compilation of its own, edited to hold ``plan``."""
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=object())
    return orchestrator, dataclasses.replace(_compiled(orchestrator), executable_plan=plan)


def _compile(**given):
    inputs = dict(claim=_claim(models={}), context=_CONTEXT, standard=_STANDARD, variants=[_VARIANT],
                  variant_models={"v": {"formula": "A", "premises": ["A"]}})
    return CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**{**inputs, **given})


def _planned(**given):
    inputs = dict(claims=[_claim()], context=_CONTEXT, variants=[_VARIANT], required_methods=["formal"])
    inputs.update(given)
    return MetacognitivePlanner(MetacognitiveMethodSelector()).plan(
        inputs["claims"], inputs["context"], inputs["variants"], required_methods=inputs["required_methods"])


def _selected(**given):
    inputs = dict(variants=[_VARIANT], required_methods=["formal"])
    inputs.update(given)
    return MetacognitiveMethodSelector().select(_CONTEXT, inputs["variants"],
                                                required_methods=inputs["required_methods"])


def _assessed(compiled):
    orchestrator, compiled = compiled
    return orchestrator.assess(compiled, standard=_STANDARD, run_result={}, evaluator=None, verifiers={},
                               artifacts={}, documentation={}, candidate_actor="candidate")


#: How each entry is called with one parameter planted and every other argument valid.
CALLS = {
    ("check_beliefs", "hypotheses"): check_beliefs,
    ("StaticProposer.propose", "hypotheses"): lambda value: StaticProposer([]).propose("q", value, []),
    ("LLMProposer.propose", "hypotheses"): lambda value: LLMProposer(
        _NoModel(), {"read": ToolSpec("read", lambda q, a: None)}).propose("q", value, []),
    ("CognitiveAgent.run", "hypotheses"): lambda value: CognitiveAgent(StaticProposer([]), {}).run("q", value,
                                                                                                    budget=0),
    ("expected_information_gain", "beliefs"): lambda value: expected_information_gain(value, _PROBE),
    ("InformationGainSelector.rank", "beliefs"): lambda value: InformationGainSelector().rank(value, [_PROBE], 1),
    ("BayesianUpdater.update", "beliefs"): lambda value: BayesianUpdater().update(value, _PROBE, True),
    ("MLAcquisitionSelector.rank", "beliefs"): lambda value: MLAcquisitionSelector(None).rank(value, [_PROBE], 1),
    ("MethodRouter.plan", "claims"): lambda value: MethodRouter().plan(value),
    ("MetacognitivePlanner.plan", "claims"): lambda value: _planned(claims=value),
    ("MetacognitivePlanner.plan", "variants"): lambda value: _planned(variants=value),
    ("MetacognitivePlanner.plan", "required_methods"): lambda value: _planned(required_methods=value),
    ("MetacognitiveMethodSelector.select", "variants"): lambda value: _selected(variants=value),
    ("MetacognitiveMethodSelector.select", "required_methods"): lambda value: _selected(required_methods=value),
    ("CognitiveAssuranceOrchestrator.compile", "claim"): lambda value: _compile(claim=value),
    ("CognitiveAssuranceOrchestrator.compile", "variants"): lambda value: _compile(variants=value),
    ("CognitiveAssuranceOrchestrator.compile", "variant_models"): lambda value: _compile(variant_models=value),
    ("CognitiveAssuranceOrchestrator.execute", "compiled"): lambda value: value[0].execute(value[1], approved=True),
    ("CognitiveAssuranceOrchestrator.assess", "compiled"): _assessed,
    ("CognitiveAssuranceOrchestrator.execution_artifact", "run_result"): lambda value: (
        CognitiveAssuranceOrchestrator.execution_artifact(value, actor="producer", claim_id="c")),
    ("MixedMethodExecutor.execute", "plan"): lambda value: MixedMethodExecutor(None).execute(value, approved=True),
    ("CompiledResearch.changed_since_compiled", "plan"): lambda value: _compile().changed_since_compiled(value),
}


def _one_over_claims():
    """One-over plants for a claim: (what is planted, the text an encoding of it would hold, the refusal, the claim)."""
    formula = "A" * 250_001
    needs = ("formal",) * 6
    return {
        "id": (lambda c: c.id, None, "length", _claim(id="i" * 101)),
        "statement": (lambda c: c.statement, None, "length", _claim(statement="s" * 3001)),
        "evidence_needs": (lambda c: c.evidence_needs, None, "evidence needs", _claim(evidence_needs=needs)),
        "model": (lambda c: c.models, formula, "250 kB", _claim(models={"formal": {"formula": formula}})),
    }


def _plants(kind, wrap=lambda value: value):
    """One-over values of one bounded kind: name -> (the value handed in, the object whose detachment is watched, the
    text whose encoding is watched, the refusal's text)."""
    if kind == "hypotheses":
        many = [Hypothesis(str(i), "s", 1 / 33) for i in range(33)]
        return {"thirty-three": (many, many, None, "32 competing hypotheses")}
    if kind == "beliefs":
        many = {f"h{i}": 1 / 33 for i in range(33)}
        return {"thirty-three": (many, many, None, "32 competing hypotheses")}
    if kind == "variants":
        many = [MethodVariant(f"v{i}", "formal", "SMT", "formal", "check_validity", 1) for i in range(101)]
        return {"one hundred and one": (many, many, None, "at most 100")}
    if kind == "obligations":
        many = ["formal"] * 6
        return {"six": (many, many, None, "at most 5 obligations")}
    if kind == "models":
        formula = "A" * 250_001
        models = {"v": {"formula": formula}}
        return {"an oversized model": (models, models, formula, "250 kB")}
    if kind == "artifact":
        data = "a" * 2_000_001
        run = {"data": data}
        return {"an oversized run": (run, None, data, "artifact exceeds 2MB")}
    assert kind == "claims"
    found = {}
    held = wrap(tuple(_claim(id=str(i)) for i in range(21)))
    found["twenty-one"] = (held, _held_claims(held), None, "20 typed claims")
    for name, (planted, text, refusal, claim) in _one_over_claims().items():
        held = wrap((claim,))
        found[name] = (held, planted(_held_claims(held)[0]), text, refusal)
    return found


def _held_claims(held):
    """The collection of claims inside what is handed in, as that value holds it now (a record made of a plan holds
    its own copy of the tuple and of each claim, so the plant watched is the one the entry will meet)."""
    if type(held) is ClaimIR:
        return (held,)
    if type(held) is MethodPlan:
        return held.claims
    if type(held) is tuple and type(held[0]) is CognitiveAssuranceOrchestrator:
        return held[1].executable_plan.claims
    return held


def _plan(claims):
    return MethodPlan(tuple(claims), (), ())


def _one(claims):
    return claims[0]


#: What each bounded parameter carries, and how a plant of it is handed in: the kind of :func:`_plants`, and the
#: wrapping of a collection of claims into what the parameter takes.
CARRIES = {
    ("check_beliefs", "hypotheses"): ("hypotheses", None),
    ("StaticProposer.propose", "hypotheses"): ("hypotheses", None),
    ("LLMProposer.propose", "hypotheses"): ("hypotheses", None),
    ("CognitiveAgent.run", "hypotheses"): ("hypotheses", None),
    ("expected_information_gain", "beliefs"): ("beliefs", None),
    ("InformationGainSelector.rank", "beliefs"): ("beliefs", None),
    ("BayesianUpdater.update", "beliefs"): ("beliefs", None),
    ("MLAcquisitionSelector.rank", "beliefs"): ("beliefs", None),
    ("MethodRouter.plan", "claims"): ("claims", list),
    ("MetacognitivePlanner.plan", "claims"): ("claims", list),
    ("MetacognitivePlanner.plan", "variants"): ("variants", None),
    ("MetacognitivePlanner.plan", "required_methods"): ("obligations", None),
    ("MetacognitiveMethodSelector.select", "variants"): ("variants", None),
    ("MetacognitiveMethodSelector.select", "required_methods"): ("obligations", None),
    ("CognitiveAssuranceOrchestrator.compile", "claim"): ("claims", _one),
    ("CognitiveAssuranceOrchestrator.compile", "variants"): ("variants", None),
    ("CognitiveAssuranceOrchestrator.compile", "variant_models"): ("models", None),
    ("CognitiveAssuranceOrchestrator.execute", "compiled"): ("claims", lambda claims: _with_plan(_plan(claims))),
    ("CognitiveAssuranceOrchestrator.assess", "compiled"): ("claims", lambda claims: _with_plan(_plan(claims))),
    ("CognitiveAssuranceOrchestrator.execution_artifact", "run_result"): ("artifact", None),
    ("MixedMethodExecutor.execute", "plan"): ("claims", _plan),
    ("CompiledResearch.changed_since_compiled", "plan"): ("claims", _plan),
}


def _cases():
    for (entry, parameter), (kind, wrap) in CARRIES.items():
        for name in (_plants(kind, wrap) if wrap is not None else _plants(kind)):
            if wrap is _one and name == "twenty-one":
                continue  # one claim is not a collection of them
            yield entry, parameter, kind, name


def _holders() -> set[str]:
    """Every record type that declares a bound for the copier (``snapshot.bounded``) or holds one that does, by its
    fields' declared kinds, from the code."""
    classes = _record_classes()
    found = {name for name, cls in classes.items() if snapshot._declared(cls) is not None}
    grew = True
    while grew:
        grew = False
        for name, cls in classes.items():
            held = {kind[1].__name__ for kind in snapshot._kinds(cls).values() if kind[0] in ("record", "records")}
            if name not in found and held & found:
                found.add(name)
                grew = True
    return found


def _admitting_calls():
    """Every copier call in the package that is handed a declared bound (``bound=`` or ``cap=``), from the code: the
    function it is in, the value it copies, and the bound."""
    for module, name, node in _functions():
        if module == "snapshot.py":
            continue  # the copiers themselves, handing their own bound on
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            for keyword in call.keywords:
                if keyword.arg in ("bound", "cap", "shape") and call.args and isinstance(call.args[0], ast.Name):
                    yield name, call.args[0].id, keyword.arg, ast.unparse(keyword.value)


def required() -> set[tuple[str, str]]:
    """Every (entry, parameter) that carries something bounded, from the code: a parameter whose annotation names a
    record type that declares a bound or holds one; a parameter a copier is handed with a declared bound; and a
    parameter of the same name and annotation as one that is."""
    holders = _holders()
    listed = dict(entries())
    found = set()
    admitted = {(name, value) for name, value, _, _ in _admitting_calls()}
    for name, value in admitted:
        if name in listed and value in inspect.signature(listed[name]).parameters:
            found.add((name, value))
    signed = {(parameter.name, str(parameter.annotation))
              for name, value in found for parameter in [inspect.signature(listed[name]).parameters[value]]}
    for name, function in listed.items():
        for parameter in inspect.signature(function).parameters.values():
            names = set(re.findall(r"\w+", str(parameter.annotation)))
            if names & holders or (parameter.name, str(parameter.annotation)) in signed:
                found.add((name, parameter.name))
    return found


def test_the_bounded_types_are_the_ones_the_code_declares():
    declared = {entry[0].__name__ for entry in snapshot._BOUNDED.values()}
    called = {ast.unparse(call.args[0]) for tree in SOURCES.values() for call in ast.walk(tree)
              if isinstance(call, ast.Call) and ast.unparse(call.func) == "bounded"}
    assert declared == called == {"ClaimIR", "Hypothesis", "MethodVariant"}
    assert _holders() == {"ClaimIR", "Hypothesis", "MethodVariant", "MethodPlan", "CompiledResearch"}


#: A parameter with the name and annotation of a bounded one that carries no bounded value, with why.
NOT_BOUNDED = {
    ("CognitiveAssuranceOrchestrator.assess", "run_result"): (
        "compared with the orchestrator's own record of the run (its bytes) and never made into an artifact: the "
        "artifact's byte cap bounds execution_artifact's run_result, which is what fills an artifact"),
}


def test_every_bounded_parameter_of_every_entry_has_a_one_over_case():
    found = required() - set(NOT_BOUNDED)
    assert set(NOT_BOUNDED) <= required() and all(NOT_BOUNDED.values())
    assert found == set(CARRIES) == set(CALLS), (
        f"no case for {sorted(found - set(CARRIES))}; no such bounded parameter {sorted(set(CARRIES) - found)}")


def test_every_copy_of_a_same_named_parameter_is_handed_the_same_bound():
    """One bound per parameter name: every copier call that takes ``claims``, ``variants``, ``required_methods``,
    ``beliefs`` or ``variant_models`` from a caller is handed the bound the others are."""
    bounds: dict[str, set[str]] = {}
    for _, value, how, bound in _admitting_calls():
        if how == "bound":
            bounds.setdefault(value, set()).add(bound)
    assert all(len(found) == 1 for found in bounds.values()), bounds
    unbound = []
    for module, name, node in _functions():
        parameters = {arg.arg for arg in [*node.args.args, *node.args.kwonlyargs]}
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            if (isinstance(call.func, ast.Name) and call.func.id == "snapshot" and call.args
                    and isinstance(call.args[0], ast.Name) and call.args[0].id in bounds
                    and call.args[0].id in parameters
                    and not any(keyword.arg == "bound" for keyword in call.keywords)):
                unbound.append(f"{module}:{name}: {ast.unparse(call)}")
    # LLMProposer.propose does not apply the belief rule (one hypothesis is a valid prompt): the walker still reads
    # the declared cap of a collection of Hypothesis records there, which its case here plants.
    assert unbound == ["engine.py:LLMProposer.propose: snapshot(hypotheses, where='hypotheses')"]


@pytest.mark.parametrize("entry,parameter,kind,name", list(_cases()),
                         ids=[f"{e}:{p}:{n}" for e, p, _, n in _cases()])
def test_one_over_is_refused_before_any_detachment_round_trip_or_encoding(entry, parameter, kind, name, monkeypatch):
    kind, wrap = CARRIES[entry, parameter]
    value, planted, text, refusal = (_plants(kind, wrap) if wrap is not None else _plants(kind))[name]
    said = ""
    with observed(monkeypatch, planted, text) as seen:
        try:
            out = CALLS[entry, parameter](value)
        except ValueError as exc:
            said = str(exc)
        else:
            assert type(out) is dict and out.get("completion_state") == "blocked-escalated", (
                f"{entry}({parameter}): {name} was not refused")
            said = out["reason"]
    assert refusal in said, f"{entry}({parameter}): {name} refused for another reason: {said}"
    assert seen == [], f"{entry}({parameter}): {name} was {seen[0]} before its bound refused it"


@pytest.mark.parametrize("make,call", [
    (lambda: [Hypothesis(str(i), "s", 1 / 32) for i in range(32)], check_beliefs),
    (lambda: [_claim(id=str(i)) for i in range(20)], lambda value: MethodRouter().plan(value)),
    (lambda: [MethodVariant(f"v{i}", "formal", "SMT", "formal", "check_validity", 1) for i in range(100)],
     lambda value: _selected(variants=value)),
    (lambda: {"data": "a" * (2_000_000 - len('{"data":""}'))},
     lambda value: CognitiveAssuranceOrchestrator.execution_artifact(value, actor="producer", claim_id="c")),
], ids=["hypotheses", "claims", "variants", "artifact"])
def test_a_value_at_its_bound_is_taken(make, call):
    call(make())


def test_a_record_is_made_past_a_copy_bound_and_refused_where_it_is_copied():
    """The declared bounds are the copier's: making a record reads only its field caps, so a plan of 21 claims can be
    built, and every copy of it is refused."""
    plan = _plan(_claim(id=str(i)) for i in range(21))
    for copy in (snapshot.snapshot, snapshot.fresh, lambda value: snapshot.snapshot([value]),
                 lambda value: snapshot.snapshot({"plan": value})):
        with pytest.raises(ValueError, match="20 typed claims"):
            copy(plan)


def test_a_mixed_collection_counts_each_declared_type_on_its_own():
    held = [Hypothesis(str(i), "s", .5) for i in range(32)] + [_claim(id=str(i)) for i in range(20)]
    assert len(snapshot.snapshot(held)) == 52
    with pytest.raises(ValueError, match="32 competing hypotheses"):
        snapshot.snapshot(held + [Hypothesis("one more", "s", .5)])


# --- 2. The converter that is measured is the one that runs ---------------------------------------------------------

def _admitted_slots() -> set[str]:
    """The slots the array admission reads, from its source: the names it resolves in the value's MRO."""
    fn = next(node for module, name, node in _functions() if (module, name) == ("snapshot.py", "_admit_array"))
    pending = next(n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and ast.unparse(n.targets[0]) == "pending")
    return set(ast.literal_eval(pending))


def test_the_only_conversion_is_by_the_admitted_slot():
    """No function in the package looks ``tolist`` up on a value: the one conversion (``xai._native``) calls what the
    admission handed back, on the value."""
    assert _admitted_slots() == {"tolist", "__getattribute__", "__buffer__"}
    lookups = [f"{module}:{name}" for module, name, node in _functions() for n in ast.walk(node)
               if (isinstance(n, ast.Attribute) and n.attr == "tolist")
               or (isinstance(n, ast.Call) and ast.unparse(n.func) == "getattr" and len(n.args) > 1
                   and isinstance(n.args[1], ast.Constant) and n.args[1].value == "tolist")]
    assert lookups == []
    callers = [f"{module}:{name}" for module, name, node in _functions() for n in ast.walk(node)
               if isinstance(n, ast.Call) and ast.unparse(n.func) == "_admit_array"]
    assert callers == ["xai.py:_native"]
    native = next(node for module, name, node in _functions() if (module, name) == ("xai.py", "_native"))
    assert "buffer, tolist = _admit_array(value, where=where)" in ast.unparse(native)
    assert "plain(tolist(value), where=where)" in ast.unparse(native)


def _hooked(slot, how, calls):
    """A one-byte native array whose ``slot`` is shadowed ``how``, each shadow recording that it ran."""
    def expand(*args, **kwargs):
        calls.append(f"{how}:{slot}")
        return ["x"] * (snapshot.MAX_ITEMS + 1)

    if how == "instance":
        class Hooked(array):
            pass
        value = Hooked("b", [0])
        setattr(value, slot, expand)
        return value
    if how == "subclass":
        def attribute(self, name):
            if name == "tolist":
                return expand
            return array.__getattribute__(self, name)
        body = {"tolist": lambda self: expand(), "__getattribute__": attribute,
                "__buffer__": lambda self, flags: expand() and memoryview(b"")}[slot]
        return type("Hooked", (array,), {slot: body})("b", [0])
    if how == "metaclass":
        class Meta(type):
            def __getattribute__(cls, name):
                if name == slot:
                    return expand
                return type.__getattribute__(cls, name)
        return Meta("Hooked", (array,), {})("b", [0])
    assert how == "metaclass-mro"

    class Shadow:
        pass
    setattr(Shadow, slot, lambda self, *args: expand())

    class Order(type):
        def mro(cls):
            return [cls, Shadow, array, object]
    return Order("Hooked", (array,), {})("b", [0])


@pytest.mark.parametrize("how", ["instance", "subclass", "metaclass", "metaclass-mro"])
@pytest.mark.parametrize("slot", sorted(_admitted_slots()))
def test_no_shadow_of_an_admitted_slot_runs(slot, how):
    calls = []
    value = _hooked(slot, how, calls)
    try:
        out = xai._native(value, where="a hooked array")
    except NotPlainData:
        out = None
    assert calls == [], "a converter that was not the measured native slot ran"
    assert out in (None, [0])
    if how in ("instance", "metaclass"):
        assert out == [0], "an instance or metaclass shadow changes nothing: the native slot converts"


# --- 3. Raw keys pass the one text-key reader before any lookup -----------------------------------------------------

class _Key:
    """A key that is not text, hashing like the text it shadows and recording every comparison."""

    def __init__(self, name, seen):
        self.name, self.seen = name, seen

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        self.seen.append(other)
        return False


def test_the_key_reader_spends_the_visit_budget_before_it_reads_a_key(monkeypatch):
    monkeypatch.setattr(snapshot, "MAX_ITEMS", 8)
    seen = []
    raw = {_Key(str(index), seen): None for index in range(5)}
    seen.clear()
    with pytest.raises(NotPlainData, match="size"):
        snapshot._text_keys(raw, "a raw mapping")
    assert seen == []
    with pytest.raises(NotPlainData, match="key is not text"):
        snapshot._text_keys({_Key("a", seen): None}, "a raw mapping")
    assert seen == []


def _slot(cls):
    """Where a nested input model stands in the block's arguments, from the schema."""
    for name, info in block.CognitiveInput.model_fields.items():
        hints = (info.annotation, *typing.get_args(info.annotation))
        nested = [arg for hint in hints for arg in (hint, *typing.get_args(hint))]
        if any(hint is cls for hint in nested):
            return name, typing.get_origin(info.annotation) is list
    raise AssertionError(f"{cls.__name__} is not in the block's schema")


def _model_fields():
    for cls in (block.CognitiveInput, *block._NESTED):
        for name in cls.model_fields:
            yield cls, name


@pytest.mark.parametrize("route", ["infer", "screen_raw", "model_validate", "constructed", "misplaced"])
@pytest.mark.parametrize("cls,name", list(_model_fields()),
                         ids=[f"{cls.__name__}.{name}" for cls, name in _model_fields()])
def test_a_foreign_key_shadowing_any_schema_field_is_refused_uncompared(cls, name, route):
    seen = []
    raw = {_Key(name, seen): None}
    if route in ("constructed", "misplaced"):
        # a typed model whose own fields were set past its constructor: its field mapping is read the same way
        given = cls.model_construct()
        object.__setattr__(given, "__dict__", raw)
        raw = given
    if route == "misplaced":
        # where the schema expects no model, only the walker meets it: it reads the field mapping's keys first
        raw = block.CognitiveInput.model_construct(probes=[{"arguments": {"held": raw}}])
    elif cls is not block.CognitiveInput and route != "model_validate":
        slot, many = _slot(cls)
        raw = {slot: [raw] if many else raw}
    seen.clear()
    if route == "model_validate":
        with pytest.raises(ValueError):
            cls.model_validate(raw)
    else:
        out = getattr(CognitiveBridgeBlock(), route if route in ("infer", "screen_raw") else "infer")(raw)
        assert out.unwrap()["code"] == INPUT
    assert seen == [], f"{cls.__name__}.{name} ({route}): a key was compared before the boundary read it"


def _bounded_fields():
    for name, cls in sorted(_record_classes().items()):
        for field in dataclasses.fields(cls):
            if "max_length" in field.metadata or "max_bytes" in field.metadata:
                yield cls, field.name


def test_the_bounded_record_fields_are_found():
    assert {(cls.__name__, name) for cls, name in _bounded_fields()} >= {
        ("EvidenceArtifact", "content"), ("EvaluatedTrial", "oracle_digest"), ("CritiqueCandidate", "failure_mode")}


@pytest.mark.parametrize("cls,name", list(_bounded_fields()),
                         ids=[f"{cls.__name__}.{name}" for cls, name in _bounded_fields()])
def test_a_foreign_key_shadowing_a_bounded_record_field_is_refused_uncompared(cls, name):
    seen = []
    raw = {_Key(name, seen): None}
    seen.clear()
    with pytest.raises(NotPlainData):
        snapshot._admit_fields(raw, cls)
    assert seen == []


@pytest.mark.parametrize("name", [field.name for field in dataclasses.fields(ClaimIR)])
def test_a_foreign_key_shadowing_a_claim_field_is_refused_uncompared(name):
    seen = []
    raw = {_Key(name, seen): None}
    seen.clear()
    with pytest.raises(NotPlainData):
        mixed_methods._admit_claim(raw)
    assert seen == []


@pytest.mark.parametrize("copy", [lambda claim: MethodRouter().plan([claim]), snapshot.snapshot, snapshot.fresh],
                         ids=["router", "snapshot", "fresh"])
def test_a_claim_whose_models_were_replaced_past_its_boundary_is_refused_uncompared(copy):
    seen = []
    claim = _claim()
    object.__setattr__(claim, "models", {_Key("formal", seen): {"formula": "A"}})
    seen.clear()
    with pytest.raises(NotPlainData):
        copy(claim)
    assert seen == []


#: A keyed read in an admission function whose mapping is not a caller's raw one, with why. Every other keyed read
#: (``in``, a subscript, ``.get``) in an admission function must follow ``_text_keys`` of that mapping there.
_PARSED = "json.loads made it (snapshot.json_reply hands the reader its parsed reply): every key is text"
OWN_KEYS: dict[tuple[str, str, str], str] = {
    ("adaptation.py", "_admitted", "declared"): "the gate's own set of file identities; a path's admission, not a "
                                                "mapping's",
    ("critique.py", "LLMMetacognitiveCritic._admit_reply", "doc"): _PARSED,
    ("engine.py", "LLMIdeaGenerator._admit_reply", "doc"): _PARSED,
    ("engine.py", "LLMIdeaGenerator._admit_reply", "item"): _PARSED,
    ("engine.py", "LLMProposer._admit_reply", "data"): _PARSED,
    ("engine.py", "LLMProposer._admit_reply", "item"): _PARSED,
    ("mixed_methods.py", "LLMClaimCompiler._admit_reply", "doc"): _PARSED,
    ("mixed_methods.py", "_admit_claim", "held"): "a claim's fields as the one reader of a record read them "
                                                  "(snapshot._record_fields), or the parsed item whose keys "
                                                  "_text_keys read on the line before",
    ("snapshot.py", "_admit_held", "tally"): "the detachment's own tally, keyed by a type's identity (an integer)",
    ("snapshot.py", "_admit_array", "members"): "a class's own namespace, read by the built-in descriptor; the line "
                                                "before refuses a key in it that is not text",
    ("snapshot.py", "_admit_array", "type.__dict__"): "the built-in type's own namespace",
    ("snapshot.py", "_admit_fields", "field.metadata"): "the bridge's own dataclass field metadata",
    ("snapshot.py", "_admit_held", "counts"): "the function's own tally, keyed by a type's identity (an integer)",
}
#: Subscripts that are annotations, not reads of a mapping.
_TYPING = {"Sequence", "Mapping", "Callable", "tuple", "dict", "list", "type", "set", "frozenset"}


def _keyed_reads():
    """Every keyed read in every admission function (a function named ``_admit...``, and each function it is handed
    to a copier as), from the code: the module, the function, the mapping read, and whether ``_text_keys`` read that
    mapping's keys earlier in the function."""
    for module, name, node in _functions():
        if not name.split(".")[-1].startswith("_admit"):
            continue
        read_first = {ast.unparse(call.args[0]): call.lineno for call in ast.walk(node)
                      if isinstance(call, ast.Call) and ast.unparse(call.func) == "_text_keys" and call.args}
        for n in ast.walk(node):
            if isinstance(n, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops):
                base = n.comparators[-1]
                if isinstance(base, (ast.Tuple, ast.Set, ast.Constant)):
                    continue  # a literal of the function's own
            elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load):
                base = n.value
                if isinstance(n.slice, ast.Constant) and type(n.slice.value) is int:
                    continue  # a position in a tuple, not a key
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get":
                base = n.func.value
            else:
                continue
            mapping = ast.unparse(base)
            if mapping in _TYPING:
                continue
            yield module, name, mapping, mapping in read_first and read_first[mapping] < n.lineno


def test_every_keyed_read_in_an_admission_function_follows_the_text_key_reader():
    unread = sorted({(module, name, mapping) for module, name, mapping, read in _keyed_reads() if not read})
    assert unread == sorted(OWN_KEYS), (
        f"unlisted keyed reads: {sorted(set(unread) - set(OWN_KEYS))}; stale: {sorted(set(OWN_KEYS) - set(unread))}")
    assert all(OWN_KEYS.values())
    assert {(module, name) for module, name, _, read in _keyed_reads() if read} >= {
        ("block.py", "_admit_input"), ("snapshot.py", "_admit_fields")}


def test_the_walker_reads_every_key_by_the_one_reader():
    """The walker's two key reads (the outer protocol mapping, and every nested object) are :func:`snapshot._text_key`,
    and nothing in the walker tests a key's kind another way."""
    walk = next(node for module, name, node in _functions() if (module, name) == ("snapshot.py", "_walk"))
    source = ast.unparse(walk)
    assert source.count("_text_key(_walk(key,") == 2
    assert "key is not text" not in source


def test_a_reply_item_is_admitted_by_the_claim_bounds_before_it_is_copied():
    """The compiler's reply reader hands each parsed claim to the claim's own bounds (finding 3's root): the same
    function the walker reads for a ClaimIR."""
    assert snapshot._declared(ClaimIR)[2] is mixed_methods._admit_claim
    reply = next(node for module, name, node in _functions()
                 if (module, name) == ("mixed_methods.py", "LLMClaimCompiler._admit_reply"))
    assert "_admit_claim(item)" in ast.unparse(reply)
