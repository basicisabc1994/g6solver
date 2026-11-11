# SPDX-License-Identifier: Apache-2.0
"""The class one_fact_two_rules, removed at its root for conversions (review 2 of PR #8's second follow-up, findings 1
and 3): the MCP block's models turned a flag and numeric text into a prior before the belief rule read it, and
``codes.refusal`` made its reason text with ``str()`` and read a supplied card through a lax ``WarningCard``, both before
the boundary saw the value. Now every value from a caller or a collaborator is read by the boundary first, raw, and then
by its rule, and nothing converts it from another kind: the block's arguments go through ``snapshot.plain`` (its raw
screen at G6's door, and ``infer`` for a Python caller) and every model in the package is strict; text is read by
``snapshot.text``, a number by ``snapshot.number``; a card through ``plain``, then a strict ``WarningCard``; a record's
field by the kind its annotation declares (a number, an integer, a flag).

This lists from the code every pydantic model and every model validation in the package; every ``str()``,
``float()``, ``int()`` and ``bool()`` call, and every other conversion (``bytes()``, ``complex()``, ``.item()``,
``.tolist()``, ``.decode()``, ``.encode()``, ``json.loads``, ``struct.pack``) on a value derived from a parameter of the
function it is in; every record field that declares a number, an integer or a flag; and every public parameter that
declares one. Each conversion is the boundary itself, or reads only the bridge's own value, or comes after a rule (or
an estimator's value made Python's own and handed straight to the boundary); each is named here with which. Then a
flag, numeric text, null, NaN, a set and bytes are planted at each place a caller's value reaches, and each is refused
unless it is that place's own kind. Review 3 of PR #8's second follow-up made ``xai._native`` the one reader of an
estimator's values (``tolist``, then ``snapshot.plain``), so the places an estimator's name or class label enters are
planted in ``test_read_inventory.py``, each with its own kind."""
from __future__ import annotations

import ast
import importlib.util
import inspect
import math
import sys
import textwrap
import types
import typing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from g6.cognitive_bridge import (
    AdaptationPromotionGate, BayesianUpdater, CandidateDossier, ClaimIR, CognitiveAgent, CognitiveAssuranceOrchestrator,
    Criterion, DomainStandard, EvidenceArtifact, ExplicitTree, Hypothesis, InformationGainSelector, LLMClaimCompiler,
    LLMIdeaGenerator, LLMMetacognitiveCritic, LLMProposer, MetacognitiveMethodSelector, MethodVariant,
    MLAcquisitionSelector, MixedMethodExecutor, MethodRouter, Observation, Probe, ProblemSignature, StaticProposer,
    ToolSpec, TreeNode, codes, distill_blackbox, evaluated_trace_rows, expected_information_gain, extract_tree,
    sklearn_payload,
)
from g6.cognitive_bridge import block as block_module
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.cognitive_bridge.snapshot import NotPlainData, _kinds

PACKAGE = Path(codes.__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_boundary", Path(__file__).with_name("test_boundary_inventory.py"))
_boundary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_boundary)
MADE, _record_classes, entries = _boundary.MADE, _boundary._record_classes, _boundary.entries

#: What is planted at each place: a flag, numeric text, null, a number that is not finite, a set and bytes.
PLANTED = {"flag": lambda: True, "numeric-text": lambda: "0.5", "null": lambda: None, "nan": lambda: math.nan,
           "set": lambda: {"x"}, "bytes": lambda: b"x"}


def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE.glob("*.py"))]


def _functions(tree):
    """Every function in a module with its dotted name (``Class.method``, ``f.inner``)."""
    found = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _own_nodes(fn):
    """The nodes of ``fn`` itself, not of a function nested in it."""
    nested = {id(n) for _, inner in _functions(fn) for n in ast.walk(inner)}
    return [n for n in ast.walk(fn) if id(n) not in nested]


def _refused(call) -> None:
    """``call`` refuses what it was handed: it raises ``ValueError`` (``NotPlainData`` among them, or a strict model's
    ``ValidationError``), or it answers a ``blocked-escalated`` result (the block's, inside G6's ``Result``)."""
    try:
        out = call()
    except ValueError:
        return
    out = out.value if hasattr(out, "is_fail") else out
    assert isinstance(out, dict) and out.get("completion_state") == "blocked-escalated", f"taken: {out!r}"[:400]


# --- 1. Every model, and every model validation, reads what the boundary made, strictly --------------------------------

def models() -> set[tuple[str, str]]:
    """Every pydantic model in the package, from the code: a class with ``BaseModel`` among its bases."""
    return {(module, cls.name) for module, tree in _modules() for cls in ast.walk(tree)
            if isinstance(cls, ast.ClassDef) and "BaseModel" in [ast.unparse(b).split(".")[-1] for b in cls.bases]}


EXPECTED_MODELS = {("block.py", name) for name in ("HypothesisInput", "ProbeInput", "ClaimInput", "ProblemSignatureInput",
                                                    "MethodVariantInput", "CognitiveInput")}
#: The boundary's conversion, by the names a validation's input may be made by. ``_held`` hands ``infer`` the read
#: ``screen_raw`` took through ``_arguments`` at G6's door, once (``test_read_inventory.py`` checks it holds nothing
#: else).
BOUNDARY = {"plain", "_plain", "_arguments", "snapshot", "_held"}


def test_the_models_are_listed_from_the_code():
    assert models() == EXPECTED_MODELS


@pytest.mark.parametrize("module,name", sorted(EXPECTED_MODELS))
def test_every_model_is_strict_and_closed_and_no_field_relaxes_it(module, name):
    model = getattr(block_module, name)
    assert model.model_config.get("strict") is True and model.model_config.get("extra") == "forbid", name
    relaxed = [field for field, info in model.model_fields.items()
               if any(getattr(m, "strict", None) is False for m in info.metadata)]
    assert relaxed == [], f"{name} relaxes {relaxed}"


def validations():
    """Every model validation in the package: (module, dotted function name, the call node, the function node)."""
    found = []
    for module, tree in _modules():
        for name, fn in _functions(tree):
            for node in _own_nodes(fn):
                if isinstance(node, ast.Call) and (
                        (isinstance(node.func, ast.Attribute) and node.func.attr in (
                            "model_validate", "model_validate_json", "validate_python", "validate_json"))
                        or (isinstance(node.func, ast.Name) and node.func.id == "TypeAdapter")):
                    found.append((module, name, node, fn))
    return found


EXPECTED_VALIDATIONS = {
    ("block.py", "CognitiveBridgeBlock.infer", "CognitiveInput.model_validate(_arguments(data))"),
    ("block.py", "CognitiveBridgeBlock.infer", "CognitiveInput.model_validate(screened)"),
    ("block.py", "_unchanged", "CognitiveInput.model_validate(_arguments(given))"),
    ("block.py", "_unchanged", "CognitiveInput.model_validate(_arguments(held))"),
    ("codes.py", "_read_card", "WarningCard.model_validate(taken, strict=True)")}


def _made_by(fn, node, names) -> bool:
    """Whether ``node`` is what one of ``names`` made: a call to one, or a name every assignment in ``fn`` binds to
    one."""
    if isinstance(node, ast.Call):
        func = node.func
        return (func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")) in names
    if isinstance(node, ast.Name):
        bound = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets)]
        return bool(bound) and all(_made_by(fn, value, names) for value in bound)
    return False


def test_the_validations_are_listed_from_the_code():
    assert {(m, n, ast.unparse(c)) for m, n, c, _ in validations()} == EXPECTED_VALIDATIONS


def test_every_validation_reads_what_the_boundary_made_and_reads_it_strictly():
    for module, name, call, fn in validations():
        assert call.args and _made_by(fn, call.args[0], BOUNDARY), \
            f"{module}:{name} validates a value the boundary did not read first: {ast.unparse(call)}"
        model = ast.unparse(call.func.value) if isinstance(call.func, ast.Attribute) else ""
        strict = any(k.arg == "strict" and isinstance(k.value, ast.Constant) and k.value.value is True
                     for k in call.keywords) or (module, model) in EXPECTED_MODELS
        assert strict, f"{module}:{name} validates with a model that converts: {ast.unparse(call)}"


# --- 1b. The block's every field, planted through infer and through G6's registry ------------------------------------

HYPOTHESES = [{"id": "a", "statement": "first hypothesis", "prior": 0.5},
              {"id": "b", "statement": "second hypothesis", "prior": 0.5}]
PROBES = [{"id": "p", "question": "measure", "tool": "read", "positive_if": {"a": True, "b": False},
           "assumed_match_probability": 0.8, "cost": 0, "novelty": 0.5, "arguments": {"k": "v"}}]
#: A request the block answers for each of its ops that reads data; together they reach every field of every model.
REQUESTS = {
    "update": {"op": "update", "hypotheses": HYPOTHESES, "probes": PROBES, "budget": 1, "probe_id": "p",
               "observed_positive": True},
    "plan_methods": {"op": "plan_methods", "claims": [{
        "id": "c", "statement": "A follows", "evidence_needs": ["formal"], "models": {"formal": {"formula": "A"}},
        "source_refs": ["s"], "origin": "caller"}]},
    "rank_methods": {"op": "rank_methods", "context": {
        "domain": "d", "uncertainty": "u", "claim_kind": "k", "risk": 1, "budget": 2, "available_evidence": ["e"]},
        "variants": [{"id": "v", "method": "formal", "representation": "SMT", "engine": "formal", "op": "check_validity",
                      "estimated_cost": 1, "expects": ["e"], "kind": "engine", "content_digest": ""}],
        "required_methods": ["formal"]},
}


def _model_of(hint):
    return next((a for a in (hint, *typing.get_args(hint)) if isinstance(a, type) and issubclass(a, BaseModel)), None)


def _walk(hint, value, path, seen):
    """Each place of the valid request ``value`` that the annotation ``hint`` reads: (path, hint)."""
    yield path, hint
    model = _model_of(hint)
    if model is not None and isinstance(value, dict):
        for name, info in model.model_fields.items():
            if name in value:
                seen.add((model.__name__, name))
                yield from _walk(info.annotation, value[name], path + (name,), seen)
        return
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        hint = next((a for a in args if a is not type(None)), Any)
        origin, args = typing.get_origin(hint), typing.get_args(hint)
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(args[0] if origin is list and args else Any, item, path + (index,), seen)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(args[1] if origin is dict and len(args) == 2 else Any, item, path + (key,), seen)


def _places():
    seen: set[tuple[str, str]] = set()
    out = [(op, path, hint) for op, request in REQUESTS.items()
           for path, hint in _walk(block_module.CognitiveInput, request, (), seen) if path]
    return out, seen


def _own(hint) -> set[str]:
    """The planted kinds that are a place's own kind, by its annotation: text takes numeric text, a flag a flag, null
    where the annotation allows it, and anything at all where the model reads any JSON."""
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if hint is Any:
        return {"flag", "numeric-text", "null"}
    if origin in (typing.Union, types.UnionType):
        return set().union(*(_own(a) for a in args if a is not type(None))) | (
            {"null"} if type(None) in args else set())
    if origin is Literal:
        return {"numeric-text"} if "0.5" in args else set()
    return {str: {"numeric-text"}, bool: {"flag"}}.get(hint, set())


def _planted(request, path, value):
    import copy

    out = copy.deepcopy(request)
    target = out
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return out


PLACES, _SEEN = _places()


def test_the_requests_reach_every_field_of_every_model_and_each_is_answered():
    every = {(model.__name__, name) for model in (getattr(block_module, n) for _, n in EXPECTED_MODELS)
             for name in model.model_fields}
    # op is the field each request is made by; the other unread fields are the defaults of an op that reads no data
    assert every - _SEEN - {("CognitiveInput", "op")} == set(), every - _SEEN
    for op, request in REQUESTS.items():
        out = CognitiveBridgeBlock().infer(request).unwrap()
        assert out["completion_state"] == "qualified-draft", (op, out)


_BLOCK_CASES = [(op, path, kind) for op, path, hint in PLACES for kind in sorted(PLANTED) if kind not in _own(hint)]


@pytest.mark.parametrize("op,path,kind", _BLOCK_CASES, ids=[f"{o}:{'.'.join(map(str, p))}:{k}" for o, p, k in
                                                            _BLOCK_CASES])
def test_the_block_refuses_what_is_not_a_places_kind_through_infer_and_through_the_registry(op, path, kind):
    request = _planted(REQUESTS[op], path, PLANTED[kind]())
    out = CognitiveBridgeBlock().infer(request).unwrap()
    assert out["completion_state"] == "blocked-escalated" and out["code"] == codes.INPUT, out
    if path == ("op",):
        return  # the registry takes the op as its name, not as a field
    from g6.core.registry import get_registry

    params = {k: v for k, v in request.items() if k != "op"}
    answer = get_registry().invoke("cognitive_bridge", op, params)
    if answer.__class__.__name__ == "Failure":
        assert str(answer.failure()).startswith("[G6_E_INPUT_VALIDATION]"), answer.failure()
    else:
        out = answer.unwrap()
        assert out["completion_state"] == "blocked-escalated" and out["code"] == codes.INPUT, out


def test_the_raw_screen_reads_what_the_models_never_see():
    # at G6's door, what is not JSON data is the block's own refusal before typed validation: a NaN prior, a set
    block = CognitiveBridgeBlock()
    for value in (math.nan, {"x"}, b"x"):
        out = block.screen_raw(_planted(REQUESTS["update"], ("hypotheses", 0, "prior"), value))
        assert out is not None and out.unwrap()["code"] == codes.INPUT
    assert block.screen_raw(REQUESTS["update"]) is None


# --- 2. Every conversion, from the code ------------------------------------------------------------------------------

#: The calls that make a value of one kind from another, by name, by method, and as module functions.
CONVERTERS = {"str", "float", "int", "bool", "bytes", "complex"}
METHODS = {"item", "tolist", "decode", "encode"}
MODULE_FUNCTIONS = {("json", "loads"), ("struct", "pack")}


def _tainted(fn) -> set[str]:
    """The names in ``fn`` derived from one of its parameters: the parameters, and every name bound (by assignment, a
    loop, a comprehension or ``with``) from an expression that reads one, until nothing new is."""
    a = fn.args
    found = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]} | {x.arg for x in (a.vararg, a.kwarg) if x}
    for lam in [n for n in ast.walk(fn) if isinstance(n, ast.Lambda)]:
        found |= {x.arg for x in [*lam.args.args, *lam.args.kwonlyargs]}

    def names(node):
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    changed = True
    while changed:
        changed = False
        for n in ast.walk(fn):
            pairs = []
            if isinstance(n, ast.Assign):
                pairs = [(t, n.value) for t in n.targets]
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and n.value is not None:
                pairs = [(n.target, n.value)]
            elif isinstance(n, (ast.For, ast.comprehension)):
                pairs = [(n.target, n.iter)]
            elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                pairs = [(n.optional_vars, n.context_expr)]
            for target, value in pairs:
                if names(value) & found and names(target) - found:
                    found |= names(target)
                    changed = True
    return found


def _converter(call) -> str:
    func = call.func
    if isinstance(func, ast.Name) and func.id in CONVERTERS:
        return func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and (func.value.id, func.attr) in MODULE_FUNCTIONS:
            return f"{func.value.id}.{func.attr}"
        if func.attr in METHODS:
            return func.attr
    return ""


def _argument(call):
    """What a conversion converts: what a method is called on, the value ``struct.pack`` packs (after its format), or
    the first argument."""
    if isinstance(call.func, ast.Attribute) and call.func.attr in METHODS:
        return call.func.value
    if _converter(call) == "struct.pack":
        return call.args[1] if len(call.args) > 1 else None
    return call.args[0] if call.args else None


def conversions():
    """Every conversion in the package on a value derived from a parameter of the function it is in:
    (module, dotted function name, converter, what it converts) -> (call, function)."""
    found = {}
    for module, tree in _modules():
        for name, fn in _functions(tree):
            taint = _tainted(fn)
            for call in [n for n in _own_nodes(fn) if isinstance(n, ast.Call)]:
                converter, argument = _converter(call), _argument(call)
                if converter and argument is not None and {
                        n.id for n in ast.walk(argument) if isinstance(n, ast.Name)} & taint:
                    found[(module, name, converter, ast.unparse(argument))] = (call, fn)
    return found


_DUMPS = "json.dumps(projected, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)"
#: Every conversion the code holds, compared whole, with how it stands to the boundary: ``boundary`` (the boundary's own
#: conversion), ``own`` (it reads only a value the bridge made), ``ruled`` (what it converts was read by a rule first:
#: the number or text rule, or an exact type test) or ``estimator`` (a fitted estimator's array or scalar made Python's
#: own values, handed straight to the boundary, ``snapshot.plain``, and read next by the rule for its kind).
EXPECTED_CONVERSIONS = {
    ("assurance.py", "CandidateDossier.__post_init__", "bool", "blockers"):
        ("ruled", "the payload's blockers, tested first to be an array (a tuple): bool reads whether it is empty"),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "bool", "positives"):
        ("own", "the list the evaluator built by replaying its checker's positive controls"),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "bool", "negatives"):
        ("own", "the list the evaluator built by replaying its checker's negative controls"),
    ("block.py", "CognitiveBridgeBlock._refused", "str", "exc"):
        ("own", "an exception the block caught: its text is the refusal's reason"),
    ("codes.py", "_claims_nothing", "bool", "ERROR_CODE_PATTERN.match(read['code'])"):
        ("own", "whether the code pattern matched the text a strictly read card holds"),
    ("engine.py", "_beliefs", "float", "v"):
        ("ruled", "a belief the number rule took (an int or a float): an integer belief, 0 or 1, read as its float"),
    ("epistemic.py", "assertions_sha256", "decode", "canonical_json(record_data(a))"):
        ("own", "the ASCII bytes the bridge's own canonical encoder wrote"),
    ("mixed_methods.py", "MixedMethodExecutor._run", "json.loads", "params"):
        ("own", "the canonical bytes the executor made of a checked step's parameters"),
    ("orchestration.py", "CompiledResearch.completion_state", "bool",
     "compilation_gaps(self.decision, self.executable_plan)"): ("own", "the list of gaps the bridge built"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator._executed", "json.loads", "record[2]"):
        ("own", "the run's canonical bytes this orchestrator stored when it ran it"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator._every_step_ran", "bool", "compiled.executable_plan.steps"):
        ("own", "a plan's tuple of step records, as its record holds it: bool reads whether it is empty"),
    ("quantitative.py", "evaluated_trace_rows", "float", "number(row[name], where=f'history[{index}].{name}')"):
        ("ruled", "a trace's feature or target, read by the number rule: an integer read as its float"),
    ("snapshot.py", "canonical_json", "encode", _DUMPS): ("boundary", "the encoder's own ASCII text, as bytes"),
    ("snapshot.py", "plain", "json.loads", "canonical_json(value)"):
        ("boundary", "the boundary's round trip through the bridge's canonical encoder"),
    ("snapshot.py", "_bytes", "bytes", "value"):
        ("boundary", "a field a record declares as bytes: its exact type (bytes, bytearray, memoryview) tested first"),
    ("snapshot.py", "json_reply", "json.loads", "reply"):
        ("boundary", "a model's reply, tested first to be text, read as JSON with no repeated key, then by plain"),
    ("xai.py", "_native", "tolist", "value"):
        ("estimator", "numpy's array or scalar made Python's own values, handed straight to the boundary "
                      "(snapshot.plain) in _native, the one reader of an estimator's values"),
    ("xai.py", "_float32", "struct.pack", "number(value, where='a tree input')"):
        ("ruled", "a tree input read by the number rule, as a float32"),
    ("xai.py", "extract_tree.visit", "float", "scores[0]"):
        ("ruled", "a regression leaf's value, read by the number rule (_numbers): an integer read as its float"),
    ("xai.py", "extract_tree.visit", "float", "_estimated(item('threshold', node_id), where=f'{at}: its threshold')"):
        ("ruled", "a split's threshold, read by the number rule: an integer read as its float"),
}
#: The rules a ruled conversion's value is read by first (``_numbers`` reads each item of a list by the number rule).
RULES = {"number", "text", "_estimated", "_numbers", "type", "isinstance"}


# Bounded encoder bytes are decoded only after its depth and size checks.
_ENCODED_BYTES = ("own", "the bounded canonical encoder's own bytes, decoded without changing their data")
EXPECTED_CONVERSIONS.update({
    ('critique.py', 'LLMMetacognitiveCritic.propose', 'decode',
     "canonical_json(payload, cap=25000, error='critique prompt cap exceeded')"): _ENCODED_BYTES,
    ('engine.py', 'LLMIdeaGenerator.generate', 'decode',
     "canonical_json({'objective': objective[:2000], 'observations': list(observations)[-6:], 'max_ideas': self.max_ideas})"): _ENCODED_BYTES,
    ('engine.py', 'LLMProposer.propose', 'decode',
     "canonical_json({'objective': objective[:2500], 'hypotheses': [{'id': h.id, 'statement': h.statement, 'prior': h.prior} for h in hypotheses], 'history': list(history)[-6:], 'allowed_tools': [{'name': t.name, 'cost': t.cost, 'assumed_match_probability': t.assumed_match_probability} for t in tools.values()], 'max_probes': self.max_probes})"): _ENCODED_BYTES,
    ('mixed_methods.py', 'LLMClaimCompiler.draft', 'decode',
     "canonical_json(payload, cap=15000, error='question and context exceed prompt cap')"): _ENCODED_BYTES,
    ('snapshot.py', 'record_data', 'json.loads',
     'canonical_json(value)'): _ENCODED_BYTES,
})


def test_the_conversions_are_listed_from_the_code():
    found = set(conversions())
    assert found == set(EXPECTED_CONVERSIONS), (sorted(found - set(EXPECTED_CONVERSIONS)),
                                                sorted(set(EXPECTED_CONVERSIONS) - found))


def _ruled(fn, call) -> bool:
    """Whether what ``call`` converts was read by a rule first: a rule's call inside it, a name bound from one (or
    iterating one), or an exact type test of a name it reads, before it."""
    argument = _argument(call)
    if any(isinstance(n, ast.Call) and getattr(n.func, "id", "") in RULES for n in ast.walk(argument)):
        return True
    read = {n.id for n in ast.walk(argument) if isinstance(n, ast.Name)}
    ruled: set[str] = set()
    for _ in range(4):
        for n in ast.walk(fn):
            pairs = ([(t, n.value) for t in n.targets] if isinstance(n, ast.Assign)
                     else [(n.target, n.iter)] if isinstance(n, (ast.For, ast.comprehension)) else [])
            for target, value in pairs:
                if any(isinstance(c, ast.Call) and getattr(c.func, "id", "") in RULES for c in ast.walk(value)) or (
                        {x.id for x in ast.walk(value) if isinstance(x, ast.Name)} & ruled):
                    ruled |= {x.id for x in ast.walk(target) if isinstance(x, ast.Name)}
    tested = {c.args[0].id for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") in
              ("type", "isinstance") and c.args and isinstance(c.args[0], ast.Name)
              and (c.lineno, c.col_offset) <= (call.lineno, call.col_offset)}
    return bool(read & (ruled | tested))


@pytest.mark.parametrize("key", sorted(k for k, v in EXPECTED_CONVERSIONS.items() if v[0] == "ruled"))
def test_every_ruled_conversion_converts_what_a_rule_read_first(key):
    call, fn = conversions()[key]
    assert _ruled(fn, call), f"{key}: what it converts is not read by a rule first"


def test_every_estimator_value_goes_straight_to_the_boundary():
    # numpy's array or scalar made Python's own values (tolist) is the boundary's own first argument (snapshot.plain),
    # in _native, the one reader of an estimator's values; test_read_inventory.py lists every read of an estimator, a
    # learner and a black box, and the rule each value is read by next
    found = [key for key, value in EXPECTED_CONVERSIONS.items() if value[0] == "estimator"]
    assert found, "no estimator conversion listed"
    for key in found:
        call, fn = conversions()[key]
        parent = {c: p for p in ast.walk(fn) for c in ast.iter_child_nodes(p)}
        up = parent[call]
        while isinstance(up, ast.IfExp):
            up = parent[up]
        assert isinstance(up, ast.Call) and getattr(up.func, "id", "") == "plain" and any(
            call in ast.walk(arg) for arg in up.args[:1]), f"{key[1]}: {ast.unparse(up)[:100]}"


# --- 2b. A flag, numeric text, null, NaN, a set and bytes planted at each place a caller's value reaches --------------

class _Estimator:
    """A fitted tree's arrays, as sklearn exposes them, with one array item planted."""

    def __init__(self, *, at: str = "", value: Any = None, regression: bool = True, label: Any = "yes"):
        tree = {"children_left": [1, -1, -1], "children_right": [2, -1, -1], "feature": [0, -2, -2],
                "threshold": [.5, -2., -2.], "value": [[[0.]], [[10.]], [[20.]]]}
        if not regression:
            tree["value"] = [[[1., 1.]], [[2., 0.]], [[0., 2.]]]
            self.classes_ = [label, "no"]
        if at == "value":
            tree["value"][1][0][0] = value
        elif at:
            tree[at][0] = value
        self.tree_ = SimpleNamespace(**tree)
        self.n_features_in_, self.n_outputs_ = 1, 1


ROWS = [[float(i), float(i % 3)] for i in range(20)]
TOOLS = {"read": ToolSpec("read", lambda question, arguments: Observation(True, "source"))}
HYPS = (Hypothesis("A", "a", .5), Hypothesis("B", "b", .5))
PROBE = Probe("p", "q", "read", {"A": True, "B": False}, .8)
HISTORY = [{"expected_information_bits": .5, "cost": 1, "novelty_preference": .1, "assumed_match_probability": .8,
            "measured_utility": .3, "evaluator_ref": "evaluator"}]


class _Model:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, *, max_tokens):
        return self.reply


def _payload(**changes):
    return {**dict(MADE["CandidateDossier"]().payload), **changes}


#: Each conversion a caller's (or a collaborator's) value reaches: its own kinds, and the places a value is planted at,
#: each a call that hands the planted value to the public entry it enters by.
PLANTS: dict[tuple[str, str, str, str], tuple[set[str], dict[str, Any]]] = {
    ("assurance.py", "CandidateDossier.__post_init__", "bool", "blockers"):
        (set(), {"a dossier's blockers": lambda v: CandidateDossier(_payload(blockers=v), "a" * 64)}),
    ("engine.py", "_beliefs", "float", "v"): (set(), {
        "a belief handed to the update": lambda v: BayesianUpdater().update({"A": v, "B": .5}, PROBE, True),
        "a belief handed to the ranking": lambda v: InformationGainSelector().rank({"A": v, "B": .5}, [PROBE], 1),
        "a belief handed to an information gain": lambda v: expected_information_gain({"A": v, "B": .5}, PROBE),
        "a hypothesis's prior": lambda v: CognitiveAgent(StaticProposer([]), TOOLS).run(
            "o", [Hypothesis("A", "a", v), Hypothesis("B", "b", .5)], budget=1)}),
    ("quantitative.py", "evaluated_trace_rows", "float", "number(row[name], where=f'history[{index}].{name}')"): (
        set(), {f"a trace's {name}": (lambda v, name=name: evaluated_trace_rows([{**HISTORY[0], name: v}]))
                for name in ("expected_information_bits", "cost", "novelty_preference", "assumed_match_probability",
                             "measured_utility")}
        | {"a payload's trace": lambda v: sklearn_payload([{**HISTORY[0], "cost": v}])}),
    ("snapshot.py", "_bytes", "bytes", "value"):
        ({"bytes"}, {"an artifact's content": lambda v: EvidenceArtifact("e", v, "report", "c", "s", "claim")}),
    ("snapshot.py", "json_reply", "json.loads", "reply"):
        (set(), {"a model's reply": lambda v: LLMProposer(_Model(v), TOOLS).propose("objective", HYPS, [])}),
    ("xai.py", "_float32", "struct.pack", "number(value, where='a tree input')"): (set(), {
        "a sample's feature": lambda v: ExplicitTree(("x",), TreeNode(value=1), "regression", "t").explain_one([v]),
        "a training row's feature": lambda v: distill_blackbox(None, [[v, 1.]] + ROWS[1:12], ROWS[12:], ["x", "y"])}),
    ("xai.py", "extract_tree.visit", "float", "_estimated(item('threshold', node_id), where=f'{at}: its threshold')"):
        (set(), {"a fitted threshold":lambda v: extract_tree(_Estimator(at="threshold", value=v), ["x"])}),
    ("xai.py", "extract_tree.visit", "float", "scores[0]"):
        (set(), {"a fitted leaf value": lambda v: extract_tree(_Estimator(at="value", value=v), ["x"])}),
    ("xai.py", "_native", "tolist", "value"): (set(), {
        f"a fitted {name}": (lambda v, name=name: extract_tree(_Estimator(at=name, value=v), ["x"]))
        for name in ("children_left", "children_right", "feature", "threshold")}),
}
#: The conversions not planted here, with why: those that read only the bridge's own value, and the boundary's own round
#: trip (``plain`` and its encoder), which every planting here reaches first.
NOT_PLANTED = {key: value[1] for key, value in EXPECTED_CONVERSIONS.items()
               if value[0] == "own" or key[:2] in (("snapshot.py", "canonical_json"), ("snapshot.py", "plain"))}


def test_every_conversion_a_callers_value_reaches_is_planted_here():
    reached = set(EXPECTED_CONVERSIONS) - set(NOT_PLANTED)
    assert set(PLANTS) == reached, (sorted(reached - set(PLANTS)), sorted(set(PLANTS) - reached))


_CONVERSION_CASES = [(key, place, kind) for key, (own, places) in sorted(PLANTS.items()) for place in sorted(places)
                     for kind in sorted(PLANTED) if kind not in own]


@pytest.mark.parametrize("key,place,kind", _CONVERSION_CASES,
                         ids=[f"{k[1]}:{k[2]}:{p}:{kind}" for k, p, kind in _CONVERSION_CASES])
def test_a_value_planted_at_each_conversion_is_refused_unless_it_is_that_places_kind(key, place, kind):
    _refused(lambda: PLANTS[key][1][place](PLANTED[kind]()))


@pytest.mark.parametrize("kind", ["flag", "numeric-text", "null"])
def test_a_class_label_of_any_json_kind_is_kept_as_that_kind(kind):
    label = PLANTED[kind]()
    tree = extract_tree(_Estimator(regression=False, label=label), ["x"])
    assert tree.root.left.value is label or tree.root.left.value == label and type(tree.root.left.value) is type(label)


# --- 3. Every record field that declares a number, an integer or a flag ----------------------------------------------

def leaf_fields():
    """Every field of every record type that declares a number, an integer or a flag (or one of them or null), from
    the code: (record, field) -> (kind, nullable)."""
    return {(name, field): kind for name, cls in _record_classes().items() for field, kind in _kinds(cls).items()
            if kind[0] in ("number", "integer", "flag")}


EXPECTED_LEAF_FIELDS = {
    ("ControlTrial", "correct_detection"): ("flag", False), ("DomainStandard", "risk"): ("integer", False),
    ("EvaluatedTrial", "actual_cost"): ("number", False), ("EvaluatedTrial", "critical_regression"): ("flag", False),
    ("EvaluatedTrial", "resolved"): ("flag", False), ("Hypothesis", "prior"): ("number", False),
    ("MethodVariant", "estimated_cost"): ("number", False), ("Observation", "measured_utility"): ("number", True),
    ("Observation", "positive"): ("flag", False), ("PairedTrial", "adapted_passed"): ("flag", False),
    ("PairedTrial", "baseline_passed"): ("flag", False), ("PairedTrial", "critical"): ("flag", False),
    ("Probe", "assumed_match_probability"): ("number", False), ("Probe", "cost"): ("number", False),
    ("Probe", "novelty"): ("number", False), ("ProblemSignature", "budget"): ("number", False),
    ("ProblemSignature", "risk"): ("integer", False), ("SelectionDecision", "admitted_trials"): ("integer", False),
    ("SelectionDecision", "rejected_trials"): ("integer", False), ("ToolSpec", "assumed_match_probability"):
        ("number", False), ("ToolSpec", "cost"): ("number", False), ("TreeNode", "feature"): ("integer", True),
    ("TreeNode", "threshold"): ("number", True),
}


def test_the_record_fields_that_declare_a_number_or_a_flag_are_listed_from_the_code():
    assert leaf_fields() == EXPECTED_LEAF_FIELDS


_FIELD_CASES = [(name, field, kind) for (name, field), (leaf, nullable) in sorted(EXPECTED_LEAF_FIELDS.items())
                for kind in sorted(PLANTED) if not (kind == "flag" and leaf == "flag" or kind == "null" and nullable)]


@pytest.mark.parametrize("name,field,kind", _FIELD_CASES, ids=[f"{n}.{f}:{k}" for n, f, k in _FIELD_CASES])
def test_every_such_field_refuses_what_is_not_its_kind_where_the_record_is_made(name, field, kind):
    made = MADE[name]()
    with pytest.raises(NotPlainData) as excinfo:
        replace(made, **{field: PLANTED[kind]()})
    assert excinfo.value.code == codes.INPUT and f"{name}.{field}" in str(excinfo.value)


@pytest.mark.parametrize("name,field", sorted(k for k, v in EXPECTED_LEAF_FIELDS.items() if v[0] == "integer"))
def test_an_integer_field_refuses_a_float_rather_than_truncating_it(name, field):
    with pytest.raises(NotPlainData):
        replace(MADE[name](), **{field: 1.5})


# --- 4. Every public parameter that declares a number or a flag --------------------------------------------------------

def leaf_parameters():
    """Every parameter of a public entry point (``test_boundary_inventory.entries``) that declares a number, an integer
    or a flag: (entry, parameter) -> its annotation."""
    out = {}
    for name, fn in entries():
        node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
        for arg in [*node.args.args, *node.args.kwonlyargs]:
            annotation = ast.unparse(arg.annotation) if arg.annotation is not None else ""
            if annotation in ("float", "int", "bool", "float | None", "int | None", "bool | None"):
                out[(name, arg.arg)] = annotation
    return out


_SELECTOR = MetacognitiveMethodSelector()
_CONTEXT = ProblemSignature("d", "u", "k", 1, 2)
_VARIANT = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
_STANDARD = DomainStandard("d", "1", "AU", "a", "a" * 64, (Criterion("c", "d", "checker", "1:1"),))


class _Predictor:
    def predict(self, rows):
        return [1.0 for _ in rows]


#: For each such parameter, a call that hands it the planted value, every other argument valid.
PARAMETER_CALLS = {
    ("AdaptationPromotionGate.__init__", "alpha"): lambda v: AdaptationPromotionGate(alpha=v),
    ("AdaptationPromotionGate.__init__", "max_evaluations"): lambda v: AdaptationPromotionGate(max_evaluations=v),
    ("AdaptationPromotionGate.__init__", "min_cases"): lambda v: AdaptationPromotionGate(min_cases=v),
    ("AdaptationPromotionGate.__init__", "min_discordant"): lambda v: AdaptationPromotionGate(min_discordant=v),
    ("BayesianUpdater.update", "positive"): lambda v: BayesianUpdater().update({"A": .5, "B": .5}, PROBE, v),
    ("CognitiveAgent.run", "budget"): lambda v: CognitiveAgent(StaticProposer([]), TOOLS).run("o", HYPS, budget=v),
    ("CognitiveAgent.run", "max_steps"): lambda v: CognitiveAgent(StaticProposer([]), TOOLS).run(
        "o", HYPS, budget=1, max_steps=v),
    ("CognitiveAssuranceOrchestrator.compile", "max_supplemental"): lambda v: CognitiveAssuranceOrchestrator(
        _SELECTOR).compile(claim=ClaimIR("c", "s", ("formal",), {}), context=ProblemSignature("d", "u", "k", 1, 2),
                           standard=_STANDARD, variants=[_VARIANT], variant_models={"v": {"formula": "A"}},
                           max_supplemental=v),
    ("CognitiveAssuranceOrchestrator.execute", "approved"): lambda v: CognitiveAssuranceOrchestrator(
        _SELECTOR).execute(MADE["CompiledResearch"](), approved=v),
    ("InformationGainSelector.__init__", "min_gain"): lambda v: InformationGainSelector(min_gain=v),
    ("InformationGainSelector.__init__", "novelty_weight"): lambda v: InformationGainSelector(novelty_weight=v),
    ("InformationGainSelector.rank", "budget"): lambda v: InformationGainSelector().rank({"A": .5, "B": .5}, [PROBE], v),
    ("LLMClaimCompiler.__init__", "max_claims"): lambda v: LLMClaimCompiler(None, max_claims=v),
    ("LLMIdeaGenerator.__init__", "max_ideas"): lambda v: LLMIdeaGenerator(None, max_ideas=v),
    ("LLMMetacognitiveCritic.__init__", "max_items"): lambda v: LLMMetacognitiveCritic(None, max_items=v),
    ("LLMProposer.__init__", "max_probes"): lambda v: LLMProposer(None, TOOLS, max_probes=v),
    ("MLAcquisitionSelector.__init__", "min_gain"): lambda v: MLAcquisitionSelector(_Predictor(), min_gain=v),
    ("MLAcquisitionSelector.rank", "budget"): lambda v: MLAcquisitionSelector(_Predictor()).rank(
        {"A": .5, "B": .5}, [PROBE], v),
    ("MetacognitiveMethodSelector.__init__", "exploration_weight"):
        lambda v: MetacognitiveMethodSelector(exploration_weight=v),
    ("MetacognitiveMethodSelector.__init__", "seed"): lambda v: MetacognitiveMethodSelector(seed=v),
    ("MetacognitiveMethodSelector.select", "max_optional"): lambda v: MetacognitiveMethodSelector().select(
        _CONTEXT, [_VARIANT], required_methods=["formal"], max_optional=v),
    ("MixedMethodExecutor.execute", "approved"): lambda v: MixedMethodExecutor(None).execute(
        MethodRouter().plan([ClaimIR("c", "s", ("formal",), {"formal": {"formula": "A"}})]), approved=v),
    ("distill_blackbox", "max_depth"): lambda v: distill_blackbox(None, ROWS[:12], ROWS[12:], ["x", "y"], max_depth=v),
    ("distill_blackbox", "min_samples_leaf"): lambda v: distill_blackbox(None, ROWS[:12], ROWS[12:], ["x", "y"],
                                                                         min_samples_leaf=v),
    ("distill_blackbox", "seed"): lambda v: distill_blackbox(None, ROWS[:12], ROWS[12:], ["x", "y"], seed=v),
    ("sklearn_payload", "seed"): lambda v: sklearn_payload(HISTORY, seed=v),
}


def test_the_public_parameters_that_declare_a_number_or_a_flag_are_listed_from_the_code():
    assert set(leaf_parameters()) == set(PARAMETER_CALLS)


@pytest.mark.parametrize("name,parameter", sorted(PARAMETER_CALLS))
def test_every_such_parameter_is_read_by_its_rule(name, parameter):
    fn = dict(entries())[name]
    node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    if leaf_parameters()[(name, parameter)].startswith("bool"):
        # a flag is read as True and nothing else, or tested to be one
        ruled = any(isinstance(c, ast.Call) and (getattr(c.func, "id", "") == "affirmed" or (
            getattr(c.func, "id", "") == "type")) and any(isinstance(n, ast.Name) and n.id == parameter
                                                          for n in ast.walk(c)) for c in ast.walk(node))
    else:
        # read by the number rule, by name or as the attribute the parameter was bound to
        bound = set()
        for a in [n for n in ast.walk(node) if isinstance(n, ast.Assign)]:
            for t in a.targets:
                pairs = zip(t.elts, a.value.elts) if isinstance(t, ast.Tuple) and isinstance(a.value, ast.Tuple) \
                    else [(t, a.value)]
                bound |= {ast.unparse(target) for target, value in pairs
                          if any(isinstance(n, ast.Name) and n.id == parameter for n in ast.walk(value))}
        ruled = any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "number" and c.args and (
            ast.unparse(c.args[0]) in {parameter, *bound} or (isinstance(c.args[0], ast.Call) and "getattr" in
                                                              ast.unparse(c.args[0])))
            for c in ast.walk(node))
    assert ruled, f"{name} reads {parameter} by no rule"


_PARAMETER_CASES = [(name, parameter, kind) for (name, parameter) in sorted(PARAMETER_CALLS)
                    for kind in sorted(PLANTED)]


@pytest.mark.parametrize("name,parameter,kind", _PARAMETER_CASES,
                         ids=[f"{n}:{p}:{k}" for n, p, k in _PARAMETER_CASES])
def test_a_value_planted_at_each_such_parameter_is_refused_unless_it_is_its_kind(name, parameter, kind, monkeypatch):
    monkeypatch.setitem(sys.modules, "sklearn", None)  # a surrogate that reached its fit would need scikit-learn
    annotation = leaf_parameters()[(name, parameter)]
    if kind == "flag" and annotation.startswith("bool"):
        return  # a flag is a flag's own kind: approval and an outcome read it as True, or False, and nothing else
    _refused(lambda: PARAMETER_CALLS[(name, parameter)](PLANTED[kind]()))


def test_nothing_in_the_package_converts_a_callers_value_with_int():
    # an integer is read as one (an integer field, or the number rule for a bound): never truncated from a float or
    # parsed from text
    assert [key for key in conversions() if key[2] == "int"] == []
