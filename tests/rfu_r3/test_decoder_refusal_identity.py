# SPDX-License-Identifier: Apache-2.0
"""A refusal crosses decoder copy boundaries without becoming an unrelated error."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.rfu_r3.test_decoder_copies import COPIES, SOURCE, _sync

ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATIONS = sorted(COPIES | {SOURCE})
BOUNDARIES = {name.removeprefix("src/").removesuffix(".py").replace("/", ".")
              for name in IMPLEMENTATIONS}
# Every refusal handler, including local readers and the source used by embedded children.
HANDLERS = {
    **{(name, "json_value"): "json" for name in IMPLEMENTATIONS},
    ("src/g6/mcp/__init__.py", "invoke_reply"): "input",
    ("src/g6/mcp/__init__.py", "build_server.g6_compose"): "input",
    ("src/g6/mcp/__init__.py", "_metaharness_tools.metaharness_start"): "input",
    ("src/g6/mcp/__init__.py", "_metaharness_tools.metaharness_propose"): "schema",
    ("src/g6/agents/agent_claude/adapter.py", "_result"): "unknown",
    ("src/g6/algebra/runner.py", "call_worker"): "worker",
    ("src/g6/_agentic/decode.py", "_decode"): "agentic",
    ("src/g6/_agentic/decode.py", "loads"): "agentic",
    ("src/g6/self_training/persistence.py", "ScorecardStore._logged"): "unknown",
}


def _module(name):
    spec = importlib.util.spec_from_file_location("decoder_copy_" + str(IMPLEMENTATIONS.index(name)), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refusal(module):
    try:
        module.decode(b"\xff")
    except ValueError as exc:
        return exc
    raise AssertionError("the guarded decoder accepted invalid UTF-8")


@pytest.mark.parametrize("sender", IMPLEMENTATIONS)
@pytest.mark.parametrize("receiver", IMPLEMENTATIONS)
def test_each_copy_recognizes_every_other_copys_refusal(sender, receiver):
    sent, received = _module(sender), _module(receiver)
    error = _refusal(sent)
    assert type(error).__g6_decoder_refusal__ == "g6.decoder.refusal.v1"
    # Exception matching APIs use subclass checks; Python except clauses need the predicate below.
    with pytest.raises(received.UndecodableRecord) as caught:
        raise error
    assert caught.value is error
    assert received.is_undecodable_record(error)
    assert isinstance(error, received.UndecodableRecord)
    assert issubclass(type(error), received.UndecodableRecord)


@pytest.mark.parametrize("name", IMPLEMENTATIONS)
def test_refusal_recognition_rejects_unrelated_errors_and_instance_markers(name):
    module = _module(name)
    unrelated = [ValueError("ordinary"), json.JSONDecodeError("syntax", "", 0), TypeError("ordinary")]
    forged_instance = ValueError("instance attributes do not declare a decoder refusal")
    forged_instance.__g6_decoder_refusal__ = "g6.decoder.refusal.v1"
    unrelated.append(forged_instance)
    for error in unrelated:
        assert not module.is_undecodable_record(error)
        assert not isinstance(error, module.UndecodableRecord)
        assert not issubclass(type(error), module.UndecodableRecord)


@pytest.mark.parametrize("sender", IMPLEMENTATIONS)
@pytest.mark.parametrize("receiver", IMPLEMENTATIONS)
def test_list_shapes_work_across_copy_boundaries(sender, receiver):
    schema = {"items": _module(sender).ListOf(int)}
    shaped = _module(receiver).shaped
    assert shaped({"items": [1, 2]}, schema)
    assert not shaped({"items": [1, True]}, schema)
    assert not shaped({"items": "12"}, schema)
    assert shaped({"items": ["a"]}, {"items": (str, list)})


def _tries(tree):
    """Visit handlers with their enclosing qualified function names, without line-number pins."""
    found = []

    def visit(node, scope=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        if isinstance(node, ast.Try):
            found.append((".".join(scope), node))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree)
    return found


def _uses_rule(handler):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "is_undecodable_record" for n in ast.walk(handler))


def _handler(path, function):
    """Execute the real site's entire except chain, injecting only the decoding failure.

    This isolates nested MCP handlers and the worker reader without starting a server,
    launching a worker or writing stores. Guard order and refusal bodies remain intact.
    """
    from g6._agentic.decode import Undecodable
    from g6.algebra.envelope import OUTPUT_UNRECOGNISED, Refusal
    from g6.core.decoder_limits import UndecodableRecord, is_undecodable_record
    from g6.core.door import blocked_reply

    tree = ast.parse((ROOT / path).read_bytes())
    chains = [node for scope, node in _tries(tree) if scope == function and any(
        _uses_rule(h) or h.type is not None and "UndecodableRecord" in ast.unparse(h.type) for h in node.handlers)]
    assert len(chains) == 1, (path, function)
    chain = copy.deepcopy(chains[0])
    chain.body = ast.parse("raise failure").body
    chain.orelse, chain.finalbody = [], []
    # The worker's earlier malformed-input branch can break its record loop.
    wrapper = ast.parse("def handle(failure):\n for _ in (0,):\n  pass\n return locals().get('entry')\n")
    wrapper.body[0].body[0].body = [chain]
    record = {}
    namespace = dict(json=json, UndecodableRecord=UndecodableRecord, is_undecodable_record=is_undecodable_record,
                     Undecodable=Undecodable, Refusal=Refusal, OUTPUT_UNRECOGNISED=OUTPUT_UNRECOGNISED,
                     blocked_reply=blocked_reply, record=record, run=SimpleNamespace(output="unreadable"), _TAIL=8000)
    # json_value in a pure copy must use that copy's own predicate.
    if path in IMPLEMENTATIONS:
        namespace.update(vars(_module(path)))
    exec(compile(ast.fix_missing_locations(wrapper), str(ROOT / path), "exec"), namespace)
    return namespace["handle"], record


@pytest.mark.parametrize("sender", IMPLEMENTATIONS)
@pytest.mark.parametrize("site", sorted(HANDLERS))
def test_each_handler_handles_refusals_from_every_copy(sender, site):
    from g6._agentic.decode import Undecodable
    from g6.algebra.envelope import OUTPUT_UNRECOGNISED, Refusal

    handle, record = _handler(*site)
    error = _refusal(_module(sender))
    policy = HANDLERS[site]
    if policy == "json":
        with pytest.raises(json.JSONDecodeError):
            handle(error)
    elif policy == "worker":
        with pytest.raises(Refusal) as caught:
            handle(error)
        assert caught.value.code == OUTPUT_UNRECOGNISED
        assert "past the decoder's limits" in str(caught.value)
        assert record["output_tail"] == "unreadable"
    elif policy == "agentic":
        with pytest.raises(Undecodable) as caught:
            handle(error)
        assert str(caught.value) == str(error)
    elif policy == "unknown":
        assert handle(error) is None
    else:
        reply = handle(error)
        assert reply["completion_state"] == "blocked-escalated"
        assert ("G6_E_INPUT_VALIDATION" if policy == "input" else "G6_E_META_SCHEMA") in json.dumps(reply)


@pytest.mark.parametrize("site", sorted(HANDLERS))
def test_handlers_do_not_silently_accept_unmarked_value_errors(site):
    from g6._agentic.decode import Undecodable

    handle, _ = _handler(*site)
    error = ValueError("unrelated failure after decoding")
    if HANDLERS[site] == "agentic":  # This boundary already normalizes all native decoding failures.
        with pytest.raises(Undecodable, match="not decoded: ValueError"):
            handle(error)
    else:
        with pytest.raises(ValueError) as caught:
            handle(error)
        assert caught.value is error


def _resolve(node, imports):
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return _resolve(node.value, imports) + "." + node.attr
    return ""


def _inventory(tree):
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.update({a.asname or a.name: (node.module or "") + "." + a.name for a in node.names})
        elif isinstance(node, ast.Import):
            imports.update({a.asname or a.name.split(".")[0]: a.name if a.asname else a.name.split(".")[0]
                            for a in node.names})
    type_aliases = {alias for alias, name in imports.items() if name.endswith(".UndecodableRecord")}
    bare_handlers, assertions = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            if any(isinstance(n, ast.Name) and n.id in type_aliases | {"UndecodableRecord"}
                   or isinstance(n, ast.Attribute) and n.attr == "UndecodableRecord" for n in ast.walk(node.type)):
                bare_handlers.append(node.lineno)
        if isinstance(node, ast.Call) and _resolve(node.func, imports) == "pytest.raises" and node.args:
            for part in ast.walk(node.args[0]):
                name = _resolve(part, imports)
                if name.endswith(".UndecodableRecord") or name == "UndecodableRecord":
                    assertions.append((node.lineno, name))
    rules = [(scope, handler) for scope, node in _tries(tree) for handler in node.handlers if _uses_rule(handler)]
    return imports, bare_handlers, assertions, rules


def test_inventory_requires_the_shared_rule_at_every_handler_and_type_assertion():
    assert set(_sync().COPIES) == COPIES
    seen = set()
    for folder in ("src", "tests"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            imports, bare, assertions, rules = _inventory(ast.parse(path.read_bytes()))
            assert not bare, (relative, bare, "except uses nominal identity instead of the refusal rule")
            for line, name in assertions:
                # The matrix above deliberately gets each registered receiver dynamically.
                dynamic_matrix = relative == "tests/rfu_r3/test_decoder_refusal_identity.py" and name == (
                    "received.UndecodableRecord")
                assert dynamic_matrix or name.removesuffix(".UndecodableRecord") in BOUNDARIES, (relative, line, name)
            for scope, handler in rules:
                seen.add((relative, scope))
                if relative not in IMPLEMENTATIONS:
                    boundary = ("g6.self_training._decoder_limits" if "self_training/" in relative
                                else "g6.core.decoder_limits")
                    assert imports.get("is_undecodable_record") == boundary + ".is_undecodable_record"
                if (relative, scope) not in {
                    ("src/g6/_agentic/decode.py", "_decode"), ("src/g6/_agentic/decode.py", "loads")
                }:
                    # Refusal-only handlers must start by re-raising errors outside their explicit policy.
                    first = handler.body[0]
                    assert isinstance(first, ast.If) and len(first.body) == 1
                    assert isinstance(first.body[0], ast.Raise) and first.body[0].exc is None
    assert seen == set(HANDLERS), sorted(seen ^ set(HANDLERS))


def test_inventory_detects_nominal_handlers_aliases_and_unregistered_assertions():
    tree = ast.parse("from g6.core.decoder_limits import UndecodableRecord as Refusal\n"
                     "from pytest import raises as expect\n"
                     "try:\n pass\nexcept Refusal:\n pass\n"
                     "with expect(Refusal):\n pass\n"
                     "with expect(other.UndecodableRecord):\n pass\n")
    _, bare, assertions, _ = _inventory(tree)
    assert bare == [5]
    assert {name for _, name in assertions} == {
        "g6.core.decoder_limits.UndecodableRecord", "other.UndecodableRecord"}
