"""Every executable template is screened by definition identity on both sides."""
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from g6.harness import behaviour_runner as br, candidate_standard as cs
from g6.harness import verification_search as vs, verification_templates as vt

GONE = [{"function": "calc.add", "case": {"args": [2, 3], "kwargs": {}, "returns": -1}}]
ADD = "def add(a, b):\n    return a + b\n"
ALIASES = {
    "direct": ("calc.add", {}),
    "assignment": ("calc.alias", {"calc.py": ADD + "alias = add\n"}),
    "chain": ("calc.second", {"calc.py": ADD + "alias = add\nsecond = alias\n"}),
    "import": ("other.alias", {"other.py": "from calc import add as alias\n"}),
    "module": ("other.alias", {"other.py": "import calc as c\nalias = c.add\n"}),
    "relative": ("pkg.api.alias", {"pkg/__init__.py": "", "pkg/api.py": "from .bridge import alias\n",
                                   "pkg/bridge.py": "from calc import add as alias\n"}),
}


def compiled(monkeypatch, template, function, base, patch=None):
    root = Path.cwd()
    patch = base if patch is None else patch
    change = SimpleNamespace(repo=root, sources=["calc.py"], base_commit="b" * 40)
    tree = vt.Tree(root, change, frozenset(patch), frozenset(base), _texts={
        **{(rel, vt.BASE): text for rel, text in base.items()},
        **{(rel, vt.PATCH): text for rel, text in patch.items()},
    })
    bindings = {"function": function}
    bindings.update({"cases": [{"args": [2, 3], "returns": -1}]} if template == "example.v1"
                    else {"calls": [{"args": [2, 3]}], "mode": "outputs"})
    [instance] = vt.template_instances([dict(id="C1", requirement_id="R1", intent="preserve",
                                           template=template, bindings=bindings)], ["R1"])
    definition = vt.REGISTRY[template]
    applied = definition.applies(tree, definition.bindings.model_validate(bindings), vt.BASE)
    monkeypatch.setattr(vs, "placements", lambda root, checks: {c.id: vs.placement(c) for c in checks})
    check, _, origin = vt.compiled_check(root, instance, applied.data, applied.notes)
    return check, instance, origin


@pytest.mark.parametrize("template", sorted(br.RUN_TEMPLATES))
@pytest.mark.parametrize("alias", ALIASES)
def test_every_executable_template_rejects_aliases(monkeypatch, template, alias):
    function, texts = ALIASES[alias]
    check, instance, origin = compiled(monkeypatch, template, function, {"calc.py": ADD, **texts})
    kept, skipped, uncounted = cs.screen(GONE, [check], lambda ident: origin, [(check, instance)])
    assert not kept and len(skipped) == 1 and not uncounted


@pytest.mark.parametrize("template", sorted(br.RUN_TEMPLATES))
def test_uncompiled_names_cannot_decide_rejection(monkeypatch, template):
    _, instance, _ = compiled(monkeypatch, template, "calc.add", {"calc.py": ADD})
    kept, skipped = cs.gate({"rejected": GONE}, [instance], "candidate check", "fixture")
    assert kept == [instance] and not skipped


@pytest.mark.parametrize("template", sorted(br.RUN_TEMPLATES))
def test_same_named_independent_definitions_keep_authority(monkeypatch, template):
    check, instance, origin = compiled(monkeypatch, template, "other.add", {
        "calc.py": ADD, "other.py": "def add(a, b):\n    return a - b\n"})
    kept, skipped, uncounted = cs.screen(GONE, [check], lambda ident: origin, [(check, instance)])
    assert kept == [check] and not skipped and not uncounted


@pytest.mark.parametrize("template", sorted(br.RUN_TEMPLATES))
def test_assignment_alias_keeps_the_definition_bound_at_assignment(monkeypatch, template):
    check, instance, origin = compiled(monkeypatch, template, "calc.alias", {
        "calc.py": ADD + "alias = add\ndef add(a, b):\n    return -1\n"})
    kept, skipped, uncounted = cs.screen(GONE, [check], lambda ident: origin, [(check, instance)])
    assert kept == [check] and not skipped and not uncounted


@pytest.mark.parametrize("template", sorted(br.RUN_TEMPLATES))
@pytest.mark.parametrize("side", [vt.BASE, vt.PATCH])
def test_alias_on_either_execution_side_is_rejected(monkeypatch, template, side):
    separate = {"calc.py": ADD, "other.py": "def alias(a, b):\n    return -1\n"}
    alias = {"calc.py": ADD, "other.py": "from calc import add as alias\n"}
    check, instance, origin = compiled(monkeypatch, template, "other.alias",
                                      alias if side == vt.BASE else separate,
                                      alias if side == vt.PATCH else separate)
    kept, skipped, uncounted = cs.screen(GONE, [check], lambda ident: origin, [(check, instance)])
    assert not kept and skipped and not uncounted


@pytest.mark.parametrize("template", sorted(br.RUN_TEMPLATES))
@pytest.mark.parametrize("source", [
    "alias = make_callable()\n", "alias = missing\n", "alias = second\nsecond = alias\n",
    "if flag:\n    from calc import add as alias\nelse:\n    alias = other\n",
    "def alias(a, b):\n    return -1\nalias = wrap(alias)\n",
    "def alias(a, b):\n    return -1\nimport external\n",
    "def alias(a, b):\n    return -1\nimport mutation\n",
    "def alias(a, b):\n    return -1\nx: mutate() = 0\n",
    "def alias(a, b):\n    return -1\ndef __getattr__(name):\n    return mutate()\n",
])
def test_unresolved_definitions_have_no_authority(monkeypatch, template, source):
    check, instance, origin = compiled(monkeypatch, template, "other.alias", {
        "calc.py": ADD, "other.py": source, "mutation.py": "import calc, other\nother.alias = calc.add\n"})
    kept, skipped, uncounted = cs.screen(GONE, [check], lambda ident: origin, [(check, instance)])
    assert kept == [check] and not skipped and uncounted == {check.id}


@pytest.mark.parametrize("template", sorted(br.RUN_TEMPLATES))
def test_serialized_compiler_text_does_not_carry_definition_custody(monkeypatch, template):
    check, instance, origin = compiled(monkeypatch, template, "other.add", {"calc.py": ADD, "other.py": ADD})
    # Restoring runner data from history cannot restore a host-held definition resolver.
    source = br.ExecutableSource(str(check.source), br.bindings(check.source)[1])
    restored = check.model_copy(update={"source": source})
    kept, skipped, uncounted = cs.screen(GONE, [restored], lambda ident: origin, [(restored, instance)])
    assert kept == [restored] and not skipped and uncounted == {check.id}


@pytest.mark.parametrize("uncounted", [None, set(), {"another"}])
def test_search_cannot_replace_its_own_screen_with_a_supplied_empty_set(monkeypatch, uncounted):
    check = vs.ProposedCheck(id="C1", requirement_id="R1", intent="preserve", file="test_fixture.py",
                             source="def test_fixture():\n    assert False\n")

    def execute(change, placed, source, node, *, at_base, **kwargs):
        result = "passed" if at_base else "failed"
        return dict(verdict=result, seconds=.01, said="", rootdir="", output="", collected_ids=[node],
                    omitted_ids=[], failed_ids=[] if at_base else [node], setup_errors={},
                    failure_messages={} if at_base else {node: "assert False"},
                    test_outcomes={node: dict(setup="passed", call=result, teardown="passed")},
                    loaded_outside={}, loaded_complete=True)

    monkeypatch.setattr(vs, "_run_check", execute)
    done = vs.search(SimpleNamespace(repo=Path.cwd(), sources=[], base_commit="b" * 40), [check],
                     {check.id: vs.placement(check)}, deadline=time.monotonic() + 120, artifact="a" * 64,
                     rejected=GONE, uncounted=uncounted)
    assert not done.defects and not done.outcomes and not done.questions
    assert done.activities[0]["result"] == vs.ASSERTS_REJECTED
