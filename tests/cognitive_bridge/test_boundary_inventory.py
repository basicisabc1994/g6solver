# SPDX-License-Identifier: Apache-2.0
"""The two classes every review of the bridge met again, ``untrusted_input_given_authority`` and
``guard_covers_less_than_it_guards``, removed at their root (follow-up 2 of PR #8): the bridge took arbitrary Python
objects and caller-built records, and each repair taught a check one more shape of them. Now:

1. every value that enters from a caller or a collaborator is converted once, where it enters, into plain JSON data
   (``snapshot.plain``: a round trip through the bridge's own canonical encoder that refuses, with a registered code and
   the path, anything that does not survive it unchanged), and an identity is text;
2. ``execute`` and ``assess`` honour only a compilation, decision, dossier or report the instance that made it issued,
   by the digest of every field (``snapshot.issue`` and ``snapshot.issued``).

Each part is listed here from the code: every entry point of the package's public API, every record type and each of its
fields, every collaborator's answer and every tool's answer, every record ``execute`` and ``assess`` read, and every type
test in the package."""
from __future__ import annotations

import ast
import dataclasses
import inspect
import math
import textwrap
import typing
from collections import OrderedDict, deque
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel
from returns.result import Success

import g6.cognitive_bridge as bridge
from g6.cognitive_bridge import (
    DOCUMENT_FIELDS, ClaimIR, CognitiveAssuranceOrchestrator, CompiledResearch, ControlTrial, CritiqueCandidate,
    CritiqueReport, CrossMethodConflictGate, Criterion, DomainStandard, EvaluatedTrial, EvidenceArtifact,
    ExplicitTree, FailureModeFinding, FrozenVerifier, GuardChange, Hypothesis, IdeaHypothesis,
    InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodRouter, MethodStep, MethodVariant,
    MitigationHypothesis, MixedMethodExecutor, Observation, PairedTrial, Probe, ProblemSignature,
    ProfessionalAttestation, ScopedAssertion, ToolSpec, TreeNode, codes, snapshot,
)

PACKAGE = Path(snapshot.__file__).resolve().parent
#: The boundary: the functions that take a caller's or a collaborator's value into the bridge.
BOUNDARY = ("snapshot", "fresh", "plain", "text", "_prompt_value")


class _Text(str):
    pass


class _Map(dict):
    pass


#: Values no field and no entry point takes: a set, a mapping whose key is not text, a subclass of a JSON type, an
#: object, a number that is not finite.
NOT_JSON = {"set": lambda: {1}, "int-key": lambda: {1: "x"}, "text-subclass": lambda: _Text("x"),
            "dict-subclass": lambda: _Map(k="x"), "object": object, "nan": lambda: float("nan"),
            "deque": lambda: deque([1]), "ordered-dict": lambda: OrderedDict(k=1), "bytes-inside": lambda: [b"x"]}


def _refused(call, where: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        call()
    assert getattr(excinfo.value, "code", None) == codes.INPUT, f"{where}: {excinfo.value!r}"
    assert where in str(excinfo.value), f"the refusal does not name {where}: {excinfo.value}"


def _modules():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in sorted(PACKAGE.glob("*.py"))]


def _record_classes() -> dict[str, type]:
    """Every record type in the package, from the code: the classes decorated ``@record``."""
    found = {}
    for module, tree in _modules():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if [ast.unparse(d) for d in cls.decorator_list] == ["record"]:
                found[cls.name] = getattr(__import__(f"g6.cognitive_bridge.{module[:-3]}", fromlist=["x"]), cls.name)
    return found


# --- 1. Every entry point takes each data parameter through the boundary before anything reads it -------------------

#: The public API's classes that are not entry points of their own, with why.
NOT_ENTRIES = {
    "CognitiveBridgeBlock": "the MCP door's block: its arguments are read raw by the boundary (snapshot.plain, in "
                            "screen_raw at G6's door and in infer), then by strict models (CognitiveInput, extra=forbid,"
                            " strict), listed and planted in test_conversion_inventory.py; every record it builds goes "
                            "through the record boundary",
}


def _code_types() -> set[str]:
    """Annotation names that are code a caller hands in (a collaborator), not data: ``Any``, ``Callable`` and every
    class in the package that is not a record, a pydantic model or the block."""
    names = {"Any", "Callable"}
    for _, tree in _modules():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            bases = [ast.unparse(b) for b in cls.bases]
            if "record" in [ast.unparse(d) for d in cls.decorator_list] or any(
                    b in ("BaseModel", "dict") or b.startswith("AIBlock") for b in bases):
                continue
            names.add(cls.name)
    return names


def _outer_names(annotation: ast.AST | None) -> set[str]:
    if annotation is None:
        return set()
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return _outer_names(ast.parse(annotation.value, mode="eval").body)
    if isinstance(annotation, ast.BinOp):
        return _outer_names(annotation.left) | _outer_names(annotation.right)
    if isinstance(annotation, ast.Subscript):
        outer = ast.unparse(annotation.value).split(".")[-1]
        if outer in ("Optional", "Union"):
            inner = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
            return set().union(*(_outer_names(item) for item in inner))
        return {outer}
    return {ast.unparse(annotation).split(".")[-1]}


def entries():
    """Every entry point of the package's public API (``g6.cognitive_bridge.__all__``): each public function, and each
    public class's own ``__init__`` and public methods with source (a record's generated ``__init__`` is the record
    boundary, below)."""
    for name in bridge.__all__:
        obj = getattr(bridge, name)
        if inspect.isfunction(obj):
            yield name, obj
        elif inspect.isclass(obj) and name not in NOT_ENTRIES and not issubclass(obj, BaseModel):
            for attr, member in vars(obj).items():
                member = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
                if (attr == "__init__" or not attr.startswith("_")) and inspect.isfunction(member):
                    try:
                        inspect.getsource(member)
                    except (OSError, TypeError):
                        continue  # generated by dataclasses: the record boundary
                    yield f"{name}.{attr}", member


def _parents(tree):
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _within(node, ancestor, parent) -> bool:
    while node is not None:
        if node is ancestor:
            return True
        node = parent.get(node)
    return False


def untaken(function) -> list[str]:
    """The data parameters of ``function`` read before they are taken through the boundary: for each, the first read
    must be inside the first argument of a boundary call, whose result is bound to it again (``x = snapshot(x)``,
    ``a, b = snapshot((a, b))``) or, when it is bound elsewhere (``self.x = snapshot(x)``), that call is the only
    read; or every read is ``affirmed(x)`` (a yes read as ``True`` and nothing else)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    fn = tree.body[0]
    parent = _parents(tree)
    code = _code_types()
    # Admission reads only exact native kind/length or the owned schema. It
    # neither converts nor replaces the caller's value; snapshot is still
    # required as the first subsequent read. One-over instrumentation covers
    # each of these helpers in test_followup4_repair1_limits.
    admissions = {'_admit_length', '_admit_hypotheses', '_admit_claims'}
    def admission_read(node):
        up = parent.get(node)
        while up is not None and not isinstance(up, ast.stmt):
            if isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id in admissions:
                return True
            up = parent.get(up)
        return False
    bad = []
    for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
        if arg.arg in ("self", "cls") or _outer_names(arg.annotation) & code:
            continue
        loads = sorted((n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == arg.arg
                        and isinstance(n.ctx, ast.Load) and not admission_read(n)), key=lambda n: (n.lineno, n.col_offset))
        if not loads:
            continue
        if all(isinstance(parent[n], ast.Call) and ast.unparse(parent[n].func) == "affirmed"
               and parent[n].args == [n] for n in loads):
            continue
        first, call = loads[0], parent[loads[0]]
        while call is not None and not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                                        and call.func.id in BOUNDARY and call.args
                                        and _within(first, call.args[0], parent)):
            call = parent.get(call)
        assign = call
        while assign is not None and not isinstance(assign, ast.Assign):
            assign = parent.get(assign)
        if call is None or assign is None or len(assign.targets) != 1:
            bad.append(f"{arg.arg} (first read at line {first.lineno}: {ast.unparse(parent[first])[:80]})")
            continue
        # Which name the taken value is bound to: x = snapshot(x); a, b = snapshot((a, b)); self.x, y = x, text(y)
        target, value = assign.targets[0], assign.value
        if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
            target = next(t for t, v in zip(target.elts, value.elts) if _within(call, v, parent))
        elif isinstance(target, ast.Tuple) and isinstance(call.args[0], ast.Tuple):
            target = next(t for t, v in zip(target.elts, call.args[0].elts) if _within(first, v, parent))
        rebinds = isinstance(target, ast.Name) and target.id == arg.arg
        inside = [n for n in loads if _within(n, call.args[0], parent)]
        ok = (all(n in inside for n in loads if _within(n, assign, parent)) if rebinds
              else len(inside) == len(loads))
        if not ok:
            bad.append(f"{arg.arg} (first read at line {first.lineno}: {ast.unparse(parent[first])[:80]})")
    return bad


def test_the_entry_inventory_finds_the_entry_points():
    found = {name for name, _ in entries()}
    for name in ("CognitiveAssuranceOrchestrator.compile", "CognitiveAssuranceOrchestrator.execute",
                 "CognitiveAssuranceOrchestrator.assess", "InProcessAssuranceEvaluator.__init__",
                 "InProcessAssuranceEvaluator.evaluate", "MixedMethodExecutor.execute", "CognitiveAgent.run",
                 "CrossMethodConflictGate.reconcile", "MetacognitiveMethodSelector.select", "distill_blackbox",
                 "CandidateDossier.from_payload", "ExplicitTree.explain_one", "LLMProposer.propose"):
        assert name in found, f"the inventory did not find {name}"
    assert len(found) >= 40


@pytest.mark.parametrize("name", sorted(name for name, _ in entries()))
def test_every_entry_point_takes_its_data_through_the_boundary_first(name):
    function = dict(entries())[name]
    assert untaken(function) == [], f"{name} reads a caller's value before the boundary takes it"


# --- 2. Every record type converts each field where it is made --------------------------------------------------------

def _compiled():
    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64,
                              (Criterion("c", "formal obligation", "checker", "1:1", required_methods=("formal",)),))
    orch = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector())
    return orch.compile(claim=ClaimIR("claim", "A entails A", ("formal",), {}),
                        context=ProblemSignature("review", "logic", "validity", 1, 2), standard=standard,
                        variants=[MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
                        variant_models={"v": {"formula": "A"}})


def _dossier():
    standard = DomainStandard("d", "1", "AU", "a", "a" * 64, (Criterion("c", "d", "checker", "1:1"),))
    return InProcessAssuranceEvaluator(evaluator_id="outside").evaluate(
        standard=standard, artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "s", "claim")},
        verifiers={"checker": FrozenVerifier("checker", "outside", "b" * 64, lambda b: b == b"GOOD", (b"GOOD",),
                                             (b"BAD",))},
        documentation={name: "Explicit limits" for name in DOCUMENT_FIELDS}, candidate_actor="candidate")


#: A valid instance of every record type, made the way a caller or the bridge makes it.
MADE = {
    "Hypothesis": lambda: Hypothesis("A", "a", .5),
    "Probe": lambda: Probe("p", "q", "read", {"A": True}, .8),
    "Observation": lambda: Observation(True, "source"),
    "ToolSpec": lambda: ToolSpec("read", lambda q, a: Observation(True, "source")),
    "IdeaHypothesis": lambda: IdeaHypothesis("i", "s", "a", "f"),
    "ClaimIR": lambda: ClaimIR("c", "s", ("formal",), {"formal": {"formula": "A"}}),
    "MethodStep": lambda: MethodStep("s", "c", "formal", "formal", "check_validity", {"formula": "A"}, "m"),
    "MethodPlan": lambda: MethodRouter().plan([ClaimIR("c", "s", ("formal",), {"formal": {"formula": "A"}})]),
    "ProblemSignature": lambda: ProblemSignature("d", "u", "k", 1, 2),
    "MethodVariant": lambda: MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1),
    "EvaluatedTrial": lambda: EvaluatedTrial("t", "case", "cohort", "d", "u", "k", "v", True, 1.0, "e", "b" * 64),
    "SelectionDecision": lambda: MetacognitiveMethodSelector().select(
        ProblemSignature("d", "u", "k", 1, 2), [MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
        required_methods=["formal"]),
    "Criterion": lambda: Criterion("c", "d", "checker", "1:1"),
    "DomainStandard": lambda: DomainStandard("d", "1", "AU", "a", "a" * 64, (Criterion("c", "d", "checker", "1:1"),)),
    "EvidenceArtifact": lambda: EvidenceArtifact("e", b"GOOD", "report", "candidate", "s", "claim"),
    "FrozenVerifier": lambda: FrozenVerifier("checker", "outside", "b" * 64, lambda b: True, (b"G",), (b"B",)),
    "ProfessionalAttestation": lambda: ProfessionalAttestation("r", "role", "a" * 64, "sig"),
    "CandidateDossier": _dossier,
    "CritiqueCandidate": lambda: CritiqueCandidate("f", "a", "c", "formal", "e"),
    "CritiqueReport": lambda: CritiqueReport((CritiqueCandidate("f", "a", "c", "formal", "e"),), "objective"),
    "FailureModeFinding": lambda: FailureModeFinding("code", "c", "d", "e", "detail", "critical"),
    "MitigationHypothesis": lambda: MitigationHypothesis("m", "code", "r", "g", "f"),
    "GuardChange": lambda: GuardChange("g", "a" * 64, "b" * 64, "c" * 64, ("src/x.py",), "cognitive_bridge",
                                       "candidate", "outside", ("false_confidence",)),
    "PairedTrial": lambda: PairedTrial("case", "c" * 64, False, True, True, "holdout", "receipt"),
    "ControlTrial": lambda: ControlTrial("case", True, "positive", "receipt"),
    "CompiledResearch": _compiled,
    "ScopedAssertion": lambda: ScopedAssertion("c", "p", "a" * 64, "supports", "formal", "source", "receipt"),
    "TreeNode": lambda: TreeNode(value=1),
    "ExplicitTree": lambda: ExplicitTree(("x",), TreeNode(value=1), "classification", "tree_transcription"),
}


def test_every_record_type_has_an_instance_here():
    assert set(MADE) == set(_record_classes()), "a record type with no instance in MADE"
    for name, make in MADE.items():
        assert type(make()) is _record_classes()[name]


def _fields():
    return sorted((name, field.name) for name, cls in _record_classes().items() for field in dataclasses.fields(cls))


@pytest.mark.parametrize("kind", sorted(NOT_JSON))
@pytest.mark.parametrize("name,field", _fields())
def test_every_field_of_every_record_refuses_what_is_not_its_kind(name, field, kind):
    if kind == "bytes-inside" and typing.get_type_hints(_record_classes()[name])[field] == tuple[bytes, ...]:
        pytest.skip("a list of bytes is what a field declared as a tuple of bytes holds")
    made = MADE[name]()
    _refused(lambda: replace(made, **{field: NOT_JSON[kind]()}), f"{name}.{field}")


def _text_fields():
    out = []
    for name, cls in _record_classes().items():
        hints = typing.get_type_hints(cls)
        out += [(name, f.name) for f in dataclasses.fields(cls) if hints[f.name] is str]
    return sorted(out)


@pytest.mark.parametrize("name,field", _text_fields())
def test_every_text_field_takes_only_text(name, field):
    made = MADE[name]()
    for value in ({"id": "x"}, ["x"], 1, None):
        _refused(lambda value=value: replace(made, **{field: value}), f"{name}.{field}")


def test_a_record_holds_what_it_took_frozen_and_shared_with_no_one():
    given = {"formal": {"formula": "A", "premises": ["A"]}}
    claim = ClaimIR("c", "s", ("formal",), given)
    given["formal"]["premises"].append("B")
    assert claim.models == {"formal": {"formula": "A", "premises": ("A",)}}
    with pytest.raises(TypeError):
        claim.models["formal"]["formula"] = "B"


# --- 3. Every collaborator's answer and every tool's answer goes through the conversion ------------------------------

@pytest.mark.parametrize("kind", sorted(NOT_JSON))
def test_a_collaborators_answer_that_is_not_plain_data_is_refused_as_it_returns(kind):
    def as_given(value):
        return value

    for answer in (NOT_JSON[kind](), [NOT_JSON[kind]()], {"k": NOT_JSON[kind]()}):
        _refused(lambda answer=answer: snapshot.returned(answer, as_given), "an answer")


def test_every_collaborators_answer_is_taken_by_returned_and_returned_takes_it_through_the_boundary():
    # returned() reads what a collaborator returned only through fresh(); test_collaborator_inventory.py lists every
    # call of a collaborator from the code and fails on any answer used other than through returned() or affirmed()
    assert untaken(snapshot.returned) == []
    calls = [n for _, tree in _modules() for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "returned"]
    assert calls and all(isinstance(n.args[0], ast.Call) for n in calls), [ast.unparse(n)[:60] for n in calls]


def _door_replies():
    """Every read of a door reply's ``result`` in the package: (module, line, the call that reads it)."""
    found = []
    for module, tree in _modules():
        parent = _parents(tree)
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            replies = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                       and ast.unparse(n.value.func).endswith(("invoke_checked", "_invoke"))
                       for t in n.targets if isinstance(t, ast.Name)}
            for node in ast.walk(fn):
                read = (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                        and node.value.id in replies and ast.unparse(node.slice) == "'result'"
                        and isinstance(node.ctx, ast.Load)) or (
                    isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in replies
                    and node.args and ast.unparse(node.args[0]) == "'result'")
                if read:
                    up = parent[node]
                    taken = isinstance(up, ast.Call) and getattr(up.func, "id", "") == "plain" and up.args[0] is node
                    found.append((module, node.lineno, taken))
    return found


def test_every_tools_answer_the_door_hands_on_is_taken_through_the_boundary():
    found = _door_replies()
    assert {m for m, _, _ in found} == {"mixed_methods.py", "engine.py"}, found
    assert [f"{m}:{line}" for m, line, taken in found if not taken] == []


@pytest.mark.parametrize("kind", sorted(NOT_JSON))
def test_a_tools_answer_that_is_not_plain_data_fails_its_step(kind):
    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": {"held": NOT_JSON[kind]()}})

    plan = MethodRouter().plan([ClaimIR("c", "check A", ("formal",), {"formal": {"formula": "A"}})])
    out = MixedMethodExecutor(Registry()).execute(plan, approved=True)
    step = out["steps"][0]
    if kind in ("dict-subclass", "ordered-dict") and step["completion_state"] == "qualified-draft":
        # G6's door hands a dict subclass on as its own plain dict: what the bridge credits is that plain data
        assert type(step["result"]["answer"]["held"]) is dict
        return
    assert step["code"] == codes.STEP_FAILED and "result" not in step and "answer.held" in step["reason"], step


def test_the_boundary_round_trips_through_the_canonical_encoder_and_shares_nothing():
    value = {"a": [1, 2.5, "x", True, None, {"b": []}], "c": {}}
    taken = snapshot.plain(value, where="a value")
    assert taken == value and taken is not value and taken["a"] is not value["a"]
    assert snapshot.canonical_json(taken) == snapshot.canonical_json(value)
    for frozen in (snapshot.snapshot(value), snapshot.fresh(value)):
        assert frozen == {"a": (1, 2.5, "x", True, None, {"b": ()}), "c": {}}
    for kind, make in NOT_JSON.items():
        _refused(lambda make=make: snapshot.plain({"k": [make()]}, where="a value"), "a value")
    _refused(lambda: snapshot.plain(math.inf, where="a value"), "a value")
    deep: list = []
    for _ in range(codes.DEPTH):  # the innermost list stands at depth DEPTH: taken
        deep = [deep]
    assert snapshot.plain(deep, where="a value") == deep and codes.claims(deep) == []
    deeper = [deep]  # at depth DEPTH + 1: refused, by the boundary and by the claim rule alike
    _refused(lambda: snapshot.plain(deeper, where="a value"), "a value")
    assert codes.claims(deeper)
    _refused(lambda: snapshot.text({"id": "x"}, where="an identity"), "an identity")


# --- 4. execute and assess honour only records this instance issued ---------------------------------------------------

def _function(cls, name):
    fn = vars(cls)[name]
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


def _calls(fn, name):
    return [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name]


def _own_returns(fn):
    """The return statements of ``fn`` itself, not of a function nested in it."""
    parent = _parents(fn)
    found = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return):
            up = parent[node]
            while not isinstance(up, (ast.FunctionDef, ast.Lambda)):
                up = parent[up]
            if up is fn:
                found.append(node)
    return found


def test_every_maker_issues_what_it_returns():
    for cls, name in ((CognitiveAssuranceOrchestrator, "compile"), (MetacognitiveMethodSelector, "select"),
                      (InProcessAssuranceEvaluator, "evaluate"), (CrossMethodConflictGate, "reconcile")):
        returns = _own_returns(_function(cls, name))
        assert returns and all(isinstance(r.value, ast.Call) and getattr(r.value.func, "id", "") == "publish"
                               and isinstance(r.value.args[0], ast.Call)
                               and getattr(r.value.args[0].func, "id", "") == "issue"
                               and ast.unparse(r.value.args[0].args[0]) == "self"
                               for r in returns), f"{cls.__name__}.{name}"


def test_every_record_execute_and_assess_read_is_checked_against_the_issued_set():
    execute, assess = (_function(CognitiveAssuranceOrchestrator, n) for n in ("execute", "assess"))
    compile_ = _function(CognitiveAssuranceOrchestrator, "compile")
    assert "issued(self, compiled)" in _calls(execute, "issued")
    assert {"issued(self, compiled)", "issued(evaluator, dossier)", "issued(gate, conflict_report)"} <= set(
        _calls(assess, "issued"))
    assert "issued(self.selector, selection)" in _calls(compile_, "issued")


def _alternatives(compiled, research):
    """Another valid value for each field of a compilation."""
    reviewer = MethodVariant("reviewer", "professional_review", "human", "reviewer", "review", 1, kind="reviewer")
    other_plan = MethodRouter().plan([replace(compiled.original_claim, models={"formal": {"formula": "B"}})])
    return {
        "original_claim": replace(compiled.original_claim, statement="another claim"),
        "executable_plan": other_plan,
        "decision": research["selector"].select(compiled.context, [reviewer], required_methods=["professional_review"]),
        "standard_digest": "f" * 64,
        "context": replace(compiled.context, claim_kind="another kind"),
        "selected_model_hashes": {"v": "0" * 64},
        "refused": codes.refusal(codes.COMPILE_BLOCKED, "invented"),
        "plan_digest": "0" * 64,
    }


@pytest.fixture
def research():
    class Registry:
        def __init__(self):
            self.calls = []

        def invoke(self, name, op, params=None, **kwargs):
            self.calls.append((name, op))
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})

    selector, registry = MetacognitiveMethodSelector(), Registry()
    orch = CognitiveAssuranceOrchestrator(selector, registry=registry)
    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64,
                              (Criterion("c", "formal obligation", "checker", "1:1", required_methods=("formal",)),))
    compiled = orch.compile(claim=ClaimIR("claim", "A entails A", ("formal",), {}),
                            context=ProblemSignature("review", "logic", "validity", 1, 2), standard=standard,
                            variants=[MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
                            variant_models={"v": {"formula": "A", "premises": ["A"]}})
    evaluation = dict(
        standard=standard, evaluator=InProcessAssuranceEvaluator(evaluator_id="outside"),
        artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "store:e", "claim")},
        verifiers={"checker": FrozenVerifier("checker", "outside", "b" * 64, lambda b: b == b"GOOD", (b"GOOD",),
                                             (b"BAD",))},
        documentation={name: "Explicit limits" for name in DOCUMENT_FIELDS}, candidate_actor="candidate")
    return dict(orch=orch, selector=selector, registry=registry, compiled=compiled, evaluation=evaluation)


def test_every_field_of_a_compilation_has_an_alternative_here(research):
    fields = {f.name for f in dataclasses.fields(CompiledResearch)}
    assert set(_alternatives(research["compiled"], research)) == fields


@pytest.mark.parametrize("field", sorted(f.name for f in dataclasses.fields(CompiledResearch)))
def test_a_compilation_edited_in_any_field_neither_runs_nor_is_credited(research, field):
    c = research
    run = c["orch"].execute(c["compiled"], approved=True)
    calls = list(c["registry"].calls)
    try:
        forged = replace(c["compiled"], **{field: _alternatives(c["compiled"], c)[field]})
    except ValueError:
        return  # refused as it is made (its refused field is the one its selection and plan give)
    out = c["orch"].execute(forged, approved=True)
    # a compilation whose plan no longer matches its own pins is PLAN_TAMPERED; any other not issued is NOT_APPROVED
    assert out["code"] in (codes.EXECUTION_NOT_APPROVED, codes.PLAN_TAMPERED) and c["registry"].calls == calls, field
    assessment = c["orch"].assess(forged, run_result=run, **c["evaluation"])
    assert assessment["completion_state"] == "blocked-escalated" and assessment["code"] == codes.ASSURANCE_BLOCKED
    assert assessment["method_results"] == {}


def test_a_compilation_another_instance_made_is_not_honoured_and_a_restart_honours_nothing(research):
    c = research
    run = c["orch"].execute(c["compiled"], approved=True)
    restarted = CognitiveAssuranceOrchestrator(c["selector"], registry=c["registry"])
    calls = list(c["registry"].calls)
    assert restarted.execute(c["compiled"], approved=True)["code"] == codes.EXECUTION_NOT_APPROVED
    assert c["registry"].calls == calls
    out = restarted.assess(c["compiled"], run_result=run, **c["evaluation"])
    assert out["code"] == codes.ASSURANCE_BLOCKED and "not one this orchestrator compiled" in out["reason"]


def test_a_compilation_changed_past_every_view_after_it_was_issued_is_refused(research):
    c = research
    compiled = c["compiled"]
    dict.__setitem__(compiled.executable_plan.steps[0].params, "formula", "B")
    out = c["orch"].execute(compiled, approved=True)
    assert out["code"] in (codes.EXECUTION_NOT_APPROVED, codes.PLAN_TAMPERED) and c["registry"].calls == []
    dict.__setitem__(compiled.executable_plan.steps[0].params, "formula", "A")  # the plan as compiled again
    dict.__setitem__(compiled.decision.ordered_variants[0], "ranking_note", "edited past every view")
    out = c["orch"].execute(compiled, approved=True)
    assert out["code"] == codes.EXECUTION_NOT_APPROVED and c["registry"].calls == []
    object.__setattr__(compiled, "context", {1, 2})  # unreadable: not a record and not data
    out = c["orch"].execute(compiled, approved=True)
    assert out["completion_state"] == "blocked-escalated" and c["registry"].calls == []


def test_a_dossier_another_evaluator_issued_is_not_credited(research):
    c = research
    run = c["orch"].execute(c["compiled"], approved=True)
    inputs = {k: v for k, v in c["evaluation"].items() if k != "evaluator"}
    made = InProcessAssuranceEvaluator(evaluator_id="outside").evaluate(**inputs)

    class Replay:
        def evaluate(self, **kwargs):
            return made

    class Relay(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            return made

    for evaluator in (Replay(), Relay(evaluator_id="outside")):
        out = c["orch"].assess(c["compiled"], run_result=run, **{**c["evaluation"], "evaluator": evaluator})
        assert out["code"] == codes.ASSURANCE_BLOCKED and "not one this evaluator issued" in out["reason"], out
    out = c["orch"].assess(c["compiled"], run_result=run, **c["evaluation"])
    assert out["completion_state"] == "qualified-draft", out.get("reason")


def test_a_report_another_gate_issued_is_not_credited(research):
    c = research
    run = c["orch"].execute(c["compiled"], approved=True)
    assertions = [ScopedAssertion("claim", "truth", "a" * 64, "supports", "formal", "source1", "sig1")]
    made = CrossMethodConflictGate(lambda a: True).reconcile(assertions)

    class Relay(CrossMethodConflictGate):
        def reconcile(self, assertions):
            return made

    out = c["orch"].assess(c["compiled"], run_result=run, scoped_assertions=assertions,
                           epistemic_gate=Relay(lambda a: True), **c["evaluation"])
    assert out["code"] == codes.ASSURANCE_BLOCKED and "not one this gate issued" in out["reason"], out
    out = c["orch"].assess(c["compiled"], run_result=run, scoped_assertions=assertions,
                           epistemic_gate=CrossMethodConflictGate(lambda a: True), **c["evaluation"])
    assert out["completion_state"] == "qualified-draft", out.get("reason")


def test_a_decision_another_selector_issued_is_not_compiled(research):
    c = research
    other = MetacognitiveMethodSelector()

    class Relay(MetacognitiveMethodSelector):
        def select(self, *args, **kwargs):
            return other.select(*args, **kwargs)

    with pytest.raises(ValueError, match="not one this selector issued"):
        CognitiveAssuranceOrchestrator(Relay()).compile(
            claim=c["compiled"].original_claim, context=c["compiled"].context, standard=c["evaluation"]["standard"],
            variants=[MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
            variant_models={"v": {"formula": "A"}})


# --- 5. Nothing after the boundary tests a Python type other than the JSON ones --------------------------------------

#: The JSON types, as the bridge holds them (``FrozenMap`` and ``tuple`` are its frozen object and array), and bytes,
#: which only a field a record declares as bytes holds.
JSON_TYPES = {"dict", "list", "tuple", "str", "int", "float", "bool", "None", "NoneType", "bytes", "FrozenMap"}
#: Where a type other than those is tested, with why: the boundary itself, and the MCP block's pydantic input.
OUTSIDE = {"snapshot.py": "the boundary: it is where a caller's value is tested and converted",
           "block.py:CognitiveInput": "the MCP door's block: what MCP sends, validated by its pydantic model",
           "block.py:_NESTED": "the MCP door's block: an exact CognitiveInput, and each exact model it holds, read by "
                               "its fields for the boundary (block._as_given, review 3 of PR #8's second follow-up, "
                               "finding 2); any other class is handed to the boundary as it is, and refused there"}
#: The names block.py may test a type against, from OUTSIDE.
_BLOCK_MODELS = {key.split(":", 1)[1] for key in OUTSIDE if key.startswith("block.py:")}


def _type_tests():
    """Every type test in the package: (module, line, the names of the types it tests)."""
    found = []
    for module, tree in _modules():
        for node in ast.walk(tree):
            tested = []
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("isinstance", "issubclass"):
                tested = [node.args[1]]
            elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Call) and getattr(
                    node.left.func, "id", "") == "type":
                tested = node.comparators
            if tested:
                names = {n.id for t in tested for n in ast.walk(t) if isinstance(n, ast.Name) and n.id != "type"}
                found.append((module, node.lineno, names))
    return found


def test_nothing_after_the_boundary_tests_a_type_other_than_the_json_ones():
    allowed = JSON_TYPES | set(_record_classes()) | {"LEAVES", "OBJECTS", "ARRAYS"}
    bad = [f"{m}:{line} {sorted(names - allowed)}" for m, line, names in _type_tests()
           if m != "snapshot.py" and names - allowed and not (m == "block.py" and names <= _BLOCK_MODELS)]
    assert bad == [], "a type test after the boundary: " + "; ".join(bad)
    assert {kind.__name__ for kind in codes.LEAVES + codes.OBJECTS + codes.ARRAYS} <= JSON_TYPES
