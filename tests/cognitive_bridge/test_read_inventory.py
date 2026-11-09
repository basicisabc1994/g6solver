# SPDX-License-Identifier: Apache-2.0
"""The class one_fact_two_rules, removed at its root for reads (review 3 of PR #8's second follow-up, all five
findings). Each earlier repair made the boundary's rule stricter and listed the places it knew, and each review found a
value that reached the bridge by another path. Review 3 found the two paths left:

- a caller's value read again, or reshaped, around the boundary: the MCP block serialized an exact ``CognitiveInput``
  before the boundary read it (finding 2), and G6's registry rebuilt the typed input from the caller's objects after
  the block's raw screen had read them (finding 5);
- a collaborator's value that entered past the boundary: a fitted classifier's leaf scores (finding 1), a draft's card
  text (finding 3) and a black box's training predictions (finding 4).

So this lists paths, not places, from the code:

1. every read of an attribute, an item or a call's result on an object the bridge did not construct (a parameter
   that holds code: an estimator, a learner, a black box, a model, a selector, a gate, a hook; a collaborator a class
   keeps by reference; a record's code field; a learner the bridge builds from a class it imports), and of every name
   bound from such a read, each with what it is: read by one of the readers before anything uses it, a presence test, a
   name bound for further reads, a call whose answer is not read, kept by reference, or handed whole to a function that
   is itself listed here. It fails on any read it cannot classify;
2. every function between the MCP block's boundary and its typed validation, each shown to take the boundary's
   snapshot and never the caller's object, nothing serialized, rebuilt or read again;
3. at each place a caller's or a collaborator's value is read, numeric text, a flag, null, NaN, a set, bytes and a
   container subclass planted, each refused unless it is that place's own kind; through the block, in every field,
   by ``infer``, an exact ``CognitiveInput`` and G6's real registry, and a caller's change after the screen at each
   nesting level."""
from __future__ import annotations

import ast
import copy
import importlib.util
import math
import sys
import types
import typing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel
from returns.result import Success

from g6.cognitive_bridge import block as block_module
from g6.cognitive_bridge import codes, distill_blackbox, extract_tree
from g6.cognitive_bridge.block import ClaimInput, CognitiveBridgeBlock, CognitiveInput, HypothesisInput
from g6.cognitive_bridge.codes import INPUT, only_fields, refusal, settled
from g6.core.reliability_envelope import warning_card
from g6.core.registry import get_registry

PACKAGE = Path(codes.__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_read_{name}", Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_boundary = _load("test_boundary_inventory")
_collaborators = _load("test_collaborator_inventory")
_conversions = _load("test_conversion_inventory")


def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE.glob("*.py"))]


def _functions(tree):
    """Every function and method of a module with its dotted name, functions nested in it read as part of it."""
    found = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((f"{prefix}{child.name}", child))
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _parents(tree):
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


# --- 1. Every read of a value the bridge did not construct ------------------------------------------------------------

#: The readers: a value read as one of their first arguments is read by its rule before anything uses it.
READERS = {"number", "text", "plain", "snapshot", "fresh", "returned", "affirmed", "_native", "_estimated", "_label",
           "_numbers", "_read_card", "_plain", "_text", "_arguments", "only_fields", "unclaimed", "_walk"}
#: The functions that read a value the bridge did not construct raw, by design: the boundary and the readers it hands a
#: value to. Their parameters are what reads a value, so they are not listed as values read.
READER_FUNCTIONS = {
    ("codes.py", "_is_type"): "exact kind identity only; challenged by the metaclass inventory",
    ("codes.py", "_type_mro"): "actual class MRO through the built-in descriptor, without metaclass overrides",
    ("codes.py", "_has_member"): "exact type MRO and namespace through type's built-in descriptors",
    ("codes.py", "_code"): "the exact type MRO's call member; challenged by the metaclass inventory",
    ("snapshot.py", "*"): "the boundary: each function reads a caller's or a collaborator's value once, to take it as "
                          "plain data, or reads a maker by its identity",
    ("codes.py", "_read_as"): "the boundary's leaf test: a value's exact type",
    ("codes.py", "_named"): "a value's type, named in a refusal",
    ("codes.py", "_unread"): "what the leaf test does not read, named in a refusal",
    ("codes.py", "_read_card"): "the one reading of a card: snapshot.plain, then a strict WarningCard",
    ("codes.py", "claims"): "the claim rule: a value walked by the boundary's leaf test, by exact type",
    ("codes.py", "_bounded_claims"): "the claim rule's entrance: detached by the shared visit-budget walker before claims",
    ("codes.py", "unclaimed"): "the claim rule's verdict",
    ("codes.py", "affirmed"): "the one reading of a yes: True and nothing else",
    ("codes.py", "_plain"): "snapshot.plain, imported where it is used",
    ("codes.py", "_text"): "snapshot.text, imported where it is used",
    ("codes.py", "only_fields"): "snapshot.plain first, then its checks on what the boundary made",
    ("codes.py", "_state_fields"): "asks G6's door's own reader which fields it reads: it hands its own field names",
    ("codes.py", "_card"): "snapshot.text or the strict card reading, before anything else reads a card's parts",
    ("codes.py", "_draft_card"): "snapshot.plain, then snapshot.text, before anything else reads a draft's card",
    ("codes.py", "result_faults"): "the makers' format check: its first read is its value's exact type (a mapping "
                                   "subclass, whose own methods would answer, is not a result; checked below)",
    ("assurance.py", "is_risk"): "the risk rule: an exact int from 0 to 3",
    ("block.py", "_arguments"): "the block's boundary: snapshot.plain of the arguments as given",
    ("block.py", "_as_given"): "reads an exact input model by its fields for the boundary, never serialized "
                               "(part 2 below)",
    ("xai.py", "_native"): "the one reader of an estimator's values: tolist, then snapshot.plain",
    ("xai.py", "_estimated"): "_native, then the number rule",
    ("xai.py", "_label"): "snapshot.plain, then a JSON leaf",
    ("xai.py", "_numbers"): "the number rule, item by item",
    ("xai.py", "_float32"): "the number rule, then a float32",
}


def _reader_function(module, name) -> bool:
    return (module, "*") in READER_FUNCTIONS or (module, name) in READER_FUNCTIONS


def _code_parameters(fn, code):
    """The parameters of ``fn`` and of every function nested in it that hold code (by annotation)."""
    found = set()
    for inner in [n for n in ast.walk(fn) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        args = inner.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.arg not in ("self", "cls") and _boundary._outer_names(arg.annotation) & code:
                found.add(arg.arg)
    return found


def _caller_inputs(module, fn):
    """A public maker's data parameters (``codes.__all__``): what a caller hands it to make a result of. Its keyword
    ``**`` parameter is not one: Python builds that dict itself, and each maker reads its values through the claim rule
    and the boundary before it returns them (planted in part 3)."""
    if module != "codes.py" or fn.name not in codes.__all__:
        return set()
    args = fn.args
    return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
            if not a.arg.startswith("_") and a.arg not in ("where", "allowed", "required")}


def _rebound(fn):
    """Each name ``fn`` binds again to what a reader read of it (``x = _plain(x)``): the line, after which the name
    holds the reader's result, not the caller's value."""
    out = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in READERS
                   and c.args[:1] and isinstance(c.args[0], ast.Name) and c.args[0].id == name
                   for c in ast.walk(n.value)):
                out[name] = min(out.get(name, n.lineno), n.lineno)
    return out


def _learners(fn):
    """The names ``fn`` binds to an object it builds from a class it imports from outside G6 (a learner)."""
    imported = {alias.asname or alias.name for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)
                and n.level == 0 and not (n.module or "").startswith("g6") for alias in n.names}
    classes = set(imported)
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            value = n.value
            options = [value.body, value.orelse] if isinstance(value, ast.IfExp) else [value]
            if all(isinstance(o, ast.Name) and o.id in imported for o in options):
                classes.add(n.targets[0].id)
    return {n.targets[0].id for n in ast.walk(fn) if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name) and n.value.func.id in classes}


def _top(node, parent):
    """The read ``node`` starts: up through its attributes, items, calls and ``getattr``."""
    top = node
    while True:
        up = parent.get(top)
        if isinstance(up, (ast.Attribute, ast.Subscript)) and up.value is top:
            top = up
        elif isinstance(up, ast.Call) and (up.func is top or (
                isinstance(up.func, ast.Name) and up.func.id == "getattr" and up.args[:1] == [top])):
            top = up
        else:
            return top


def _handed(outer, parent):
    """What a bare value ``outer`` is handed on to: (the kind, the target), or None."""
    up = parent.get(outer)
    if isinstance(up, ast.Tuple) and isinstance(parent.get(up), ast.Assign):
        assign = parent[up]
        target = assign.targets[0]
        if isinstance(target, ast.Tuple) and len(target.elts) == len(up.elts):
            target = target.elts[up.elts.index(outer)]
        return ("bound", target)
    if isinstance(up, ast.Assign) and up.value is outer and len(up.targets) == 1:
        return ("bound", up.targets[0])
    if isinstance(up, ast.keyword):
        return ("handed", parent[up])
    if isinstance(up, ast.Call) and outer in up.args:
        return ("handed", up)
    return None


def _classify(node, parent, kept):
    """What a read of a value the bridge did not construct is, and the name it binds, if any."""
    top = _top(node, parent)
    up = parent.get(top)
    if top is not node:  # an attribute, an item or a call's result was read
        if isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id in READERS and up.args[:1] == [top]:
            return f"read by {up.func.id}", None
        if isinstance(up, ast.Assign) and up.value is top and len(up.targets) == 1 and isinstance(up.targets[0],
                                                                                                    ast.Name):
            return f"bound to {up.targets[0].id}, whose reads are listed", up.targets[0].id
        if isinstance(up, ast.Expr) and isinstance(top, ast.Call):
            return "called; its answer is not read", None
        return "UNCLASSIFIED: " + ast.unparse(up)[:100], None
    if isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id == "hasattr" and up.args[:1] == [top]:
        return "a presence test (hasattr)", None
    if isinstance(up, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in up.ops) and all(
            isinstance(c, ast.Constant) and c.value is None for c in up.comparators):
        return "a presence test (is None)", None
    outer = top
    while isinstance(parent.get(outer), ast.BoolOp) or (
            isinstance(parent.get(outer), ast.IfExp) and parent[outer].test is not outer):
        outer = parent[outer]
    handed = _handed(outer, parent)
    if handed and handed[0] == "handed" and isinstance(handed[1].func, ast.Name) and handed[1].func.id in READERS \
            and handed[1].args[:1] == [outer]:
        return f"read by {handed[1].func.id}", None
    if handed and handed[0] == "bound":
        target = handed[1]
        if isinstance(target, ast.Name):
            return f"bound to {target.id}, whose reads are listed", target.id
        if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"
                and target.attr in kept):
            return f"kept by reference as self.{target.attr}, whose reads are listed", None
    if handed and handed[0] == "handed":
        return f"handed whole to {ast.unparse(handed[1].func)}", None
    return "UNCLASSIFIED: " + ast.unparse(up)[:100], None


def reads():
    """Every read of a value the bridge did not construct: (module, function, the read, what it is)."""
    code, kept = _boundary._code_types(), _collaborators._kept_by_reference()
    fields, checks = _collaborators._callable_fields(), _collaborators._checks()
    found = []
    for module, tree in _modules():
        for name, fn in _functions(tree):
            parent, rebound = _parents(fn), _rebound(fn)
            roots = set() if _reader_function(module, name) or name.split(".")[-1] in checks else (
                _code_parameters(fn, code) | _learners(fn) | _caller_inputs(module, fn))
            seen, changed = set(), True
            while changed:
                changed = False
                for node in ast.walk(fn):
                    if id(node) in seen:
                        continue
                    root = (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in roots
                            and node.lineno <= rebound.get(node.id, node.lineno)) or (
                        isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                        and isinstance(node.value, ast.Name) and node.value.id == "self" and node.attr in kept) or (
                        isinstance(node, ast.Attribute) and node.attr in fields
                        and isinstance(parent.get(node), ast.Call) and parent[node].func is node)
                    if not root or module == "snapshot.py":
                        continue
                    seen.add(id(node))
                    what, bound = _classify(node, parent, kept)
                    found.append((module, name, ast.unparse(_top(node, parent))[:90], what))
                    if bound and bound not in roots:
                        roots.add(bound)
                        changed = True
    return found


_KEPT = "kept by reference as self.{}, whose reads are listed"
_RETURNED, _AFFIRMED, _NONE, _HAS = "read by returned", "read by affirmed", "a presence test (is None)", \
    "a presence test (hasattr)"
#: Every read of a value the bridge did not construct, compared whole: a new one is listed here with what it is.
EXPECTED_READS = sorted([
    ("codes.py", "_plain_fields", "value", "read by _plain"),
    ("adaptation.py", "AdaptationPromotionGate.__init__", "admit_control", _KEPT.format("admit_control")),
    ("adaptation.py", "AdaptationPromotionGate.__init__", "admit_pair", _KEPT.format("admit_pair")),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_control", _NONE),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_control", _NONE),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_control(fresh(x))", _AFFIRMED),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_pair", _NONE),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_pair", _NONE),
    ("adaptation.py", "AdaptationPromotionGate.assess", "self.admit_pair(fresh(x))", _AFFIRMED),
    ("assurance.py", "InProcessAssuranceEvaluator.__init__", "reviewer_authenticator",
     _KEPT.format("reviewer_authenticator")),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "self.reviewer_authenticator", _NONE),
    ("assurance.py", "InProcessAssuranceEvaluator.evaluate", "self.reviewer_authenticator(fresh(attestation))",
     _AFFIRMED),
    ("assurance.py", "_test", "checker.check(fresh(content))", _RETURNED),
    # a refusal's code, reason and card; a refusal and a draft's card handed to settled (review 3, finding 3)
    ("codes.py", "refusal", "card", "handed whole to _card"),
    ("codes.py", "refusal", "code", "read by _text"),
    ("codes.py", "refusal", "reason", "read by _text"),
    ("codes.py", "settled", "draft_card", _NONE),
    ("codes.py", "settled", "draft_card", _NONE),
    ("codes.py", "settled", "draft_card", "handed whole to _draft_card"),
    ("codes.py", "settled", "refused", _NONE),
    ("codes.py", "settled", "refused", "read by _plain"),
    ("critique.py", "LLMMetacognitiveCritic.__init__", "llm", _KEPT.format("llm")),
    ("critique.py", "LLMMetacognitiveCritic.propose", "self.llm.complete(fresh(system), fresh(user), max_tokens=2600)",
     _RETURNED),
    ("engine.py", "CognitiveAgent.__init__", "proposer", _KEPT.format("proposer")),
    ("engine.py", "CognitiveAgent.__init__", "selector", _KEPT.format("selector")),
    ("engine.py", "CognitiveAgent.__init__", "updater", _KEPT.format("updater")),
    ("engine.py", "CognitiveAgent.run", "self.proposer.propose(fresh(objective), fresh(current), fresh(history))",
     _RETURNED),
    ("engine.py", "CognitiveAgent.run",
     "self.selector.rank(fresh(_prior(beliefs, checked)), fresh(candidates), fresh(remaining))", _RETURNED),
    ("engine.py", "CognitiveAgent.run",
     "self.updater.update(fresh(prior), fresh(chosen), fresh(observation.positive))", _RETURNED),
    ("engine.py", "LLMIdeaGenerator.__init__", "llm", _KEPT.format("llm")),
    ("engine.py", "LLMIdeaGenerator.generate", "self.llm.complete(fresh(system), fresh(user), max_tokens=1200)",
     _RETURNED),
    ("engine.py", "LLMProposer.__init__", "llm", _KEPT.format("llm")),
    ("engine.py", "LLMProposer.propose", "self.llm.complete(fresh(system), fresh(user), max_tokens=1500)", _RETURNED),
    ("engine.py", "MLAcquisitionSelector.__init__", "predictor", _KEPT.format("predictor")),
    ("engine.py", "MLAcquisitionSelector.rank", "self.predictor.predict(fresh(features))", _RETURNED),
    ("engine.py", "_ToolDoor.invoke", "self.tool.call(fresh(params['question']), fresh(params['arguments']))",
     _RETURNED),
    ("epistemic.py", "CrossMethodConflictGate.__init__", "admit_assertion", _KEPT.format("admit_assertion")),
    ("epistemic.py", "CrossMethodConflictGate.reconcile", "self.admit_assertion", _NONE),
    ("epistemic.py", "CrossMethodConflictGate.reconcile", "self.admit_assertion(fresh(row))", _AFFIRMED),
    ("epistemic.py", "checked_report", "report", "read by only_fields"),
    ("metacognition.py", "MetacognitiveMethodSelector.__init__", "admit_trial", _NONE),
    ("metacognition.py", "MetacognitiveMethodSelector.__init__", "admit_trial", _KEPT.format("admit_trial")),
    ("metacognition.py", "MetacognitiveMethodSelector.__init__", "admit_trial(fresh(t))", _AFFIRMED),
    ("metacognition.py", "MetacognitivePlanner.__init__", "selector", _KEPT.format("selector")),
    ("metacognition.py", "MetacognitivePlanner.plan",
     "self.selector.select(fresh(context), fresh(variants), required_methods=fresh(required_meth", _RETURNED),
    ("mixed_methods.py", "LLMClaimCompiler.__init__", "llm", _KEPT.format("llm")),
    ("mixed_methods.py", "LLMClaimCompiler.draft", "self.llm.complete(fresh(system), fresh(user), max_tokens=2200)",
     _RETURNED),
    ("mixed_methods.py", "MixedMethodExecutor.__init__", "registry", _KEPT.format("registry")),
    ("mixed_methods.py", "MixedMethodExecutor._invoke", "self.registry", "handed whole to invoke_checked"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.__init__", "registry", _KEPT.format("registry")),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.__init__", "selector", _KEPT.format("selector")),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess", "epistemic_gate",
     "bound to gate, whose reads are listed"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess", "evaluator", "handed whole to issued"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess",
     "evaluator.evaluate(standard=fresh(standard), artifacts=fresh(artifacts), verifiers=fresh(v", _RETURNED),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess", "gate", "handed whole to issued"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.assess", "gate.reconcile(fresh(scoped_assertions))",
     _RETURNED),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.compile", "self.selector", "handed whole to issued"),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.compile",
     "self.selector.select(fresh(context), fresh(variants), required_methods=fresh(sorted(reques", _RETURNED),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.execute", "self.registry", _NONE),
    ("orchestration.py", "CognitiveAssuranceOrchestrator.execute", "self.registry",
     "handed whole to MixedMethodExecutor"),
    # a fitted estimator, a learner and a black box (review 3, findings 1 and 4)
    ("xai.py", "_feature_names", "estimator", _HAS),
    ("xai.py", "_feature_names", "estimator", _HAS),
    ("xai.py", "_feature_names", "estimator.n_features_in_", "read by _estimated"),
    ("xai.py", "_feature_names", "estimator.feature_names_in_", "bound to supplied, whose reads are listed"),
    ("xai.py", "_feature_names", "supplied", "read by _native"),
    ("xai.py", "_feature_names", "getattr(estimator, 'n_outputs_', 1)", "read by _estimated"),
    ("xai.py", "distill_blackbox", "blackbox.predict(fresh(holdout))", _RETURNED),
    ("xai.py", "distill_blackbox", "blackbox.predict(fresh(train))", _RETURNED),
    ("xai.py", "distill_blackbox", "tree", "handed whole to extract_tree"),
    ("xai.py", "distill_blackbox", "tree.fit(train, observed_train)", "called; its answer is not read"),
    ("xai.py", "extract_tree", "estimator", _HAS),
    ("xai.py", "extract_tree", "estimator", "handed whole to _feature_names"),
    ("xai.py", "extract_tree", "estimator.classes_", "read by _native"),
    ("xai.py", "extract_tree", "estimator.tree_", "bound to tree, whose reads are listed"),
    ("xai.py", "extract_tree", "getattr(tree, name)", "read by _native"),
])
#: Where a value is handed whole outside the package's listed functions, with why.
OUTSIDE_HANDOFFS = {
    "invoke_checked": "G6's door (g6.core.door), which calls the registry and hands the bridge back only an answer the "
                      "door passed, taken by the boundary (test_boundary_inventory, the tools' answers)",
    "issued": "snapshot.issued: reads a maker by its identity in the record of what it issued; never calls it",
}


def test_the_inventory_finds_the_paths_review_3_found():
    listed = {(m, n, read) for m, n, read, _ in reads()}
    for site in [("xai.py", "extract_tree", "getattr(tree, name)"), ("xai.py", "extract_tree", "estimator.classes_"),
                 ("xai.py", "distill_blackbox", "blackbox.predict(fresh(train))"),
                 ("codes.py", "settled", "draft_card"), ("codes.py", "settled", "refused")]:
        assert site in listed, f"the inventory did not find {site}"


def test_the_reads_are_listed_from_the_code():
    assert sorted(reads()) == EXPECTED_READS


def test_no_read_is_left_unclassified():
    bad = [f"{m}:{n} {read} -> {what}" for m, n, read, what in reads() if what.startswith("UNCLASSIFIED")]
    assert bad == [], "a read of a value the bridge did not construct that this cannot classify: " + "; ".join(bad)


def test_every_value_handed_on_whole_is_read_where_it_arrives():
    # handed to a reader function, to a function or class whose own reads are listed here, or to G6's door or the
    # issued record (OUTSIDE_HANDOFFS): never to anything that reads it unlisted
    listed = {n.split(".")[-1] if not n.endswith("__init__") else n.split(".")[-2] for _, n, _, _ in reads()}
    readers = {name for _, name in READER_FUNCTIONS}
    bad = [f"{m}:{n} {read} -> {what}" for m, n, read, what in reads() if what.startswith("handed whole to ")
           and what.removeprefix("handed whole to ") not in listed | readers | set(OUTSIDE_HANDOFFS)]
    assert bad == [], "a value handed on to code whose reads are not listed: " + "; ".join(bad)


def test_every_kept_value_is_a_collaborator_the_store_inventory_names():
    kept = {what.split("self.")[1].split(",")[0] for _, _, _, what in reads() if what.startswith("kept by")}
    assert kept and kept <= _collaborators._stores.COLLABORATORS


def test_the_format_check_reads_its_value_by_exact_type_first():
    fn = next(f for name, f in _functions(ast.parse((PACKAGE / "codes.py").read_text(encoding="utf-8")))
              if name == "result_faults")
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert isinstance(body[0], ast.If) and ast.unparse(body[0].test) == "not _is_type(type(result), OBJECTS)"

    class Forged(dict):
        def get(self, key, default=None):
            return {"completion_state": "qualified-draft"}.get(key, default)

    assert codes.result_faults(Forged())
    assert codes.claims({"x": Forged(completion_state="verified")})


# --- 2. Every function between the block's boundary and its typed validation -----------------------------------------

def _block():
    return ast.parse(Path(block_module.__file__).read_text(encoding="utf-8"))


def _block_functions():
    return dict(_functions(_block()))


#: The functions a caller's arguments, or the boundary's untyped read of them, pass through, from the block's door
#: (screen_raw, infer) to its typed validation (``CognitiveInput.model_validate``), listed from the code below, each with
#: what it takes. What validation makes is the bridge's own typed input (``_unchanged`` compares two such, from the
#: boundary's two reads, and the conversion inventory checks every validation reads what the boundary made).
EXPECTED_BETWEEN = {
    "CognitiveBridgeBlock.screen_raw": "the caller's raw arguments, read once, by _arguments; it keeps that read",
    "CognitiveBridgeBlock.infer": "the caller's arguments (or the registry's typed input), read once by _arguments; "
                                  "and screen_raw's read, by _held",
    "_arguments": "the boundary: snapshot.plain of the arguments as given (_as_given)",
    "_as_given": "outer mapping protocol; exact input models and their nested fields go through the shared budget walker",
    "CognitiveBridgeBlock._held": "screen_raw's read, which no one else holds, taken once",
}


def _validated(node) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "model_validate"


def _carried(expr, names) -> bool:
    """Whether ``expr`` hands on the caller's value or the boundary's untyped read of it: it reads one of ``names``, or
    screen_raw's read (``_held``), anywhere but inside a typed validation, whose result is the bridge's own."""
    if _validated(expr):
        return False
    if isinstance(expr, ast.Name):
        return expr.id in names
    if isinstance(expr, ast.Call) and ast.unparse(expr.func).endswith("_held"):
        return True
    return any(_carried(child, names) for child in ast.iter_child_nodes(expr))


def _carry(fn, tainted):
    """The names in ``fn`` that hold the caller's value or the boundary's untyped read of it: ``tainted``, and every
    name bound from an expression that carries one (:func:`_carried`)."""
    names, changed = set(tainted), True
    while changed:
        changed = False
        for n in ast.walk(fn):
            pairs = [(t, n.value) for t in n.targets] if isinstance(n, ast.Assign) else (
                [(n.target, n.iter)] if isinstance(n, (ast.For, ast.comprehension)) else [])
            for target, value in pairs:
                if not isinstance(target, (ast.Name, ast.Tuple)):
                    continue
                bound = {x.id for x in ast.walk(target) if isinstance(x, ast.Name)}
                if _carried(value, names) and bound - names:
                    names |= bound
                    changed = True
    return names


def between() -> dict[str, set[str]]:
    """The functions of block.py a caller's arguments reach before typed validation, each with the names in it that
    hold them: from screen_raw's and infer's parameter, every function of the block called with a value read from one
    (or screen_raw's read), in turn."""
    functions = _block_functions()
    short = {name.split(".")[-1]: name for name in functions}
    reached, todo = {}, [("CognitiveBridgeBlock.screen_raw", {"raw"}), ("CognitiveBridgeBlock.infer", {"data"})]
    while todo:
        name, tainted = todo.pop()
        if name in reached:
            continue
        fn = functions[name]
        reached[name] = names = _carry(fn, tainted)
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            callee = call.func.id if isinstance(call.func, ast.Name) else (
                call.func.attr if isinstance(call.func, ast.Attribute) and ast.unparse(call.func.value) == "self"
                else None)
            arguments = [*call.args, *(k.value for k in call.keywords)]
            if callee in short and (callee == "_held" or any(_carried(a, names) for a in arguments)):
                target = short[callee]
                todo.append((target, {a.arg for a in functions[target].args.args if a.arg != "self"}))
    return reached


def test_the_functions_between_the_boundary_and_typed_validation_are_listed_from_the_code():
    assert set(between()) == set(EXPECTED_BETWEEN)


#: What would read a caller's object again, or make it another kind, before or after the boundary: serializing,
#: rebuilding a container, constructing a model without validation, a copy.
REREADS = {"model_dump", "model_dump_json", "dict", "list", "tuple", "model_construct", "copy", "deepcopy",
           "model_copy"}


@pytest.mark.parametrize("name", sorted(EXPECTED_BETWEEN))
def test_nothing_between_serializes_rebuilds_or_rereads_a_callers_object(name):
    fn, names = _block_functions()[name], between()[name]
    bad = [ast.unparse(n)[:80] for n in ast.walk(fn)
           if (isinstance(n, ast.Call) and (getattr(n.func, "id", None) in REREADS or getattr(n.func, "attr", None)
                                            in REREADS) and any(_carried(a, names) for a in [
               *n.args, *(k.value for k in n.keywords), *([n.func.value] if isinstance(n.func, ast.Attribute) else [])]))
           or (isinstance(n, ast.Attribute) and n.attr == "__dict__")
           or (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "vars")]
    assert bad == [], f"{name} reshapes or rereads what it is handed: {bad}"


def test_the_callers_object_is_read_only_by_the_boundary_and_its_read_kept_only_for_infer():
    tree = _block()
    functions = dict(_functions(tree))
    for name, param in (("CognitiveBridgeBlock.screen_raw", "raw"), ("CognitiveBridgeBlock.infer", "data")):
        fn = functions[name]
        parent = _parents(fn)
        loads = [n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == param and isinstance(n.ctx, ast.Load)]
        assert loads and all(isinstance(parent[n], ast.Call) and getattr(parent[n].func, "id", "") == "_arguments"
                             and parent[n].args == [n] for n in loads), f"{name} reads {param} past the boundary"
    parent = _parents(tree)
    stores = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "_screened"
              and isinstance(n.ctx, ast.Store)]
    loads = [n for n in ast.walk(tree) if (isinstance(n, ast.Attribute) and n.attr == "_screened"
                                           and isinstance(n.ctx, ast.Load)) or (
        isinstance(n, ast.Call) and getattr(n.func, "id", "") == "getattr" and len(n.args) > 1
        and isinstance(n.args[1], ast.Constant) and n.args[1].value == "_screened")]
    for store in stores:  # None, or a name bound from _arguments(...) in screen_raw
        assign = parent[store] if not isinstance(parent[store], ast.Tuple) else parent[parent[store]]
        value = assign.value if not isinstance(assign.value, ast.Tuple) else assign.value.elts[
            assign.targets[0].elts.index(store)]
        if isinstance(value, ast.Constant) and value.value is None:
            continue
        screen_raw = functions["CognitiveBridgeBlock.screen_raw"]
        bound = [n.value for n in ast.walk(screen_raw) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == getattr(value, "id", None) for t in n.targets)]
        assert bound and all(isinstance(b, ast.Call) and getattr(b.func, "id", "") == "_arguments" for b in bound), \
            ast.unparse(assign)
    held = functions["CognitiveBridgeBlock._held"]
    assert loads and all(any(n is load for n in ast.walk(held)) for load in loads), "_screened read outside _held"
    infer = functions["CognitiveBridgeBlock.infer"]
    assert ast.unparse(infer.body[0]) == "screened = self._held()", \
        "infer takes screen_raw's read first, so it is never left for another call"


def _models_held(model, seen=None):
    """Every pydantic model a model's fields hold, from their annotations."""
    seen = set() if seen is None else seen
    for info in model.model_fields.values():
        stack = [info.annotation]
        while stack:
            hint = stack.pop()
            if isinstance(hint, type) and issubclass(hint, BaseModel):
                if hint not in seen:
                    seen.add(hint)
                    _models_held(hint, seen)
            stack.extend(typing.get_args(hint))
    return seen


def test_the_models_read_by_their_fields_are_every_model_a_cognitive_input_holds():
    assert set(block_module._NESTED) == _models_held(CognitiveInput)


# --- 3. Each kind planted at each place a caller's or a collaborator's value is read ----------------------------------

class _List(list):
    pass


class _Map(dict):
    pass


class _Text(str):
    pass


#: What is planted at each place: numeric text, a flag, null, NaN, a set, bytes, and a container subclass of each kind.
KINDS = {"flag": lambda: True, "numeric-text": lambda: "0.5", "null": lambda: None, "nan": lambda: math.nan,
         "set": lambda: {"x"}, "bytes": lambda: b"x", "list-subclass": lambda: _List(["x"]),
         "dict-subclass": lambda: _Map(k="x"), "text-subclass": lambda: _Text("x")}
SUBCLASSES = ("list-subclass", "dict-subclass", "text-subclass")


def _tree_arrays(regression=True):
    return {"children_left": [1, -1, -1], "children_right": [2, -1, -1], "feature": [0, -2, -2],
            "threshold": [0.5, -2.0, -2.0],
            "value": [[[0.0]], [[10.0]], [[20.0]]] if regression else [[[1.0, 1.0]], [[2.0, 0.0]], [[0.0, 2.0]]]}


def _fitted(*, regression=True, tree=None, **attrs):
    """A fitted tree estimator as sklearn exposes one: three nodes, one feature; ``tree`` and ``attrs`` replace its
    arrays and attributes."""
    arrays = {**_tree_arrays(regression), **(tree or {})}
    fitted = {"n_features_in_": 1, "n_outputs_": 1, **({} if regression else {"classes_": ["yes", "no"]}), **attrs}
    return SimpleNamespace(tree_=SimpleNamespace(**arrays), **fitted)


def _with_item(name, index, value, regression=True):
    arrays = _tree_arrays(regression)
    arrays[name][index] = value
    return arrays


def _leaf(value, regression=True):
    arrays = _tree_arrays(regression)
    arrays["value"][1][0][0] = value
    return arrays


def _row(value):
    arrays = _tree_arrays()
    arrays["value"][1] = value
    return arrays


def _sklearn(monkeypatch, *, planted=None):
    """A fake scikit-learn whose learner fits a one-leaf tree on the targets it is handed (its classes, or their mean),
    with ``planted`` (attribute, value) set on what it fits."""
    tree_module = types.ModuleType("sklearn.tree")

    class Learner:
        def __init__(self, **kwargs):
            self.classify = False

        def fit(self, rows, targets):
            self.n_features_in_, self.n_outputs_ = len(rows[0]), 1
            if self.classify:
                self.classes_ = list(dict.fromkeys(targets))
                value = [[[1.0] + [0.0] * (len(self.classes_) - 1)]]
            else:
                value = [[[sum(targets) / len(targets)]]]
            self.tree_ = SimpleNamespace(children_left=[-1], children_right=[-1], feature=[-2], threshold=[-2.0],
                                         value=value)
            if planted:
                where, value = planted
                if where.startswith("tree_."):
                    setattr(self.tree_, where[6:], value)
                else:
                    setattr(self, where, value)
            return self

    class Classifier(Learner):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.classify = True

    tree_module.DecisionTreeClassifier, tree_module.DecisionTreeRegressor = Classifier, Learner
    package = types.ModuleType("sklearn")
    package.tree = tree_module
    monkeypatch.setitem(sys.modules, "sklearn", package)
    monkeypatch.setitem(sys.modules, "sklearn.tree", tree_module)


class _Box:
    """A black box whose ``call``-th answer has one prediction planted, or is planted whole."""

    def __init__(self, value, call, whole=False, label=False):
        self.value, self.call, self.whole, self.label, self.calls = value, call, whole, label, 0

    def predict(self, rows):
        self.calls += 1
        good = ["yes" if self.label else 0.5 for _ in rows]
        if self.calls != self.call:
            return good
        return self.value if self.whole else [self.value, *good[1:]]


ROWS = [[float(i), float(i % 3)] for i in range(20)]


def _distill(monkeypatch, box, task="regression", planted=None, names=("x", "y")):
    _sklearn(monkeypatch, planted=planted)
    return distill_blackbox(box, ROWS[:12], ROWS[12:], list(names), task=task)


_LABELS = {"flag", "numeric-text", "null"}
#: Each place a collaborator's or a caller's value is read, with its own kinds (what it takes) and a call that plants a
#: value there: (own kinds, call(value, monkeypatch)).
PLACES: dict[str, tuple[set[str], Any]] = {
    # a fitted estimator (finding 1)
    "an estimator's n_features_in_": (set(), lambda v, mp: extract_tree(_fitted(n_features_in_=v), ["x"])),
    "an estimator's n_outputs_": (set(), lambda v, mp: extract_tree(_fitted(n_outputs_=v), ["x"])),
    "an estimator's feature_names_in_": (set(),  # present metadata must be an array, even when falsy
                                         lambda v, mp: extract_tree(_fitted(feature_names_in_=v))),
    "a name in an estimator's feature_names_in_": ({"numeric-text"},
                                                   lambda v, mp: extract_tree(_fitted(feature_names_in_=[v]))),
    "a caller's feature name": ({"numeric-text"}, lambda v, mp: extract_tree(_fitted(), [v])),
    "an estimator's classes_": (set(), lambda v, mp: extract_tree(_fitted(regression=False, classes_=v), ["x"])),
    "a class in an estimator's classes_": (_LABELS, lambda v, mp: extract_tree(
        _fitted(regression=False, classes_=[v, "no"]), ["x"])),
    **{f"an estimator's tree_.{name}": (set(), lambda v, mp, name=name: extract_tree(_fitted(tree={name: v}), ["x"]))
       for name in ("children_left", "children_right", "feature", "threshold", "value")},
    **{f"an item of an estimator's tree_.{name}": (set(), lambda v, mp, name=name: extract_tree(
        _fitted(tree=_with_item(name, 0, v)), ["x"])) for name in ("children_left", "children_right", "feature",
                                                                   "threshold")},
    "a leaf's value row": (set(), lambda v, mp: extract_tree(_fitted(tree=_row(v)), ["x"])),
    "a regression leaf's value": (set(), lambda v, mp: extract_tree(_fitted(tree=_leaf(v)), ["x"])),
    "a classification leaf's score": (set(), lambda v, mp: extract_tree(
        _fitted(regression=False, tree=_leaf(v, regression=False)), ["x"])),
    # a learner the surrogate fits, and a black box's predictions, training and held-out alike (finding 4)
    "a learner's fitted leaf value": (set(), lambda v, mp: _distill(mp, _Box(None, 0), planted=(
        "tree_.value", [[[v]]]))),
    "a learner's fitted classes_": (set(), lambda v, mp: _distill(mp, _Box(None, 0, label=True), "classification",
                                                                  planted=("classes_", v))),
    "a learner's fitted n_features_in_": (set(), lambda v, mp: _distill(mp, _Box(None, 0), planted=(
        "n_features_in_", v))),
    "a regression training prediction": (set(), lambda v, mp: _distill(mp, _Box(v, 1))),
    "a regression held-out prediction": (set(), lambda v, mp: _distill(mp, _Box(v, 2))),
    "a black box's training answer": (set(), lambda v, mp: _distill(mp, _Box(v, 1, whole=True))),
    "a classification training prediction": (_LABELS, lambda v, mp: _distill(mp, _Box(v, 1, label=True),
                                                                             "classification")),
    "a classification held-out prediction": (_LABELS, lambda v, mp: _distill(mp, _Box(v, 2, label=True),
                                                                             "classification")),
    "a surrogate's feature name": ({"numeric-text"}, lambda v, mp: _distill(mp, _Box(None, 0), names=(v, "y"))),
    # a draft's card and a refusal's parts (finding 3), and a caller's value a maker reads
    "a draft's card": ({"null"}, lambda v, mp: settled(None, draft_card=v)),  # null is no card
    "a draft card's code": (set(), lambda v, mp: settled(None, draft_card=(v, "a message"))),
    "a draft card's message": ({"numeric-text"}, lambda v, mp: settled(None, draft_card=(INPUT, v))),
    "a refusal's code": (set(), lambda v, mp: refusal(v, "a reason")),
    "a refusal's reason": ({"numeric-text"}, lambda v, mp: refusal(INPUT, v)),
    "a supplied card": ({"null"}, lambda v, mp: refusal(INPUT, "a reason", card=v)),  # null is a new card
    "a supplied card's message": ({"numeric-text"}, lambda v, mp: refusal(
        INPUT, "a reason", card={**warning_card(INPUT, "a reason"), "message": v})),
    "a refusal handed to settled": ({"null"}, lambda v, mp: settled(v)),  # null is no refusal: a qualified draft
    "a refusal's reason handed to settled": ({"numeric-text"}, lambda v, mp: settled(
        {**refusal(INPUT, "a reason"), "reason": v})),
    "a result's field": (_LABELS, lambda v, mp: settled(None, answer=v)),
    "a refusal's extra field": (_LABELS, lambda v, mp: refusal(INPUT, "a reason", answer=v)),
    "a collaborator's mapping": (set(), lambda v, mp: only_fields(v, allowed=("a",), where="a mapping")),
    "a field of a collaborator's mapping": (_LABELS, lambda v, mp: only_fields({"a": v}, allowed=("a",),
                                                                                where="a mapping")),
}
_PLACE_CASES = [(place, kind) for place in sorted(PLACES) for kind in sorted(KINDS)]


@pytest.mark.parametrize("place,kind", _PLACE_CASES, ids=[f"{p}:{k}" for p, k in _PLACE_CASES])
def test_each_kind_planted_at_each_place_is_refused_unless_it_is_that_places_own(place, kind, monkeypatch):
    # refused: ValueError (NotPlainData, or a strict model's ValidationError), where the value is read; a maker's own
    # answer may itself be a refusal, so refusing the planted value is raising, never answering
    own, call = PLACES[place]
    if kind in own:
        call(KINDS[kind](), monkeypatch)
    else:
        with pytest.raises(ValueError):
            call(KINDS[kind](), monkeypatch)


def test_each_place_takes_a_value_of_its_own_kind():
    # every place above takes a valid value, so a refusal there is the planted value's, not the place's
    valid = {"an estimator's n_features_in_": 1, "an estimator's n_outputs_": 1, "an estimator's feature_names_in_": ["x"],
             "a name in an estimator's feature_names_in_": "x", "a caller's feature name": "x",
             "an estimator's classes_": ["yes", "no"], "a class in an estimator's classes_": "yes",
             "an estimator's tree_.children_left": [1, -1, -1], "an estimator's tree_.children_right": [2, -1, -1],
             "an estimator's tree_.feature": [0, -2, -2], "an estimator's tree_.threshold": [0.5, -2.0, -2.0],
             "an estimator's tree_.value": [[[0.0]], [[10.0]], [[20.0]]],
             "an item of an estimator's tree_.children_left": 1, "an item of an estimator's tree_.children_right": 2,
             "an item of an estimator's tree_.feature": 0, "an item of an estimator's tree_.threshold": 0.5,
             "a leaf's value row": [[10.0]], "a regression leaf's value": 10.0, "a classification leaf's score": 2.0,
             "a learner's fitted leaf value": 0.5, "a learner's fitted classes_": ["yes"],
             "a learner's fitted n_features_in_": 2, "a regression training prediction": 0.5,
             "a regression held-out prediction": 0.5, "a black box's training answer": [0.5] * 12,
             "a classification training prediction": "yes", "a classification held-out prediction": "yes",
             "a surrogate's feature name": "x", "a draft's card": (INPUT, "a message"),
             "a draft card's code": INPUT, "a draft card's message": "a message", "a refusal's code": INPUT,
             "a refusal's reason": "a reason", "a supplied card": warning_card(INPUT, "a reason"),
             "a supplied card's message": "a reason", "a refusal handed to settled": refusal(INPUT, "a reason"),
             "a refusal's reason handed to settled": "a reason", "a result's field": 1.0,
             "a refusal's extra field": 1.0, "a collaborator's mapping": {"a": 1}, "a field of a collaborator's mapping": 1}
    assert set(valid) == set(PLACES)
    monkeypatch = pytest.MonkeyPatch()
    try:
        for place, value in valid.items():
            PLACES[place][1](value, monkeypatch)
    finally:
        monkeypatch.undo()


# --- 3b. The block: every field, by infer, an exact CognitiveInput and G6's registry; a change after the screen -------

REQUESTS, BLOCK_PLACES = _conversions.REQUESTS, _conversions.PLACES


def _set_in_model(data, path, value):
    """``value`` set at ``path`` in a validated CognitiveInput, as a Python caller can, past its validation."""
    target = data
    for step in path[:-1]:
        target = getattr(target, step) if isinstance(target, BaseModel) else target[step]
    if isinstance(target, BaseModel):
        setattr(target, path[-1], value)
    else:
        target[path[-1]] = value


_SUBCLASS_CASES = [(op, path, kind) for op, path, _ in BLOCK_PLACES for kind in SUBCLASSES]


@pytest.mark.parametrize("op,path,kind", _SUBCLASS_CASES,
                         ids=[f"{o}:{'.'.join(map(str, p))}:{k}" for o, p, k in _SUBCLASS_CASES])
def test_a_container_subclass_in_every_field_is_refused_by_infer_an_exact_input_and_the_registry(op, path, kind):
    request = _conversions._planted(REQUESTS[op], path, KINDS[kind]())
    out = CognitiveBridgeBlock().infer(request).unwrap()
    assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT, out
    data = CognitiveInput.model_validate(REQUESTS[op])
    _set_in_model(data, path, KINDS[kind]())
    out = CognitiveBridgeBlock().infer(data).unwrap()
    assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT, out
    if path == ("op",):
        return  # the registry takes the op as its name, not as a field
    params = {k: v for k, v in request.items() if k != "op"}
    out = get_registry().invoke("cognitive_bridge", op, params)
    assert isinstance(out, Success), out
    assert out.unwrap()["completion_state"] == "blocked-escalated" and out.unwrap()["code"] == INPUT, out


_MODEL_CASES = [(op, path, kind) for op, path, hint in BLOCK_PLACES for kind in sorted(_conversions.PLANTED)
                if kind not in _conversions._own(hint)]


@pytest.mark.parametrize("op,path,kind", _MODEL_CASES,
                         ids=[f"{o}:{'.'.join(map(str, p))}:{k}" for o, p, k in _MODEL_CASES])
def test_what_is_not_a_fields_kind_set_in_an_exact_input_is_refused(op, path, kind):
    # test_conversion_inventory plants these through infer's plain dict and the registry; this is the exact model
    data = CognitiveInput.model_validate(REQUESTS[op])
    _set_in_model(data, path, _conversions.PLANTED[kind]())
    out = CognitiveBridgeBlock().infer(data).unwrap()
    assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT, out


def _changed(value):
    """``value`` with one leaf changed, kind for kind."""
    if type(value) is str:
        return value + "-changed"
    if type(value) is bool:
        return not value
    if type(value) in (int, float):
        return value / 2 if value else 1
    if type(value) is list:
        return [_changed(value[0]), *value[1:]] if value else ["changed"]
    if type(value) is dict:
        first = next(iter(value))
        return {**value, first: _changed(value[first])}
    return value


_CHANGE_CASES = [(op, path) for op, path, _ in BLOCK_PLACES if path != ("op",)]


@pytest.mark.parametrize("op,path", _CHANGE_CASES, ids=[f"{o}:{'.'.join(map(str, p))}" for o, p in _CHANGE_CASES])
def test_a_callers_change_after_the_screen_at_each_level_is_refused_or_never_answered(op, path, monkeypatch):
    params = {k: v for k, v in copy.deepcopy(REQUESTS[op]).items() if k != "op"}
    original = get_registry().invoke("cognitive_bridge", op, copy.deepcopy(params)).unwrap()
    assert original["completion_state"] == "qualified-draft", original
    screened = CognitiveBridgeBlock.screen_raw

    def change_after_screen(block, raw):
        verdict = screened(block, raw)
        target = params
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = _changed(target[path[-1]])
        return verdict

    monkeypatch.setattr(CognitiveBridgeBlock, "screen_raw", change_after_screen)
    out = get_registry().invoke("cognitive_bridge", op, params)
    if not isinstance(out, Success):
        assert "[G6_E_" in out.failure()
        return
    answer = out.unwrap()
    assert answer["completion_state"] == "blocked-escalated" or answer == original, answer


@pytest.mark.parametrize("op", sorted(REQUESTS))
def test_the_door_and_an_exact_input_from_python_answer_alike(op):
    params = {k: v for k, v in REQUESTS[op].items() if k != "op"}
    door = get_registry().invoke("cognitive_bridge", op, params).unwrap()
    python = CognitiveBridgeBlock().infer(CognitiveInput.model_validate(REQUESTS[op])).unwrap()
    assert door == python and door["completion_state"] == "qualified-draft", (door, python)


class _Input(CognitiveInput):
    pass


class _Claim(ClaimInput):
    pass


def test_the_exact_input_class_is_answered_and_a_subclass_refused():
    out = CognitiveBridgeBlock().infer(CognitiveInput.model_validate(REQUESTS["plan_methods"])).unwrap()
    assert out["completion_state"] == "qualified-draft", out
    refused = [
        _Input.model_validate(REQUESTS["plan_methods"]),
        CognitiveInput.model_construct(op="plan_methods",
                                       claims=[_Claim.model_validate(REQUESTS["plan_methods"]["claims"][0])]),
        CognitiveInput.model_construct(op=_Text("ops")),
        CognitiveInput.model_construct(op="update", hypotheses=[
            HypothesisInput.model_construct(id=_Text("a"), statement="a", prior=0.5)]),
    ]
    for data in refused:
        out = CognitiveBridgeBlock().infer(data).unwrap()
        assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT, (type(data), out)


def test_a_screened_read_is_answered_once_and_only_for_the_call_it_was_taken_for():
    block, request = CognitiveBridgeBlock(), REQUESTS["plan_methods"]
    other = _conversions._planted(request, ("claims", 0, "models", "formal", "formula"), "B")
    assert block.screen_raw(request) is None
    out = block.infer(other).unwrap()  # not the call that was screened: refused, and the read is spent
    assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT, out
    assert block.infer(other).unwrap()["steps"][0]["params"]["formula"] == "B"
    assert block.screen_raw(request) is None
    out = block.infer(CognitiveInput.model_validate(request)).unwrap()
    assert out["completion_state"] == "qualified-draft" and out["steps"][0]["params"]["formula"] == "A", out
    assert block.screen_raw({**request, "budget": math.nan}) is not None  # refused: nothing kept for infer
    assert block.infer(other).unwrap()["steps"][0]["params"]["formula"] == "B"
