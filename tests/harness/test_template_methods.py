# SPDX-License-Identifier: Apache-2.0
"""Method binding admission and compiler custody; repository calls run only in confined tests."""
from __future__ import annotations

import ast
import json

import pytest

from g6.harness import behaviour_runner, verification_formal, verification_templates as vt
from g6.workforce import repo_tests
from tests.harness.test_verification_templates import (
    _activity,
    _bindings_segment,
    _patch,
    _prepared,
    _repo,
    instance,
    review,
)

pytestmark = pytest.mark.usefixtures("declared_mcp_grants")

BAR = '''class ProgressBar:
    def __init__(self, length, *, start=0, label=""):
        self.length = length
        self.pos = start
        self.label = label

    def update(self, n):
        self.pos += n
        return self.pos

    def echo(self, value):
        return value

    def read_pos(self):
        return self.pos

    def read_label(self):
        return self.label

    def permitted(self, user):
        return user == "owner"

    @property
    def position(self):
        return self.pos

    @staticmethod
    def static(n):
        return n

    @classmethod
    def class_method(cls, n):
        return n

    class Inner:
        def update(self, n):
            return n

    alias = update
    descriptor = property(lambda self: self.pos)

def make_bar(length, *, start=0, label=""):
    return ProgressBar(length, start=start, label=label)

Alias = ProgressBar
factory_alias = make_bar
'''
FILES = {"click/__init__.py": "", "click/_termui_impl.py": BAR}
METHOD = "click._termui_impl.ProgressBar.update"
CONSTRUCTOR = {"args": [20], "kwargs": {"start": 4}}


def method_instance(template="example.v1", *, function=METHOD, constructor=None, intent="preserve"):
    bindings = {"constructor": CONSTRUCTOR if constructor is None else constructor}
    if template == "example.v1":
        bindings.update(function=function, cases=[{"args": [3], "returns": 7}])
    elif template == "differential.v1":
        bindings.update(function=function, mode="outputs", calls=[{"args": [3]}, {"args": [2]}])
    elif template == "relation.v1":
        bindings.update(function=function, relation="idempotence", inputs=[1, 2])
    else:
        bindings.update(operation=function, refusal="falsy_return", cases=[
            {"who": "owner", "resource": "bar", "args": ["owner"], "expected": "allowed"},
            {"who": "guest", "resource": "bar", "args": ["guest"], "expected": "refused"}])
    return instance("method", template, bindings, intent)


@pytest.mark.parametrize("constructor", [CONSTRUCTOR,
    {"call": "click._termui_impl.ProgressBar", **CONSTRUCTOR},
    {"call": "click._termui_impl.make_bar", **CONSTRUCTOR}])
def test_json_constructor_applies_to_click_shaped_method(tmp_path, constructor):
    prepared = _prepared(tmp_path, FILES, {}, [method_instance(constructor=constructor)])
    assert len(prepared.checks) == 1, prepared.activities
    assert prepared.activities == []
    assert prepared.origins["method"].about["notes"] == []


@pytest.mark.parametrize("suffix, reason", [
    ("position", "property"), ("descriptor", "descriptor"), ("static", "staticmethod"),
    ("class_method", "classmethod"), ("Inner.update", "nested class"), ("alias", "assignment alias"),
])
def test_unsupported_class_members_stay_refused_with_a_reason(tmp_path, suffix, reason):
    prepared = _prepared(tmp_path, FILES, {}, [method_instance(function="click._termui_impl.ProgressBar." + suffix)])
    assert prepared.checks == []
    assert prepared.activities[0]["result"] == "refused"
    assert reason in prepared.activities[0]["reason"]


@pytest.mark.parametrize("name", ["click._termui_impl.ProgressBar.__init__",
    "click._termui_impl.ProgressBar.update;raise SystemExit", "click._termui_impl.ProgressBar.update()"])
def test_method_name_injection_is_refused_before_preparation(name):
    with pytest.raises(ValueError, match="double-underscore|dotted name"):
        vt.template_instances([method_instance(function=name)], {"R1"})


@pytest.mark.parametrize("value", [object(), b"file", (1, 2), {1, 2}])
def test_constructor_arguments_must_be_json(value):
    with pytest.raises(ValueError, match="JSON"):
        vt.template_instances([method_instance(constructor={"args": [value]})], {"R1"})


@pytest.mark.parametrize("constructor, reason", [
    ({"args": []}, "constructor"),
    ({"args": [20], "kwargs": {"stream": "not a file"}}, "constructor"),
    ({"call": "os.system", "args": ["anything"]}, "same module"),
    ({"call": "click._termui_impl.factory_alias", "args": [20]}, "top-level factory"),
    ({"call": "click._termui_impl.Alias", "args": [20]}, "top-level factory"),
])
def test_constructor_cannot_escape_its_class_or_module(tmp_path, constructor, reason):
    prepared = _prepared(tmp_path, FILES, {}, [method_instance(constructor=constructor)])
    assert prepared.checks == []
    assert reason in prepared.activities[0]["reason"]


def test_method_outside_import_roots_is_refused(tmp_path):
    prepared = _prepared(tmp_path, {"private/click/__init__.py": "", "private/click/_termui_impl.py": BAR}, {},
                         [method_instance()])
    assert prepared.checks == []
    assert "import roots" in prepared.activities[0]["reason"]


@pytest.mark.parametrize("extra, name, reason", [
    ("Alias = ProgressBar\n", "click._termui_impl.Alias.update", "assignment aliases"),
    ("from missing import *\n", "click._termui_impl.Unknown.update", "star import"),
    ("", "click._termui_impl.ProgressBar.missing", "class body"),
])
def test_class_lookup_does_not_follow_aliases_imports_or_inheritance(tmp_path, extra, name, reason):
    prepared = _prepared(tmp_path, {**FILES, "click/_termui_impl.py": BAR + extra}, {},
                         [method_instance(function=name)])
    assert prepared.checks == []
    assert reason in prepared.activities[0]["reason"]


@pytest.mark.parametrize("before, after, reason", [
    ("    def update(self, n):", "    async def update(self, n):", "asynchronous"),
    ("    def __init__(self, length, *, start=0, label=\"\"):",
     "    @some_decorator\n    def __init__(self, length, *, start=0, label=\"\"):", "constructor"),
])
def test_unreadable_or_async_method_calls_stay_refused(tmp_path, before, after, reason):
    prepared = _prepared(tmp_path, {**FILES, "click/_termui_impl.py": BAR.replace(before, after)}, {},
                         [method_instance()])
    assert prepared.checks == []
    assert reason in prepared.activities[0]["reason"]


@pytest.mark.parametrize("intent", ["change", "preserve"])
def test_patch_only_method_is_refused_for_differential_even_with_change_intent(tmp_path, intent):
    before = BAR.replace("    def update(self, n):\n        self.pos += n\n        return self.pos\n", "")
    prepared = _prepared(tmp_path, {**FILES, "click/_termui_impl.py": before},
                         {"click/_termui_impl.py": BAR}, [method_instance("differential.v1", intent=intent)])
    assert prepared.checks == []
    assert "base commit" in prepared.activities[0]["reason"]
    assert "update" in prepared.activities[0]["reason"]


def test_patch_side_property_is_refused_for_a_base_method(tmp_path):
    after = BAR.replace("    def update(self, n):", "    @property\n    def update(self, n):", 1)
    prepared = _prepared(tmp_path, FILES, {"click/_termui_impl.py": after},
                         [method_instance("differential.v1")])
    assert prepared.checks == []
    assert "property" in prepared.activities[0]["reason"]
    assert "working tree" in prepared.activities[0]["reason"]


def test_patch_side_harmless_decorator_is_read_as_a_decorated_method(tmp_path):
    decorator = "def unchanged(function):\n    return function\n\n"
    after = decorator + BAR.replace("    def update(self, n):", "    @unchanged\n    def update(self, n):", 1)
    prepared = _prepared(tmp_path, FILES, {"click/_termui_impl.py": after},
                         [method_instance("differential.v1")])
    assert prepared.checks == []
    reason = prepared.activities[0]["reason"]
    assert "decorated method" in reason
    assert "working tree" in reason
    assert "could not be read or parsed" not in reason


def test_patch_side_invalid_python_stays_unreadable(tmp_path):
    after = BAR.replace("    def update(self, n):", "    def update(self, n) syntax_error:", 1)
    prepared = _prepared(tmp_path, FILES, {"click/_termui_impl.py": after},
                         [method_instance("differential.v1")])
    assert prepared.checks == []
    reason = prepared.activities[0]["reason"]
    assert "could not be read or parsed" in reason
    assert "SyntaxError" in reason
    assert "working tree" in reason


@pytest.mark.parametrize("template", ["example.v1", "relation.v1", "access_rule.v1", "differential.v1"])
def test_constructor_text_only_enters_generated_source_as_a_literal(tmp_path, template):
    hostile = "'); raise SystemExit('injected') #\n__import__('os')\u202e"
    def build(label):
        return method_instance(template, constructor={"args": [20], "kwargs": {"label": label}})
    plain = _prepared(tmp_path, FILES, {}, [build("plain")], "plain")
    attack = _prepared(tmp_path, FILES, {}, [build(hostile)], "attack")
    [one], [two] = plain.checks, attack.checks
    literal = _bindings_segment(two.source)
    assert hostile in [node.value for node in ast.walk(ast.parse(literal)) if isinstance(node, ast.Constant)]
    assert one.source.replace(_bindings_segment(one.source), "LITERAL") == two.source.replace(literal, "LITERAL")
    compile(two.source, "<method-template>", "exec")


@pytest.mark.parametrize("template, name", [("example.v1", "update"), ("relation.v1", "echo"),
    ("access_rule.v1", "permitted"), ("differential.v1", "update")])
def test_callable_template_families_use_the_shared_resolver(tmp_path, monkeypatch, template, name):
    seen, real = [], vt.resolve
    def recording(tree, dotted, side):
        seen.append((dotted, side))
        return real(tree, dotted, side)
    monkeypatch.setattr(vt, "resolve", recording)
    item = method_instance(template, function="click._termui_impl.ProgressBar." + name)
    prepared = _prepared(tmp_path, FILES, {}, [item])
    assert len(prepared.checks) == 1, prepared.activities
    assert (item["bindings"].get("function", item["bindings"].get("operation")), vt.BASE) in seen
    assert ("click._termui_impl.ProgressBar", vt.BASE) in seen


def test_symbolic_search_does_not_treat_self_as_a_json_input(tmp_path):
    root = _repo(tmp_path, FILES)
    tree = vt.Tree.read(root, repo_tests.change(root))
    with pytest.raises(verification_formal._Outside, match="instance method"):
        verification_formal._function(tree, METHOD, vt.BASE)


@pytest.mark.parametrize("template, name", [("example.v1", "echo"), ("relation.v1", "echo"),
    ("access_rule.v1", "permitted"), ("differential.v1", "update")])
def test_confined_method_calls_run_at_base_and_patch(tmp_path, bwrap_backend, template, name):
    item = method_instance(template, function="click._termui_impl.ProgressBar." + name)
    if template == "example.v1":
        item["bindings"]["cases"] = [{"args": ["'); raise SystemExit(9) #"],
                                        "returns": "'); raise SystemExit(9) #"}]
    # Changed update returns 8 and then 11; the base returns 7 and then 9 on one bound instance.
    after = BAR.replace("self.pos += n", "self.pos += n + 1")
    root = _patch(_repo(tmp_path, FILES), {"click/_termui_impl.py": after})
    got = review(root, [{"id": "R1", "text": "keep method behavior", "checks": []}], template_instances=[item])
    activity = _activity(got, "method")
    assert activity["result"] == ("counterexample" if template == "differential.v1" else "not_refuted"), activity
    if template == "differential.v1":
        assert [json.loads(case["base"]) for case in activity["differences"]] == [{"returned": 7}, {"returned": 9}]
        assert [json.loads(case["patch"]) for case in activity["differences"]] == [{"returned": 8}, {"returned": 11}]


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("roots", [("lib",), ("first", "lib")])
def test_method_observation_keeps_the_resolved_file_and_roots(tmp_path, template, roots):
    root = _repo(tmp_path, {**{"lib/" + rel: text for rel, text in FILES.items()}, "first/helper.py": "VALUE = 1\n"})
    prepared = vt.prepare(root, repo_tests.change(root), vt.template_instances([method_instance(template)], {"R1"}),
                          roots=roots)
    [check] = prepared.checks
    _, data = behaviour_runner.bindings(check.source)
    located = behaviour_runner.located(root, data)
    assert located["function"][3] == "lib/click/_termui_impl.py"
    assert located["function"][4]["call"][3] == "lib/click/_termui_impl.py"
    assert behaviour_runner._import_roots(located["function"], located.get("import_roots")) == [*roots, ".", "src"]


def state_instance():
    return instance("state-method", "state_invariant.v1", {
        "constructor": {"call": "click._termui_impl.ProgressBar", "args": [20]},
        "model": {"variables": {"pos": "Int", "act": "Int"}, "init": "(and (= pos 0) (= act 0))",
                  "transition": "(and (= next_act 1) (= next_pos (+ pos 3)))",
                  "invariants": {"bounded": "(<= pos 20)"}},
        "action": "act", "actions": {"1": {"call": METHOD, "args": [3]}},
        "observe": {"pos": {"call": "click._termui_impl.ProgressBar.read_pos"}}})


def test_state_methods_resolve_against_the_stated_state_constructor(tmp_path, monkeypatch):
    seen, real = [], vt.resolve
    def recording(tree, dotted, side):
        seen.append(dotted)
        return real(tree, dotted, side)
    monkeypatch.setattr(vt, "resolve", recording)
    prepared = _prepared(tmp_path, FILES, {}, [state_instance()])
    assert prepared.activities == []
    assert len(prepared.formal) == 1
    assert {METHOD, "click._termui_impl.ProgressBar.read_pos", "click._termui_impl.ProgressBar"} <= set(seen)


def test_confined_state_methods_share_the_constructed_state(tmp_path, bwrap_backend):
    # Replay 7 updates: the model and real base exceed 20. Patch's update stops at 20, so its replay stays in bounds.
    root = _patch(_repo(tmp_path, FILES), {"click/_termui_impl.py": BAR.replace(
        "self.pos += n", "self.pos = min(self.length, self.pos + n)")})
    item = state_instance()
    item["bindings"].update(depth=8, traces=1)
    got = review(root, [{"id": "R1", "text": "position stays within length", "checks": []}], template_instances=[item])
    activity = _activity(got, "state-method")
    assert activity["result"] == "not_refuted", activity
    assert activity["formal"]["correspondence"]["reason"] == "model_only", activity


@pytest.mark.parametrize("factory", [None, "click._termui_impl.make_bar"])
def test_confined_constructor_code_text_is_only_a_literal(tmp_path, bwrap_backend, factory):
    hostile = "'); raise SystemExit(91) #\n__import__('os')"
    constructor = {"args": [20], "kwargs": {"label": hostile}}
    if factory:
        constructor["call"] = factory
    item = method_instance(function="click._termui_impl.ProgressBar.read_label", constructor=constructor)
    item["bindings"]["cases"] = [{"returns": hostile}]
    root = _patch(_repo(tmp_path, FILES), {"notes.txt": "exercise the unchanged method\n"})
    got = review(root, [{"id": "R1", "text": "label stays literal", "checks": []}], template_instances=[item])
    assert _activity(got, "method")["result"] == "not_refuted", got


def test_confined_factory_must_return_the_named_class(tmp_path, bwrap_backend):
    files = {**FILES, "click/_termui_impl.py": BAR + "\ndef wrong_factory(length):\n    return {}\n"}
    item = method_instance(constructor={"call": "click._termui_impl.wrong_factory", "args": [20]})
    root = _patch(_repo(tmp_path, files), {"notes.txt": "exercise constructor return validation\n"})
    got = review(root, [{"id": "R1", "text": "bind the requested class", "checks": []}], template_instances=[item])
    assert _activity(got, "method")["result"] == "check_error", got


def test_multi_step_access_method_names_use_the_same_resolver(tmp_path, monkeypatch):
    seen, real = [], vt.resolve
    def recording(tree, dotted, side):
        seen.append(dotted)
        return real(tree, dotted, side)
    monkeypatch.setattr(vt, "resolve", recording)
    item = instance("steps", "access_rule.v1", {
        "constructor": {"call": "click._termui_impl.ProgressBar", "args": [20]},
        "operation": "click._termui_impl.ProgressBar.permitted", "args": ["owner"],
        "who": "owner", "resource": "bar", "refusal": "falsy_return", "initially": "allowed",
        "steps": {"update": {"call": METHOD, "args": [3], "effect": "none"}}})
    prepared = _prepared(tmp_path, FILES, {}, [item])
    assert prepared.activities == []
    assert len(prepared.formal) == 1
    assert {METHOD, "click._termui_impl.ProgressBar.permitted", "click._termui_impl.ProgressBar"} <= set(seen)
