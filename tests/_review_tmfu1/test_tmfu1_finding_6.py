"""Review 1 of claude/template-methods-fu (9203d9c6), finding 6.

Class: one fact decided by two rules (the parent's review 4 I2, for the pair the repair does not reach).

The repair tells a base commit whose step raised from one whose read raised after every step ran, but only against a
patch that broke an evaluated invariant. Against a patch whose own step raises, ``_judge`` still compares the two
outcomes by their label alone (``also``: both ``operation_failed``) and reports ``fails_on_base_too``: the base
commit ran the very step the patch now raises in, and only its read of the state raised.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tmfu1_support as support  # noqa: E402
from g6.harness import verification_formal as vf  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

BANK = '''
class Account:
    def __init__(self):
        self.cents = 0
class Overdrawn(Exception):
    pass
def make():
    return Account()
def withdraw(account, amount):
    account.cents -= amount
def balance(account):
    return account.cents
'''
#: Every step runs; the read of the balance raises, so nothing is evaluated.
BANK_UNREADABLE = BANK.replace("return account.cents", "return account.balance")
#: The withdrawal raises before anything is read.
BANK_RAISES = BANK.replace("    account.cents -= amount\n", '    raise Overdrawn("now refused")\n')
BANK_BINDINGS = {
    "model": {"variables": {"balance": "Int", "act": "Int"}, "init": "(and (= balance 0) (= act 0))",
              "transition": "(and (= next_act 1) (= next_balance (- balance 5)))",
              "invariants": {"nonnegative": "(>= balance 0)"}},
    "action": "act", "constructor": {"call": "bank.make"},
    "actions": {"1": {"call": "bank.withdraw", "args": [5]}},
    "observe": {"balance": {"call": "bank.balance"}}}


def _replayed(folder, monkeypatch, source):
    """The compiled replay of one trace on ``source``, as ``search``'s reading of its run gives it to ``_judge``."""
    definition, data = support.applied(folder, "state_invariant.v1", "bank", BANK, BANK_BINDINGS)
    data["traces"] = [{"actions": [1], "ghost": {}}]
    with monkeypatch.context() as patch:
        namespace, _ = support.legacy(folder, patch, "state_invariant.v1", "bank", BANK, source, None, data)
        outcomes = [namespace["_replay"](trace) for trace in data["traces"]]
        sys.modules.pop("bank", None)
    failed = any(one["outcome"] in ("broken", "operation_failed") for one in outcomes)
    return {"result": "failed" if failed else "passed", "detail": "replayed",
            "observations": [{"input": index, "output": one} for index, one in enumerate(outcomes)]}


def test_a_step_the_patch_raises_in_is_not_a_failure_of_a_base_commit_that_ran_it(request, monkeypatch):
    disk = support.disk_for(request.node.nodeid)
    at_base = _replayed(disk / "base", monkeypatch, BANK_UNREADABLE)
    on_patch = _replayed(disk / "patch", monkeypatch, BANK_RAISES)
    # the fixture is what it says: the base commit ran the withdrawal and only its read raised; the patch's step raised
    assert at_base["observations"][0]["output"] == {"outcome": "operation_failed",
                                                    "why": "reading balance raised AttributeError"}
    assert on_patch["observations"][0]["output"] == {"outcome": "operation_failed",
                                                     "why": "step 1 (action 1) raised bank.Overdrawn"}
    verdict = vf._judge(1, "preserve", on_patch, at_base)
    assert verdict["result"] != vs.FAILS_ON_BASE_TOO and "at the base commit too" not in verdict["detail"], (
        "the base commit ran step 1 (the withdrawal) and raised only while the replay read the state; the patch "
        "raises in step 1 itself. G6 says the trace failed during an operation at the base commit too, so the "
        f"refusal the patch brings reads as one the base commit had: {verdict}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_the_same_step_raising_on_both_trees_fails_on_base_too(request, monkeypatch):
    disk = support.disk_for(request.node.nodeid)
    at_base = _replayed(disk / "base", monkeypatch, BANK_RAISES)
    on_patch = _replayed(disk / "patch", monkeypatch, BANK_RAISES)
    assert vf._judge(1, "preserve", on_patch, at_base)["result"] == vs.FAILS_ON_BASE_TOO


def test_control_a_base_commit_whose_read_failed_is_not_established_against_a_violation(request, monkeypatch):
    disk = support.disk_for(request.node.nodeid)
    at_base = _replayed(disk / "base", monkeypatch, BANK_UNREADABLE)
    on_patch = _replayed(disk / "patch", monkeypatch, BANK)
    verdict = vf._judge(1, "preserve", on_patch, at_base)
    assert verdict["result"] == vs.CHECK_ERROR and "refused the step" not in verdict["detail"]
    assert verdict["correspondence"]["invariants_not_evaluated_at_base"] == {
        "0": "reading balance raised AttributeError"}


def test_control_a_base_commit_that_evaluated_a_violation_is_not_said_to_have_failed_to_replay(request, monkeypatch):
    disk = support.disk_for(request.node.nodeid)
    at_base = _replayed(disk / "base", monkeypatch, BANK)
    on_patch = _replayed(disk / "patch", monkeypatch, BANK_RAISES)
    verdict = vf._judge(1, "preserve", on_patch, at_base)
    assert verdict["result"] == vs.CHECK_ERROR and "could not replay" not in verdict["detail"]
    assert "traces_not_replayable_at_base" not in verdict["correspondence"]


def test_control_a_step_the_base_commit_refuses_is_new_on_the_patch(request, monkeypatch):
    disk = support.disk_for(request.node.nodeid)
    guarded = BANK.replace("    account.cents -= amount\n",
                           '    if amount > account.cents:\n        raise Overdrawn("refused")\n'
                           "    account.cents -= amount\n")
    at_base = _replayed(disk / "base", monkeypatch, guarded)
    on_patch = _replayed(disk / "patch", monkeypatch, BANK)
    verdict = vf._judge(1, "preserve", on_patch, at_base)
    assert verdict["result"] == vs.COUNTEREXAMPLE and verdict["correspondence"]["steps_refused_at_base"] == [0]
