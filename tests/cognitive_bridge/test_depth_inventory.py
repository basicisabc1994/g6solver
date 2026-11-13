# SPDX-License-Identifier: Apache-2.0
"""The class boundary_case, removed at its root for the depth bound (review 1 of PR #8's second follow-up, finding 1):
``snapshot._taken`` handed a subtree to ``plain`` with the count started again, so a record beside the path let data
nested deeper than ``codes.DEPTH`` into a snapshot that the claim rule then refused. The bound is one count, from where
a value enters: every walk in the package that recurses into a caller's or a collaborator's value takes its depth from
its caller, hands it on to every walk it gives an item to, and counts it against ``codes.DEPTH``.

This lists those walks from the code (a function that calls itself, or another such walk, on an item of its argument)
and checks each one. Then, for each place a value enters (``snapshot``, ``fresh``, ``plain`` and the claim rule over
``view``, a record's digest, a collaborator's answer, a record's field where it is made, a model's reply, a tool's
answer through the agent and through the executor, a fitted tree), a leaf at depth ``DEPTH`` is taken and one at
``DEPTH + 1`` is refused: with no record, with a record beside the path at its first level, its middle and its last, and
with records inside the path (a record's data field, a record field holding records, a field holding a list of
records)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from returns.result import Success

import g6.cognitive_bridge.mixed_methods as mixed_methods
from g6.cognitive_bridge import (
    ClaimIR, CognitiveAgent, Criterion, DomainStandard, Hypothesis, MethodRouter, MixedMethodExecutor, Observation,
    Probe, StaticProposer, ToolSpec, TreeNode, codes, extract_tree,
)
from g6.cognitive_bridge.codes import DEPTH
from g6.cognitive_bridge.snapshot import digest, fresh, json_reply, plain, returned, snapshot, view
from g6.core import door

PACKAGE = Path(codes.__file__).resolve().parent
DEPTH_NAMES = ("depth", "_depth")


# --- The walks, from the code ------------------------------------------------------------------------------------------

def _functions():
    """Every function in the package, nested ones too: (module, dotted name, node, whether it is a method)."""
    found = []

    def visit(node, module, prefix, in_class):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, module, f"{prefix}{child.name}.", True)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((module, f"{prefix}{child.name}", child, in_class))
                visit(child, module, f"{prefix}{child.name}.", False)
            else:
                visit(child, module, prefix, in_class)

    for path in sorted(PACKAGE.glob("*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), path.name, "", False)
    return found


def _params(fn) -> list[str]:
    return [a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]]


def _callee(call: ast.Call, in_class: bool) -> str | None:
    """The name a call calls, when it calls a function of this package by name: ``f(...)``, or a method of the same
    class through ``self.f(...)`` / ``cls.f(...)``."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if in_class and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and \
            call.func.value.id in ("self", "cls"):
        return call.func.attr
    return None


def _reads(node, names) -> bool:
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _is_item(expr, derived) -> bool:
    """Whether ``expr`` is an item of a value named in ``derived``: a subscript or an attribute of it, or a getattr."""
    if isinstance(expr, (ast.Subscript, ast.Attribute)):
        return _reads(expr.value, derived)
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "getattr"
            and bool(expr.args) and _reads(expr.args[0], derived))


def _items(fn) -> set[str]:
    """The names ``fn`` binds to an item of one of its parameters (or of such an item): a loop or comprehension target
    over an expression that reads one, or a name assigned a subscript, an attribute or a getattr of one."""
    derived, items, changed = set(_params(fn)) - {"self", "cls"}, set(), True
    while changed:
        changed = False
        for node in ast.walk(fn):
            pairs = []
            if isinstance(node, (ast.For, ast.comprehension)) and _reads(node.iter, derived):
                pairs = [(node.target, None)]
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                        pairs += [(t, v) for t, v in zip(target.elts, node.value.elts) if _is_item(v, derived)]
                    elif _is_item(node.value, derived):
                        pairs.append((target, node.value))
            for target, _ in pairs:
                for name in [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]:
                    if name not in items:
                        items.add(name)
                        derived.add(name)
                        changed = True
    return items


def _item_argument(call, fn) -> bool:
    items, derived = _items(fn), set(_params(fn)) - {"self", "cls"}
    args = [*call.args, *(k.value for k in call.keywords)]
    return any((isinstance(a, ast.Name) and a.id in items) or _is_item(a, derived | items) for a in args)


def walks():
    """Every walk in the package: a function that calls itself, or calls another walk into a value on an item of its
    argument."""
    functions = _functions()
    found = {(m, name) for m, name, fn, in_class in functions
             if any(isinstance(c, ast.Call) and _callee(c, in_class) == fn.name for c in ast.walk(fn))}
    changed = True
    while changed:
        changed = False
        names = {name.split(".")[-1] for key in found - set(NOT_INTO_A_VALUE) for name in [key[1]]}
        for m, name, fn, in_class in functions:
            if (m, name) in found:
                continue
            if any(isinstance(c, ast.Call) and _callee(c, in_class) in names and _item_argument(c, fn)
                   for c in ast.walk(fn)):
                found.add((m, name))
                changed = True
    return found


#: The walks, compared whole: a new one is listed here, and checked below.
EXPECTED_WALKS = {
    ("codes.py", "claims"),
    ("metacognition.py", "MetacognitiveMethodSelector.select.search"),
    ("snapshot.py", "_frozen"), ("snapshot.py", "_has_record"), ("snapshot.py", "_not_json"),
    ("snapshot.py", "_renewed"), ("snapshot.py", "_same"), ("snapshot.py", "_taken"), ("snapshot.py", "view"),
    ("snapshot.py", "_walk"),
    ("snapshot.py", "_encoded_size.visit"),
    ("xai.py", "extract_tree.visit"),
}
#: A function that calls itself but does not recurse into a value, with why.
NOT_INTO_A_VALUE = {
    ("snapshot.py", "_encoded_size.visit"):
        "only a detached, depth-admitted projection from _admit_json reaches this exact ASCII-size counter; "
        "the raw-input depth refusal still runs in view's _walk before it",
    ("metacognition.py", "MetacognitiveMethodSelector.select.search"):
        "it recurses over the selector's own list of pools by index, one level per required method: its depth is the "
        "number of methods required, not the nesting of a caller's value",
}


def _depth_takers() -> dict[str, tuple[int, str]]:
    """Every function in the package that takes a depth: its name -> (the depth parameter's position, its name)."""
    out = {}
    for _, name, fn, in_class in _functions():
        params = _params(fn)
        for depth in DEPTH_NAMES:
            if depth in params:
                position = params.index(depth) - (1 if in_class and params[:1] in (["self"], ["cls"]) else 0)
                out[name.split(".")[-1]] = (position, depth)
    return out


def test_the_walks_are_listed_from_the_code():
    assert walks() == EXPECTED_WALKS
    assert set(NOT_INTO_A_VALUE) <= EXPECTED_WALKS


def test_what_a_walk_hands_a_subtree_to_takes_the_count_too():
    # the boundary's conversion, its text, the view the claim rule reads and the rule itself: a walk hands each a
    # subtree, so each takes the depth it is handed (finding 1: _taken handed plain a subtree and plain began at 0)
    takers = _depth_takers()
    missing = [name for name in ("plain", "text", "view", "claims") if name not in takers]
    assert missing == [], f"these start the count again: {missing}"


@pytest.mark.parametrize("module,name", sorted(EXPECTED_WALKS - set(NOT_INTO_A_VALUE)))
def test_every_walk_takes_its_depth_from_its_caller_hands_it_on_and_bounds_it(module, name):
    fn = next(f for m, n, f, _ in _functions() if (m, n) == (module, name))
    in_class = next(c for m, n, _, c in _functions() if (m, n) == (module, name))
    depth = next((d for d in DEPTH_NAMES if d in _params(fn)), None)
    assert depth, f"{name} takes no depth from its caller"
    takers = _depth_takers()
    for call in [c for c in ast.walk(fn) if isinstance(c, ast.Call)]:
        callee = _callee(call, in_class)
        if callee not in takers:
            continue
        position, keyword = takers[callee]
        given = [k.value for k in call.keywords if k.arg == keyword] or call.args[position:position + 1]
        assert given, f"{name} calls {callee} without a depth: the count would start again ({ast.unparse(call)[:90]})"
        assert _reads(given[0], {depth}), f"{name} hands {callee} a depth not counted from its own: " \
                                           f"{ast.unparse(given[0])}"
    bounded = [c for c in ast.walk(fn) if isinstance(c, ast.Compare) and _reads(c, {depth}) and _reads(c, {"DEPTH"})]
    assert bounded, f"{name} does not count its depth against codes.DEPTH"


# --- Each place a value enters: DEPTH taken, DEPTH + 1 refused --------------------------------------------------------

def nested(depth, leaf="leaf"):
    """``leaf`` standing at ``depth``: inside ``depth`` lists."""
    for _ in range(depth):
        leaf = [leaf]
    return leaf


def beside(depth, at):
    """A leaf at ``depth`` inside lists, with a record beside the path standing at depth ``at`` (an item of the list at
    ``at - 1``)."""
    value = "leaf"
    for level in range(depth - 1, -1, -1):
        value = [Hypothesis("A", "a", .5), value] if level == at - 1 else [value]
    return value


def in_data_field(depth):
    """A record inside the path: lists to depth 10, a Probe there, its ``arguments`` (a data field, at 11) holding the
    rest of the path, the leaf at ``depth``."""
    return nested(10, Probe("p", "q", "t", {"A": True}, .8, arguments={"k": nested(depth - 12)}))


def in_record_fields(depth):
    """Records inside the path: lists to depth 10, then a chain of TreeNode records (each a record field of the one
    above), the last one's ``value`` the leaf at ``depth``."""
    node = TreeNode(value=1)
    for _ in range(depth - 11):
        node = TreeNode(feature=0, threshold=0.0, left=node, right=TreeNode(value=0))
    return nested(10, node)


def in_records_field(depth):
    """Records inside the path through a field that holds a list of records: a DomainStandard at ``depth - 4``, its
    ``criteria`` one level in, each Criterion two levels in, its ``required_methods`` three, the leaf four."""
    standard = DomainStandard("d", "1", "AU", "a", "a" * 64,
                              (Criterion("c", "d", "checker", "1:1", required_methods=("formal",)),))
    return nested(depth - 4, standard)


PLACEMENTS = {
    "no-record": nested,
    "record-beside-first": lambda d: beside(d, 1),
    "record-beside-middle": lambda d: beside(d, DEPTH // 2),
    # the deepest a record stands with its own fields taken: its fields are one level inside it
    "record-beside-last": lambda d: beside(d, DEPTH - 1),
    "record-inside-data-field": in_data_field,
    "records-inside-record-fields": in_record_fields,
    "records-inside-a-records-field": in_records_field,
}


def _as_given(value):
    return value


def _refused(call):
    with pytest.raises(ValueError) as excinfo:
        call()
    assert getattr(excinfo.value, "code", None) == codes.INPUT, repr(excinfo.value)
    assert f"deeper than {DEPTH}" in str(excinfo.value), str(excinfo.value)[:300]


#: Each place a value enters, as a call that raises when it is refused.
ENTRIES = {
    "snapshot": lambda v: snapshot(v, where="a value"),
    "fresh": lambda v: fresh(v, where="a copy"),
    "plain-over-view": lambda v: plain(view(v), where="a value"),
    "digest": digest,
    "a-collaborators-answer": lambda v: returned(v, _as_given),
}


def _depths_of(value):
    """The deepest leaf of ``value`` as the claim rule reads it (``view``), counted here, not by the code."""
    seen = view(value)
    best, stack = 0, [(seen, 0)]
    while stack:
        item, depth = stack.pop()
        best = max(best, depth)
        if isinstance(item, dict):
            stack += [(v, depth + 1) for v in item.values()]
        elif isinstance(item, list):
            stack += [(v, depth + 1) for v in item]
    return best


@pytest.mark.parametrize("placement", sorted(PLACEMENTS))
def test_each_placement_stands_where_it_says(placement):
    for depth in (DEPTH, DEPTH + 1):
        assert _depths_of(PLACEMENTS[placement](depth)) == depth, placement


@pytest.mark.parametrize("placement", sorted(PLACEMENTS))
@pytest.mark.parametrize("entry", sorted(ENTRIES))
def test_a_leaf_at_depth_DEPTH_is_taken_and_one_deeper_refused(entry, placement):
    make = PLACEMENTS[placement]
    ENTRIES[entry](make(DEPTH))
    _refused(lambda: ENTRIES[entry](make(DEPTH + 1)))


@pytest.mark.parametrize("placement", sorted(PLACEMENTS))
def test_the_claim_rule_over_view_counts_the_same(placement):
    make = PLACEMENTS[placement]
    assert codes.claims(view(make(DEPTH))) == []
    found = codes.claims(view(make(DEPTH + 1)))
    assert found and all(f"deeper than {DEPTH}" in place for place in found), found


def test_a_record_counts_its_fields_from_where_it_is_made():
    # a record made on its own stands at depth 0, its fields at 1: the data in a field is counted from there
    Probe("p", "q", "t", {"A": True}, .8, arguments={"k": nested(DEPTH - 2)})
    _refused(lambda: Probe("p", "q", "t", {"A": True}, .8, arguments={"k": nested(DEPTH - 1)}))
    # and a record standing at DEPTH has its fields one level deeper: refused where it is taken
    _refused(lambda: snapshot(nested(DEPTH, Hypothesis("A", "a", .5))))
    snapshot(nested(DEPTH - 1, Hypothesis("A", "a", .5)))


@pytest.mark.parametrize("depth,taken", [(DEPTH, True), (DEPTH + 1, False)])
def test_a_models_reply(depth, taken):
    text = json.dumps(nested(depth))
    for call in (lambda: json_reply(text, 12000), lambda: returned(text, json_reply, 12000)):
        if taken:
            assert call() == nested(depth)
        else:
            _refused(call)


def test_a_models_reply_nested_past_what_the_parser_reads_is_refused_with_the_code():
    # within the 12000-character cap a reply can nest 5999 levels: refused as too deep, never a RecursionError
    text = "[" * 5999 + "]" * 5999
    _refused(lambda: json_reply(text, 12000))
    _refused(lambda: returned(text, json_reply, 12000))


@pytest.mark.parametrize("depth,credited", [(DEPTH, True), (DEPTH + 1, False)])
def test_a_tools_answer_the_agent_takes(depth, credited, monkeypatch):
    # planted after G6's door (which itself cuts a reply at 32 levels): the bridge counts the door's copy from its
    # root, where it enters, and uses only its observation
    real = door.invoke_checked

    def planted(*args, **kwargs):
        reply = real(*args, **kwargs)
        return {**reply, "result": {**reply["result"], "planted": nested(depth - 1)}}

    monkeypatch.setattr(door, "invoke_checked", planted)
    out = CognitiveAgent(StaticProposer([Probe("p", "measure", "read", {"A": True, "B": False}, .8)]), {
        "read": ToolSpec("read", lambda q, a: Observation(True, "source:actual"))}).run(
        "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    if credited:
        assert out["history"] and out["completion_state"] == "qualified-draft", out.get("reason")
    else:
        assert out["history"] == [] and out["code"] == codes.STEP_FAILED and f"deeper than {DEPTH}" in out["reason"]


@pytest.mark.parametrize("depth,credited", [(DEPTH - 2, True), (DEPTH, True), (DEPTH + 1, False), (DEPTH + 3, False)])
def test_a_tools_answer_the_executor_publishes(depth, credited, monkeypatch):
    # the executor publishes a step's answer at run["steps"][i]["result"]: three levels inside the run it returns, so
    # its leaf is counted in that run, and the run itself is never refused for what a step took
    real = mixed_methods.invoke_checked

    def planted(*args, **kwargs):
        reply = real(*args, **kwargs)
        return {**reply, "result": {**reply["result"], "answer": nested(depth - 4)}}

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": {}})

    monkeypatch.setattr(mixed_methods, "invoke_checked", planted)
    plan = MethodRouter().plan([ClaimIR("c", "check A", ("formal",), {"formal": {"formula": "A"}})])
    out = MixedMethodExecutor(Registry()).execute(plan, approved=True)
    step = out["steps"][0]
    if credited:
        assert out["completion_state"] == "qualified-draft" and _depths_of(out) == depth, step.get("reason")
    else:
        assert out["code"] == codes.EXECUTION_INCOMPLETE and step["code"] == codes.STEP_FAILED
        assert "result" not in step and f"deeper than {DEPTH}" in step["reason"], step


class _Arrays:
    """The arrays a fitted scikit-learn tree exposes, for a chain: node i (i < edges) splits, its left child i + 1
    and its right child a leaf; node ``edges`` is a leaf. ``cycle`` points the root back at itself."""

    def __init__(self, edges, cycle=False):
        leaves = list(range(edges + 1, 2 * edges + 1))
        size = 2 * edges + 1
        self.children_left = [i + 1 for i in range(edges)] + [-1] * (size - edges)
        self.children_right = leaves + [-1] * (size - edges)
        if cycle:
            self.children_left[0] = 0
        self.feature = [0] * size
        self.threshold = [0.0] * size
        self.value = [[[1.0]]] * size


class _Estimator:
    n_features_in_ = 1

    def __init__(self, edges, cycle=False):
        self.tree_ = _Arrays(edges, cycle)


@pytest.mark.parametrize("depth,taken", [(DEPTH, True), (DEPTH + 1, False)])
def test_a_fitted_tree(depth, taken):
    # ExplicitTree stands at 0, its root node at 1, a node at 1 + its depth in the tree, a leaf node's value one more:
    # a tree whose deepest leaf value stands at ``depth`` has depth - 2 edges on its longest path
    estimator = _Estimator(depth - 2)
    if taken:
        tree = extract_tree(estimator, ["x"])
        assert _depths_of(tree) == depth and codes.claims(view(tree)) == []
        snapshot(tree)
    else:
        _refused(lambda: extract_tree(estimator, ["x"]))


def test_a_fitted_tree_that_loops_is_refused_not_a_recursion_error():
    _refused(lambda: extract_tree(_Estimator(3, cycle=True), ["x"]))
