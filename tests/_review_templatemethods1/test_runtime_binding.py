import sys
from pathlib import Path
from types import ModuleType
import pytest
from g6.harness import verification_templates as vt

class Tree:
    def folders(self):
        return ("", "src/")
    def has(self, rel, side):
        return rel == "review_fixture.py"
    def text(self, rel, side):
        return self.source
    roots = ()

@pytest.mark.parametrize("replacement", [
    'self.permitted = lambda who: True',
    'type(self).permitted = lambda self, who: True',
], ids=["instance-shadow", "class-rebind"])
def test_constructor_shadow_cannot_pass_access_rule(monkeypatch, replacement):
    tree = Tree()
    tree.source = '''class Gate:
    def __init__(self):
        REPLACEMENT
    def permitted(self, who):
        return who == "owner"
'''.replace("REPLACEMENT", replacement)
    raw = {"id": "gate", "template": "access_rule.v1",
           "requirement_id": "R1", "intent": "change",
           "bindings": {"operation": "review_fixture.Gate.permitted", "constructor": {},
                        "refusal": "falsy_return", "cases": [
                            {"who": "owner", "resource": "record", "args": ["owner"], "expected": "allowed"},
                            {"who": "guest", "resource": "record", "args": ["guest"], "expected": "refused"}]}}
    [item] = vt.template_instances([raw], {"R1"})
    applied = vt._access(tree, vt.AccessBindings.model_validate(item.bindings), vt.PATCH)
    fixture = ModuleType("review_fixture")
    fixture.__file__ = str(Path.cwd() / "review_fixture.py")
    exec(tree.source, fixture.__dict__)
    monkeypatch.setitem(sys.modules, "review_fixture", fixture)
    assert fixture.Gate().permitted("guest") is True
    exec(tree.source, fixture.__dict__)  # Restore a fresh class before G6 constructs it.
    namespace = {}
    exec(vt.compile_text(vt.TEMPLATES["access_rule.v1"], applied.data), namespace)
    with pytest.raises(pytest.fail.Exception, match="expected refused"):
        namespace["test_access_rule"]()

@pytest.mark.parametrize("template", ["access_rule.v1", "relation.v1"])
def test_constructor_type_refusal_is_check_error(monkeypatch, template):
    from g6.harness import verification_search as vs
    from tests.harness.test_verification_templates import result

    before = '''class Gate:
    def permitted(self, who):
        return who == "owner"
    def echo(self, value):
        return value

def make_gate():
    return Gate()
'''
    after = before.replace("return Gate()", "return {}")
    tree = Tree()
    tree.source = before
    if template == "access_rule.v1":
        bindings = {
            "operation": "review_fixture.Gate.permitted",
            "constructor": {"call": "review_fixture.make_gate"},
            "refusal": "falsy_return", "cases": [
                {"who": "owner", "resource": "record", "args": ["owner"], "expected": "allowed"},
                {"who": "guest", "resource": "record", "args": ["guest"], "expected": "refused"}]}
        test_name = "test_access_rule"
    else:
        bindings = {
            "function": "review_fixture.Gate.echo",
            "constructor": {"call": "review_fixture.make_gate"},
            "relation": "idempotence", "inputs": [1]}
        test_name = "test_relation"
    [item] = vt.template_instances([{
        "id": "gate", "template": template, "requirement_id": "R1",
        "intent": "preserve", "bindings": bindings}], {"R1"})
    definition = vt.TEMPLATES[template]
    bound = definition.bindings.model_validate(item.bindings)
    data = definition.applies(tree, bound, vt.BASE).data
    tree.source = after
    definition.applies(tree, bound, vt.PATCH)  # Patch-side admission also succeeds.
    namespace = {}
    exec(vt.compile_text(definition, data), namespace)
    fixture = ModuleType("review_fixture")
    fixture.__file__ = str(Path.cwd() / "review_fixture.py")
    monkeypatch.setitem(sys.modules, "review_fixture", fixture)
    exec(before, fixture.__dict__)
    namespace[test_name]()
    exec(after, fixture.__dict__)
    with pytest.raises(TypeError, match="constructor did not return the named class") as refused:
        namespace[test_name]()

    # Same structured pytest reports used by the repository's executor tests;
    # the call exception above is real, not a made-up constructor failure.
    node = "test_g6_fixture.py::" + test_name
    base = vs.reading(result(passed=[node]), node)
    patch = vs.reading(result(failed=[node], messages={
        node: f"{type(refused.value).__name__}: {refused.value}"}), node)
    verdict = vs.classify("preserve", patch, base)
    assert verdict["result"] == vs.CHECK_ERROR, verdict


def test_formal_replay_cannot_ignore_instance_action(monkeypatch):
    tree = Tree()
    tree.source = '''class Counter:
    def __init__(self):
        self.n = 0
        self.increment = lambda: setattr(self, "n", -1)
    def increment(self):
        self.n += 1
    def read(self):
        return self.n
'''
    bindings = {
        "constructor": {"call": "review_fixture.Counter"},
        "model": {"variables": {"n": "Int", "act": "Int"},
                  "init": "(and (= n 0) (= act 0))",
                  "transition": "(and (= next_act 1) (= next_n (- 1)))",
                  "invariants": {"nonnegative": "(>= n 0)"}},
        "action": "act",
        "actions": {"1": {"call": "review_fixture.Counter.increment"}},
        "observe": {"n": {"call": "review_fixture.Counter.read"}}}
    [item] = vt.template_instances([{
        "id": "state", "template": "state_invariant.v1", "requirement_id": "R1",
        "intent": "change", "bindings": bindings}], {"R1"})
    definition = vt.FORMAL_TEMPLATES["state_invariant.v1"]
    applied = definition.applies(
        tree, definition.bindings.model_validate(item.bindings), vt.PATCH)
    data = {**applied.data, "traces": [{"actions": [1], "ghost": {}}]}
    fixture = ModuleType("review_fixture")
    fixture.__file__ = str(Path.cwd() / "review_fixture.py")
    exec(tree.source, fixture.__dict__)
    monkeypatch.setitem(sys.modules, "review_fixture", fixture)
    actual = fixture.Counter()
    actual.increment()
    assert actual.read() == -1
    namespace = {}
    exec(vt.compile_text(definition, data), namespace)
    observed = namespace["_replay"](data["traces"][0])
    assert observed["outcome"] == "broken", observed
