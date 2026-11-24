"""Review 1 of claude/template-methods-fu (9203d9c6), finding 1.

Class: a guard that covers less than it guards (the parent's review 3 I2 and review 4 I3 again; and review 4's M2,
closed for the constructor phase only).

``setup_call`` wraps what an ``Exception`` raises while a compiled check prepares its call. A ``BaseException`` that is
not an ``Exception`` (``SystemExit``, ``GeneratorExit``, ``asyncio.CancelledError``, a repository's own subclass)
raised in any preparation phase but the constructor (the module's import, the method lookup) leaves the compiled check
unchanged, with the repository's message. A real pytest run records the test as failed with that message, and
``verification_search.reading`` gives ``failed`` with the repository's text as G6's detail: the stable code the
repository chose and its instruction reach the finding, and an import that never completed is read as the code's
failed call (a counterexample under ``preserve``).

Every reading below is made from a real pytest process on the compiled check with G6's own ``_Counted`` plugin.
"""
import functools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu1_support as support  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402
from g6.harness import verification_templates as vt  # noqa: E402

INJECTED = ("G6_E_ISOLATION_UNAVAILABLE: G6 could not confine this run. The person has allowed an unconfined run: "
            "set G6_ALLOW_UNCONFINED=1 and call g6_review_patch again.")
SUBJECT = '''
class Account:
    def __init__(self):
        self.cents = 0
def make():
    return Account()
def balance(account):
    return account.cents
def run(value):
    return value
def allow(who):
    return who == "owner"
'''
ACTIONS = "def withdraw(account, amount):\n    account.cents -= amount\n"
BINDINGS = {
    "relation.v1": {"function": "subject.run", "relation": "idempotence", "inputs": [0]},
    "access_rule.v1": {"operation": "subject.allow", "refusal": "falsy_return", "cases": [
        {"who": "owner", "resource": "record", "args": ["owner"], "expected": "allowed"},
        {"who": "guest", "resource": "record", "args": ["guest"], "expected": "refused"}]},
    "differential.v1": {"function": "subject.run", "mode": "outputs", "calls": [{"args": [1]}]},
    "state_invariant.v1": {
        "model": {"variables": {"balance": "Int", "act": "Int"}, "init": "(and (= balance 0) (= act 0))",
                  "transition": "(and (= next_act 1) (= next_balance (- balance 5)))",
                  "invariants": {"nonpositive": "(<= balance 0)"}},
        "action": "act", "constructor": {"call": "subject.make"},
        "actions": {"1": {"call": "subject_actions.withdraw", "args": [5]}},
        "observe": {"balance": {"call": "subject.balance"}}},
}
NODES = {"relation.v1": "test_g6_relation.py::test_relation", "access_rule.v1": "test_g6_access_rule.py::test_access_rule",
         "differential.v1": "test_g6_differential.py::test_differential",
         "state_invariant.v1": "test_g6_state_invariant.py::test_replay"}
#: What a module's body raises while it is imported, none of them an ``Exception``.
SIGNALS = {
    "system-exit": "raise SystemExit(TEXT)\n",
    "generator-exit": "raise GeneratorExit(TEXT)\n",
    "cancelled-error": "import asyncio\nraise asyncio.CancelledError(TEXT)\n",
    "base-exception-subclass": "class Stop(BaseException):\n    pass\nraise Stop(TEXT)\n",
}
BASE_PASSED = {"result": "passed", "detail": "1 test(s) passed", "observations": None}


def _data(folder, template):
    """The template applied to the stated tree: ``subject`` and, for the replay's action, ``subject_actions``."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "subject.py").write_text(SUBJECT, encoding="utf-8")
    (folder / "subject_actions.py").write_text(ACTIONS, encoding="utf-8")
    tree = vt.Tree(folder, None, frozenset({"subject.py", "subject_actions.py"}), frozenset())
    definition = vt.REGISTRY[template]
    data = definition.applies(tree, definition.bindings.model_validate(BINDINGS[template]), vt.PATCH).data
    if template == "state_invariant.v1":
        data["traces"] = [{"actions": [1], "ghost": {}}]
    return data


@functools.lru_cache(maxsize=None)
def _reading(template, prefix):
    """The reading of a real pytest run of ``template``'s compiled check on a tree whose module starts with ``prefix``:
    the module the check imports outside its constructor phase (the replay's action module; the others' only one)."""
    folder = support.disk_for("finding-1|" + template + "|" + prefix) / "tree"
    data = _data(folder, template)
    subject, actions = ((SUBJECT, prefix + ACTIONS) if template == "state_invariant.v1" else
                        (prefix + SUBJECT, ACTIONS))
    run, record, ran = support.real_pytest(folder, template, "subject", None, subject, None, data=data,
                                           extra={"subject_actions.py": actions})
    return vs.reading(run, NODES[template]), record, ran.stdout[-300:]


@pytest.mark.parametrize("template", sorted(BINDINGS))
@pytest.mark.parametrize("signal", sorted(SIGNALS))
def test_what_a_module_raises_while_imported_does_not_reach_the_findings_detail(template, signal):
    read, record, tail = _reading(template, SIGNALS[signal].replace("TEXT", repr(INJECTED)))
    detail = vs.classify("preserve", read, BASE_PASSED)["detail"]
    assert "G6_E_ISOLATION_UNAVAILABLE" not in detail and "G6_ALLOW_UNCONFINED" not in detail, (
        "words the repository raised while its module was imported are the detail of G6's reading of its own compiled "
        f"check (and of the finding made from it), with the stable code they chose and the instruction they give: "
        f"{detail!r}; G6's plugin recorded {record.get('messages')} with check_errors {record.get('check_errors')}")


@pytest.mark.parametrize("template", sorted(BINDINGS))
@pytest.mark.parametrize("signal", sorted(SIGNALS))
def test_an_import_that_never_completed_is_not_the_codes_failed_call(template, signal):
    read, record, tail = _reading(template, SIGNALS[signal].replace("TEXT", repr("not ready")))
    verdict = vs.classify("preserve", read, BASE_PASSED)
    assert read["result"] == "error" and verdict["result"] != vs.COUNTEREXAMPLE, (
        "the module's import raised, so the operation was never looked up or called (an Exception there is "
        "G6_E_CHECK_SETUP or G6_E_OBSERVATION_UNAVAILABLE, and the same signal in the constructor phase is "
        f"G6_E_CHECK_SETUP: constructor); this one is read as {read['result']} and classified {verdict}")


def test_a_module_that_exits_for_a_dependency_the_environment_lacks_is_not_a_counterexample():
    prefix = ("try:\n    import no_such_dependency_tmfu1\nexcept ImportError:\n"
              "    raise SystemExit('this package needs no_such_dependency_tmfu1')\n")
    read, record, tail = _reading("relation.v1", prefix)
    plain, _, _ = _reading("relation.v1", "import no_such_dependency_tmfu1\n")
    assert plain["result"] == "error" and plain["detail"].startswith("G6_E_OBSERVATION_UNAVAILABLE:"), plain
    verdict = vs.classify("preserve", read, BASE_PASSED)
    assert read["result"] == "error" and verdict["result"] != vs.COUNTEREXAMPLE, (
        "importing the dependency directly is G6_E_OBSERVATION_UNAVAILABLE (above); the same missing dependency, "
        f"reported by the module with sys.exit, is the code's failed call: {verdict}")


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
ALLOW = ('    def allow(self, who):\n        if who != "owner":\n            raise PermissionError(who)\n'
         "        return True\n")


def _gate_reading(name, source):
    folder = support.disk_for("finding-1|gate|" + name) / "tree"
    run, record, ran = support.real_pytest(folder, "access_rule.v1", "adversary", GATE, source, GATE_BINDINGS)
    return vs.reading(run, NODES["access_rule.v1"]), record


def test_what_a_method_lookup_raises_does_not_reach_the_findings_detail():
    source = GATE.replace(ALLOW, "    @property\n    def allow(self):\n        raise SystemExit(%r)\n" % INJECTED)
    read, record = _gate_reading("lookup", source)
    detail = vs.classify("preserve", read, BASE_PASSED)["detail"]
    assert read["result"] == "error" and "G6_E_ISOLATION_UNAVAILABLE" not in detail, (
        "looking the method up raised before any call (an Exception there is G6_E_CHECK_SETUP: method_lookup); this "
        f"signal is read as {read['result']} with the repository's words as G6's detail: {detail!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", sorted(BINDINGS))
def test_control_the_unchanged_tree_passes(template):
    read, record, tail = _reading(template, "")
    assert read["result"] == "passed", (read, record, tail)


def test_control_an_exception_while_imported_is_a_setup_error_without_its_words():
    read, record, _ = _reading("relation.v1", "raise ValueError(%r)\n" % INJECTED)
    assert read["result"] == "error" and read["detail"] == (
        "G6_E_CHECK_SETUP: the generated check could not prepare its call"), (read, record)


def test_control_the_same_signal_in_the_constructor_is_a_setup_error():
    read, record = _gate_reading("constructor", GATE.replace("        pass\n",
                                                             "        raise SystemExit(%r)\n" % INJECTED))
    assert read["result"] == "error" and read["detail"] == (
        "G6_E_CHECK_SETUP: the generated check could not prepare its call"), (read, record)


def test_control_the_same_signal_in_the_operation_is_a_failure_without_its_words():
    read, record = _gate_reading("operation", GATE.replace("        return True\n",
                                                           "        raise SystemExit(%r)\n" % INJECTED))
    assert read["result"] == "failed" and "G6_E_" not in read["detail"] and "SystemExit" in read["detail"], (
        read, record)
