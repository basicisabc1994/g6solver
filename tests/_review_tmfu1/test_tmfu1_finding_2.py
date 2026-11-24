"""Review 1 of claude/template-methods-fu (9203d9c6), finding 2.

Class: a guard that covers less than it guards (the parent's review 4 I1, for a member the repair does not reach).

The repair reads an import G6's ``-I -S`` runner cannot satisfy as ``G6_E_OBSERVATION_UNAVAILABLE`` when the module's
body imports it, or when the module's own ``__getattr__`` (PEP 562) does. ``target_attribute`` sends every other
statically absent name through ``setup_call('missing_callable', ...)``, where ``call_absent`` takes any
``ImportError`` for the tree's own missing name. So the same absent installed package, imported lazily by the
``__getattr__`` of the module's class (the spelling PEP 562 replaced) or of the bound class's instances, is the code's
failed call: a counterexample on the patch, and at the base commit the control a confirmed check's pass needs.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu1_support as support  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

RUNS = support.RUNS
#: A module the tree does not hold, that this interpreter has installed and G6's runner (``-I -S``) cannot import.
INSTALLED = "pydantic"
BASE_PASSED = {"result": "passed", "detail": "G6 observed the calls in its isolated runner",
               "observations": [{"input": {"args": [1], "kwargs": {}}, "output": {"returned": 1}}]}

#: The module's attributes come from a package loaded on first use, through the module's class.
LAZY_MODULE_CLASS = f'''
import sys, types
class _Lazy(types.ModuleType):
    def __getattr__(self, name):
        import {INSTALLED}
        return getattr({INSTALLED}, name)
sys.modules[__name__].__class__ = _Lazy
'''
#: The same, through the module's own ``__getattr__`` (PEP 562): the member the repair covers.
LAZY_MODULE_FUNCTION = f'''
def __getattr__(name):
    import {INSTALLED}
    return getattr({INSTALLED}, name)
'''
CLIENT = '''
class Client:
    def fetch(self, value):
        return value
def make():
    return Client()
'''
#: The bound class's methods come from a backend loaded on first use.
LAZY_CLIENT = f'''
class Client:
    def __getattr__(self, name):
        import {INSTALLED}
        return getattr({INSTALLED}, name)
def make():
    return Client()
'''


def _method_reading(folder, template, tree_source):
    bindings = {"constructor": {"call": "subject.make"}, "function": "subject.Client.fetch"}
    bindings.update({"cases": [{"args": [1], "returns": 1}]} if template == "example.v1" else
                    {"mode": "outputs", "calls": [{"args": [1]}]})
    _, data = support.applied(folder, template, "subject", CLIENT, bindings)
    (folder / "subject.py").write_text(tree_source, encoding="utf-8")
    found, ran = support.observed(folder, data)
    return support.read(found), found, ran


def _reading(request, template, where):
    disk = support.disk_for(request.node.nodeid) / "tree"
    if where == "module-class":
        return support.runner_reading(disk, template, RUNS, LAZY_MODULE_CLASS)
    return _method_reading(disk, template, LAZY_CLIENT)


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("where", ["module-class", "bound-instance"])
def test_a_lazy_import_the_runner_cannot_satisfy_is_not_a_counterexample(request, template, where):
    assert importlib.util.find_spec(INSTALLED) is not None   # the person's environment has the package
    on_patch, found, ran = _reading(request, template, where)
    verdict = vs.classify("preserve", on_patch, BASE_PASSED)
    assert on_patch["result"] == "error" and verdict["result"] != vs.COUNTEREXAMPLE, (
        "the name is looked up lazily, and the lookup raised ModuleNotFoundError for an installed package G6's own "
        "interpreter (started with -I -S) cannot import: no observation of the code's behaviour, and "
        "G6_E_OBSERVATION_UNAVAILABLE when the module's own __getattr__ does the same (the control below). "
        f"G6 reports: {verdict} from {found} (stderr {ran.stderr[-200:]!r})")


@pytest.mark.parametrize("where", ["module-class", "bound-instance"])
def test_a_lazy_import_the_runner_cannot_satisfy_at_the_base_commit_is_not_a_confirmed_checks_control(request, where):
    at_base, found, _ = _reading(request, "example.v1", where)
    # confirmed_checks.run's one rule for the control: the check ran to a failure at the base commit
    killed = vs._to_a_result(at_base) and at_base["result"] == "failed"
    assert not killed, (
        "the base commit's run never called the entry point (its lazy lookup needs a package G6's interpreter "
        f"lacks), and that run counts as the control a confirmed check's pass needs: {found}")


GATE = '''
class Gate:
    def __init__(self):
        pass
    def allow(self, who):
        if who != "owner":
            raise PermissionError(who)
        return True
'''
GATE_BINDINGS = {"operation": "adversary.Gate.allow", "constructor": {}, "refusal": "exception",
                 "exception": "PermissionError",
                 "cases": [{"who": "owner", "resource": "record", "args": ["owner"], "expected": "allowed"},
                           {"who": "guest", "resource": "record", "args": ["guest"], "expected": "refused"}]}
#: A dependency this environment lacks, as the pytest executor meets it (no interpreter here has it).
ABSENT = "no_such_dependency_tmfu1"
LAZY_GATE = f'''
class Gate:
    def __init__(self):
        pass
    def __getattr__(self, name):
        import {ABSENT}
        return getattr({ABSENT}, name)
'''
ACCESS_NODE = "test_g6_access_rule.py::test_access_rule"


def _compiled_reading(request, name, source):
    """The reading of a real pytest run of the compiled access rule on ``source`` (G6's own plugin records it)."""
    disk = support.disk_for(request.node.nodeid + name) / "tree"
    run, record, ran = support.real_pytest(disk, "access_rule.v1", "adversary", GATE, source, GATE_BINDINGS)
    return vs.reading(run, ACCESS_NODE), record


def test_a_compiled_checks_lazy_import_the_environment_lacks_is_not_a_counterexample(request):
    direct, _ = _compiled_reading(request, "direct", f"import {ABSENT}\n" + GATE)
    assert direct["result"] == "error" and direct["detail"].startswith("G6_E_OBSERVATION_UNAVAILABLE:"), direct
    lazy, record = _compiled_reading(request, "lazy", LAZY_GATE)
    verdict = vs.classify("preserve", lazy, {"result": "passed", "detail": "1 test(s) passed"})
    assert lazy["result"] == "error" and verdict["result"] != vs.COUNTEREXAMPLE, (
        "the module importing the dependency is G6_E_OBSERVATION_UNAVAILABLE (above); the same dependency, imported "
        f"when the bound instance looks the method up, is the code's failed call: {verdict}; G6's plugin recorded "
        f"{record.get('messages')}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_control_the_modules_own_getattr_is_unavailable(request, template):
    disk = support.disk_for(request.node.nodeid) / "tree"
    read, found, ran = support.runner_reading(disk, template, RUNS, LAZY_MODULE_FUNCTION)
    assert read["result"] == "error" and read["detail"] == (
        "G6_E_OBSERVATION_UNAVAILABLE: import or lazy lookup did not complete: builtins.ModuleNotFoundError"), (
        found, ran.stderr)


def test_control_a_name_the_module_lacks_is_a_failed_call(request):
    disk = support.disk_for(request.node.nodeid) / "tree"
    read, found, ran = support.runner_reading(disk, "example.v1", RUNS, "VERSION = 1\n")
    assert read["result"] == "failed" and read["detail"] == "call failed: builtins.AttributeError", (found, ran.stderr)


def test_control_a_method_the_class_lacks_is_a_failed_call(request):
    disk = support.disk_for(request.node.nodeid) / "tree"
    read, found, ran = _method_reading(disk, "example.v1", CLIENT.replace("def fetch", "def other"))
    assert read["result"] == "failed" and read["detail"] == "call failed: builtins.AttributeError", (found, ran.stderr)


def test_control_the_stated_trees_pass(request):
    disk = support.disk_for(request.node.nodeid)
    read, found, ran = support.runner_reading(disk / "function", "example.v1", RUNS, RUNS)
    assert read["result"] == "passed", (found, ran.stderr)
    read, found, ran = _method_reading(disk / "method", "example.v1", CLIENT)
    assert read["result"] == "passed", (found, ran.stderr)
