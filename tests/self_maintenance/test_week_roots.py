# SPDX-License-Identifier: Apache-2.0
"""The weekly allowance book held to three roots, each by an inventory read from its code (review wksfu2).

1. Names. Every name a method of the store or the meter takes (an owner, a venture, a provider, an account, a month, a
   reservation, a tick) is converted once, where it enters, to plain text, and no statement of the store binds anything but a
   plain value: a caller's own kind of text can answer SQLite differently each time it is bound (check_then_act_race).
2. Transactions. Whatever fails at any statement that begins, ends or undoes a transaction or a step, by an error or by an
   interrupt, nothing a failed transaction or step wrote is kept, and a connection whose rollback is not known to have
   happened admits nothing more (guard_covers_less_than_it_guards).
3. Numbers. No value is turned into a number on its way to a check that decides admission: settings, sums and amounts are
   read by the one rule (``gates.exact_number``), and a sum is compared with its limit in one place (``gates.over``), with a
   slack that is a share of the limit (boundary_case, one_fact_two_rules).

And to four more that review wksfu3 found the store's own times and the settings still open to (sections 4 to 7):

4. Readings. The store is told when every reading the meter is given was taken, with a reset or without, before it writes
   any stamp, so no charge written after a reading was taken is dated inside it; and a reservation is refused while the
   clock reads earlier than what the store has recorded (guard_covers_less_than_it_guards, boundary_case).
5. The week. Only a reset the provider gave moves the kept week, which the store alone keeps (one_fact_two_rules).
6. Windows. A list of windows becomes a mapping in one place, which refuses a provider named twice (boundary_case).
7. A reader starts unread for every provider it asks (failure_read_as_success).

And to what review wksfu4, the last, found (sections 8 to 11; the lists of sections 1 to 4 follow the code as it now is):

8. A reset is believed by one rule in both directions, and a week's end already past never moves a kept week.
9. A reading flows one way: taken, told to the store, given once to a decision; a charge is on top of it unless settled
   strictly before it.
10. Whether a figure still stands is one rule for the meter, its schedule and its reader.
11. A sum that overflows and a time with nothing after it are refusals, and never errors that a store which only grows
    could not clear.
"""
from __future__ import annotations

import ast
import math
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.operator import gates
from g6.operator import store as store_module
from g6.operator.store import BudgetRefused, OperatorStore, OwnerScope
from g6.self_maintenance import pacing, usage
from g6.self_maintenance.pacing import OWNER, AccountReading, Admission, BudgetMeter, ProviderWindow, windows_from
from g6.self_maintenance.usage import NOT_REPORTED, WEEK_MINUTES, AccountReader

START = datetime(2026, 11, 1, tzinfo=UTC)
NOW = START + timedelta(days=2)
WEEK = timedelta(days=7)
LAST_DAY = START + timedelta(days=6)
_BOUND = (str, int, float, bytes, type(None))
_REFUSALS = (ValueError, TypeError, LookupError, BudgetRefused)
_TREES = {module.__name__: ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
          for module in (store_module, gates, pacing, usage)}


def _functions(tree) -> list[ast.FunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def _inside(tree, node) -> str:
    """The name of the innermost function ``node`` is in, or ``""``."""
    holding = [f for f in _functions(tree) if f.lineno <= node.lineno <= f.end_lineno]
    return max(holding, key=lambda f: f.lineno).name if holding else ""


def _meter(store, allowance: float = 100, read_account=None) -> BudgetMeter:
    return BudgetMeter((ProviderWindow("codex", allowance, START, reserve_share=0),), store=store, read_account=read_account)


# 1. Names ------------------------------------------------------------------------------------------------------------------

class _Moving(str):
    """Text of a caller's own class: the text it holds is right, and it answers SQLite with another each time it is bound."""
    asked: list[str] = []

    def __conform__(self, protocol):
        type(self).asked.append(str.__str__(self))
        return f"another-{len(type(self).asked)}"


class _Lying(str):
    """Text whose own rendering, equality and hash say it is some other text."""

    def __str__(self):
        return "another"

    def __eq__(self, other):
        return True

    def __hash__(self):
        return hash("another")


class _Recording:
    """The scope's connection, keeping every value a statement was given to bind."""

    def __init__(self, real) -> None:
        self.real, self.bound = real, []

    def execute(self, sql, *args):
        for group in args:
            self.bound.extend(group.values() if isinstance(group, dict) else group)
        return self.real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self.real, name)


#: Text the store binds beside a row and checks nothing against: held to plain values where it is bound, not turned to text.
_BESIDE = {"OwnerScope.set_status(by)", "OwnerScope.record(kind)", "OwnerScope.record(ref)", "OwnerScope.evidence(kind)",
           "OwnerScope.evidence(ref)"}


def _scene():
    """A store with an open reservation a tick made, a kept grid, and a monthly venture holding a reservation."""
    store = OperatorStore(":memory:", clock=lambda: NOW)
    meter = _meter(store)
    spend = meter.reserve("codex", NOW, 10, tick="t1")
    assert spend.admitted
    scope = meter.scope
    scope.keep_grid("codex", START + WEEK, NOW)
    monthly = scope.create_venture("monthly", caps={"model": 50, "cash": 5})["venture_id"]
    held = scope.reserve(monthly, month="2026-11", account="model", amount=5)
    scope.conn = _Recording(scope.conn)
    return store, meter, {"monthly": monthly, "held": held}


def _calls(store, meter, ids) -> dict:
    """Every method that takes a name, as a call of it with each name it takes, and the plain names that make it work."""
    scope, caps, hour = meter.scope, {"model": 100, "cash": 0}, timedelta(hours=1)
    monthly = ids["monthly"]

    def other_scope(owner):
        made = OwnerScope(store, owner)
        made.conn = scope.conn
        return made.list_ventures()

    def decide():
        return "", START, NOW, START + WEEK

    return {
        "OwnerScope.__init__": (other_scope, {"owner": OWNER}),
        "OwnerScope.resolve": (lambda venture_id: scope.resolve(venture_id), {"venture_id": monthly}),
        "OwnerScope.create_venture": (lambda name: scope.create_venture(name, caps=caps), {"name": "another venture"}),
        "OwnerScope.has_venture": (lambda name: scope.has_venture(name), {"name": "monthly"}),
        "OwnerScope.week_grid": (lambda provider: scope.week_grid(provider), {"provider": "codex"}),
        "OwnerScope.kept_grid": (lambda provider: scope.kept_grid(provider), {"provider": "codex"}),
        "OwnerScope.keep_grid": (lambda provider: scope.keep_grid(provider, START + 2 * WEEK, NOW + hour), {"provider": "codex"}),
        "OwnerScope.keep_reading": (lambda provider: scope.keep_reading(provider, NOW + hour), {"provider": "codex"}),
        "OwnerScope.week_venture": (lambda provider: scope.week_venture(provider, START), {"provider": "codex"}),
        "OwnerScope.week_held": (lambda provider, account: scope.week_held(provider, account, START, START + WEEK),
                                 {"provider": "codex", "account": "model"}),
        "OwnerScope.reserve_span": (lambda provider, account, tick: scope.reserve_span(
            provider, account=account, amount=1, caps=caps, decide=decide, tick=tick),
            {"provider": "codex", "account": "model", "tick": "t2"}),
        "OwnerScope.settle_open": (lambda provider, account: scope.settle_open(provider, account),
                                   {"provider": "codex", "account": "model"}),
        "OwnerScope.adopt_books": (lambda providers: scope.adopt_books(providers), {"providers": "codex"}),
        "OwnerScope.venture_named": (lambda name: scope.venture_named(name, caps=caps), {"name": "named"}),
        "OwnerScope.venture": (lambda venture_id: scope.venture(venture_id), {"venture_id": monthly}),
        "OwnerScope.set_status": (lambda venture_id, status, by: scope.set_status(venture_id, status, by=by),
                                  {"venture_id": monthly, "status": "paused", "by": "ada"}),
        "OwnerScope.record": (lambda venture_id, kind, ref: scope.record(venture_id, kind, ref=ref),
                              {"venture_id": monthly, "kind": "note", "ref": "r1"}),
        "OwnerScope.evidence": (lambda venture_id, kind, ref: scope.evidence(venture_id, kind=kind, ref=ref),
                                {"venture_id": monthly, "kind": "created", "ref": ""}),
        "OwnerScope.committed": (lambda venture_id, month: scope.committed(venture_id, month),
                                 {"venture_id": monthly, "month": "2026-11"}),
        "OwnerScope.outstanding": (lambda venture_id, month: scope.outstanding(venture_id, month),
                                   {"venture_id": monthly, "month": "2026-11"}),
        "OwnerScope.reserve": (lambda venture_id, month, account: scope.reserve(
            venture_id, month=month, account=account, amount=1), {"venture_id": monthly, "month": "2026-11", "account": "model"}),
        "OwnerScope.settle": (lambda venture_id, reservation: scope.settle(venture_id, reservation),
                              {"venture_id": monthly, "reservation": ids["held"]}),
        "BudgetMeter.reading": (lambda provider: meter.reading(provider, NOW), {"provider": "codex"}),
        "BudgetMeter.limit_reached": (lambda provider: meter.limit_reached(provider, NOW + hour), {"provider": "codex"}),
        "BudgetMeter.reserve": (lambda provider, tick: meter.reserve(provider, NOW, 1, tick=tick),
                                {"provider": "codex", "tick": "t3"}),
        "BudgetMeter.recover": (lambda provider: meter.recover(provider), {"provider": "codex"}),
        "BudgetMeter.settle_open": (lambda provider: meter.settle_open(provider), {"provider": "codex"}),
        "BudgetMeter.tell": (lambda provider: meter.tell(provider, NOW + hour), {"provider": "codex"}),
        "BudgetMeter.stands": (lambda provider: meter.stands(provider, AccountReading(used=1, estimated=False, read_at=NOW), NOW),
                               {"provider": "codex"}),
    }


def _names_in_the_code() -> set[str]:
    """Listed from the code: every parameter typed as text (``str``, ``str | None`` or a sequence of them; a callback is
    none) of every public method, and the constructor, of the store's owner scope and of the meter."""
    found = set()
    for module, name in ((store_module.__name__, "OwnerScope"), (pacing.__name__, "BudgetMeter")):
        node = next(n for n in ast.walk(_TREES[module]) if isinstance(n, ast.ClassDef) and n.name == name)
        for f in node.body:
            if isinstance(f, ast.FunctionDef) and (not f.name.startswith("_") or f.name == "__init__"):
                found |= {f"{name}.{f.name}({a.arg})" for a in (*f.args.args, *f.args.kwonlyargs)
                          if a.annotation is not None and ast.unparse(a.annotation) in ("str", "str | None", "Sequence[str]")}
    return found


def _listed() -> list[tuple[str, str]]:
    store, meter, ids = _scene()
    try:
        return [(call, name) for call, (_, names) in _calls(store, meter, ids).items() for name in names]
    finally:
        store.close()


def test_the_names_listed_here_are_the_names_the_code_takes():
    assert {f"{call}({name})" for call, name in _listed()} == _names_in_the_code()


def _outcome(call: str, name: str, spelling):
    """One call with one of its names spelled another way: what it gave or raised, what its statements bound, and how many
    rows the connection changed."""
    store, meter, ids = _scene()
    try:
        run, names = _calls(store, meter, ids)[call]
        given = spelling(names[name])
        before = store.conn.total_changes
        try:
            result = run(**{**names, name: (given,) if name == "providers" else given})
        except BaseException as exc:  # noqa: BLE001 - what it raised is the outcome
            result = exc
        return result, list(meter.scope.conn.bound), store.conn.total_changes - before
    finally:
        store.close()


def _nothing(result) -> bool:
    """Whether a call that did not raise still did nothing with the name: no venture, no rows, nothing settled, or an
    admission refused with its reason."""
    if isinstance(result, Admission):
        return not result.admitted and bool(result.reason)
    if isinstance(result, AccountReading):
        return bool(result.unread)                         # a reading that says nothing of the account
    return isinstance(result, _REFUSALS) or result in ("", {}, 0.0)


@pytest.mark.parametrize("call, name", _listed(), ids=lambda value: value)
def test_a_name_of_a_callers_own_class_is_read_once_at_the_text_it_holds(call, name):
    """The reviewer's spelling: text that answers SQLite with another text each time it is bound. It is never asked: a
    name is read at the text it holds, and the call does what the plain name does; text bound beside a row is refused."""
    _Moving.asked.clear()
    plain, _, _ = _outcome(call, name, lambda text: text)
    assert not isinstance(plain, BaseException), plain
    result, bound, changed = _outcome(call, name, _Moving)
    assert not _Moving.asked, f"{call}({name}) let SQLite ask the caller's text what it is: {_Moving.asked}"
    assert all(type(value) in _BOUND for value in bound), [type(value).__name__ for value in bound]
    if f"{call}({name})" in _BESIDE:
        assert isinstance(result, TypeError), result
    else:
        assert not isinstance(result, BaseException), result
        assert type(result) is type(plain)
        if isinstance(plain, Admission):
            assert (result.admitted, result.reason) == (plain.admitted, plain.reason)
        elif isinstance(plain, float | bool | type(None)):
            assert result == plain


@pytest.mark.parametrize("call, name", _listed(), ids=lambda value: value)
@pytest.mark.parametrize("spelling", [_Lying, lambda text: text.encode(), lambda text: 7, lambda text: None,
                                      lambda text: object()], ids=["lying text", "bytes", "int", "none", "object"])
def test_a_name_that_is_not_plain_text_reaches_no_statement(call, name, spelling):
    """A name that does not read as one text (its own rendering says otherwise, or it is no text at all) is refused, or does
    nothing, before any statement binds it, and changes no row. Text bound beside a row may be another plain value, as it
    always could, and is never anything else."""
    result, bound, changed = _outcome(call, name, spelling)
    assert all(type(value) in _BOUND for value in bound), [type(value).__name__ for value in bound]
    if f"{call}({name})" in _BESIDE:
        assert not isinstance(result, BaseException) or isinstance(result, (*_REFUSALS, sqlite3.IntegrityError)), result
    else:
        assert _nothing(result) and not changed, result


def test_every_statement_of_the_store_binds_through_the_one_gate():
    """Listed from the code: every statement the store runs on its connection that is given values to bind. Each hands
    them through ``_plain``, which lets through only what SQLite stores as it is given."""
    tree = _TREES[store_module.__name__]
    bound, loose = [], []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("execute", "executemany")
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "conn" and len(node.args) > 1):
            where = f"{_inside(tree, node)}:{node.lineno}"
            bound.append(where)
            given = node.args[1]
            if not (isinstance(given, ast.Call) and isinstance(given.func, ast.Name) and given.func.id == "_plain"):
                loose.append(where)
    assert len(bound) >= 20 and {where.split(":")[0] for where in bound} >= {
        "resolve", "_named", "kept_grid", "keep_grid", "week_held", "reserve_span", "settle_open", "_append_ledger",
        "_append_evidence", "_settle", "_stamp", "_kept_times", "_month"}, bound
    assert not loose, loose
    for value in ("a", 1, 1.5, b"a", None):
        assert store_module._plain(value) == (value,)
    for value in (_Moving("a"), True, [1], datetime(2026, 11, 1, tzinfo=UTC), object()):
        with pytest.raises(TypeError, match="plain"):
            store_module._plain("a", value)


def test_a_settlement_names_one_reservation_in_every_statement():
    """Finding 1: a reservation of a caller's class named the newer reservation to the statement that found it and the
    older one to the statements that settled, so 90 still held read as settled."""
    now = [START - timedelta(days=1)]
    store = OperatorStore(":memory:", clock=lambda: now[0])
    try:
        meter = _meter(store)
        older = meter.reserve("codex", now[0], 90)
        now[0] = LAST_DAY
        newer = meter.reserve("codex", now[0], 1)

        class Moving(str):
            answers = iter((newer.reservation, newer.reservation, older.reservation, older.reservation, newer.reservation))

            def __conform__(self, protocol):
                return next(self.answers)

        assert meter.scope.settle(newer.venture, Moving(newer.reservation)) == 1
        assert meter.reading("codex", now[0]).used == 91
        assert not meter.reserve("codex", now[0], 90).admitted
        with pytest.raises(ValueError, match="named by text"):
            meter.scope.settle(older.venture, 7)
    finally:
        store.close()


def test_the_account_a_reservation_is_checked_on_is_the_account_it_is_written_on():
    """Finding 2: text holding ``cash`` was checked against the cash cap and bound as ``model``."""
    class Account(str):
        def __conform__(self, protocol):
            return "model"

    store = OperatorStore(":memory:")
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 0, "cash": 100})["venture_id"]
        scope.reserve(venture, month="2026-11", account=Account("cash"), amount=100)
        assert scope.committed(venture, "2026-11") == {"cash": 100.0}
        for account in (b"cash", None, _Lying("cash")):
            with pytest.raises(BudgetRefused, match="text"):
                scope.reserve(venture, month="2026-11", account=account, amount=1)
    finally:
        store.close()


def test_the_grid_a_reading_is_compared_with_is_the_grid_it_replaces():
    """Finding 4: a provider of a caller's class named no kept grid to the check and Codex's to the write."""
    store = OperatorStore(":memory:", clock=lambda: START)
    try:
        scope = store.for_owner(OWNER)
        kept = scope.keep_grid("codex", START + WEEK, START + timedelta(hours=2))

        class Provider(str):
            answers = iter(("unknown-provider", "codex"))

            def __conform__(self, protocol):
                return next(self.answers)

        assert scope.keep_grid(Provider("codex"), START + timedelta(days=8), START) == kept
        assert scope.kept_grid("codex") == kept
    finally:
        store.close()


# 2. Transactions -----------------------------------------------------------------------------------------------------------

#: Every statement of the store that begins, ends or undoes a transaction or a step, as (function, statement).
_CONTROLS = {("__init__", "commit"), ("script", "executescript"), ("immediate", "BEGIN"), ("immediate", "commit"),
             ("immediate", "SAVEPOINT"), ("immediate", "RELEASE"), ("_undo", "ROLLBACK TO"), ("_undo", "RELEASE"),
             ("_abandon", "rollback")}
_WORDS = ("BEGIN", "SAVEPOINT", "RELEASE", "ROLLBACK TO", "ROLLBACK", "COMMIT", "END")


def _leading(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return node.values[0].value
    return ""


def test_the_controls_listed_here_are_the_ones_in_the_code():
    """Listed from the code: every ``commit``, ``rollback`` and ``executescript`` on the store's connection, and every
    statement run on it whose text begins a transaction or a savepoint, or ends or undoes one."""
    tree = _TREES[store_module.__name__]
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "conn"):
            continue
        if node.func.attr in ("commit", "rollback", "executescript"):
            found.add((_inside(tree, node), node.func.attr))
        elif node.func.attr == "execute" and node.args:
            word = next((w for w in _WORDS if _leading(node.args[0]).upper().lstrip().startswith(w)), None)
            if word:
                found.add((_inside(tree, node), word))
    assert found == _CONTROLS, sorted(found ^ _CONTROLS)


class _Faulty:
    """The scope's connection, failing once at a chosen statement: before it runs, or after it has run."""

    def __init__(self, real) -> None:
        self.real, self.armed = real, {}

    def arm(self, statement: str, error: BaseException, after: bool = False) -> None:
        self.armed[statement] = (error, after)

    def _maybe(self, statement: str, run):
        if statement not in self.armed:
            return run()
        error, after = self.armed.pop(statement)
        if after:
            run()
        raise error

    def execute(self, sql, *args):
        word = next((w for w in _WORDS if sql.upper().lstrip().startswith(w)), "")
        return self._maybe(word, lambda: self.real.execute(sql, *args))

    def commit(self):
        return self._maybe("commit", self.real.commit)

    def rollback(self):
        return self._maybe("rollback", self.real.rollback)

    def __getattr__(self, name):
        return getattr(self.real, name)


def _kinds(path) -> list[str]:
    """What a store opened again on the file holds of the venture's history."""
    reopened = OperatorStore(path)
    try:
        return [row["kind"] for row in reopened.conn.execute("SELECT kind FROM evidence ORDER BY seq")]
    finally:
        reopened.close()


def _poisoned(store, scope) -> None:
    """Nothing more is written on a poisoned store: no transaction, no script, no reservation."""
    assert store.poisoned
    for write in (lambda: scope.record(scope.list_ventures()[0]["venture_id"], "next"),
                  lambda: store.script("CREATE TABLE IF NOT EXISTS probe (n INTEGER)"),
                  lambda: _meter(store).reserve("codex", NOW, 1)):
        with pytest.raises(sqlite3.OperationalError, match="nothing more is written on this connection"):
            write()


def _transaction(tmp_path, arm, *, nested: bool, step_fails: bool = False, work_fails: bool = False):
    """One outer transaction, with one step inside it when ``nested``, on a connection armed to fail. It gives what the
    outer transaction raised, whether the step came through, whether the store was poisoned, and what a store opened again
    holds. Before it is closed the store is shown to be in one of two states: clean and working, or poisoned and refusing."""
    path = tmp_path / "op.db"
    store = OperatorStore(path)
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 10, "cash": 0})["venture_id"]
        faulty = scope.conn = _Faulty(scope.conn)
        arm(faulty)
        raised = stepped = None
        try:
            with scope.immediate():
                scope._append_evidence(venture, "outer", "", None)
                if nested:
                    try:
                        with scope.immediate():
                            scope._append_evidence(venture, "step", "", None)
                            if step_fails:
                                raise ValueError("the step failed")
                        stepped = True
                    except BaseException:  # noqa: BLE001 - a caller that goes on after a failed step, whatever stopped it
                        stepped = False
                    scope._append_evidence(venture, "after", "", None)
                if work_fails:
                    raise ValueError("the work failed")
        except BaseException as exc:  # noqa: BLE001 - what the transaction raised is the outcome
            raised = exc
        assert not faulty.armed, f"the fault was never reached: {faulty.armed}"
        assert not store.in_transaction()
        poisoned = bool(getattr(store, "poisoned", ""))       # a store that has no such word is never poisoned
        if poisoned:
            _poisoned(store, scope)
        else:
            assert store.conn.in_transaction is False
            scope.record(venture, "next")                     # the next transaction is a fresh one, and is kept
    finally:
        store.close()
    kept = _kinds(path)
    assert ("next" in kept) is not poisoned
    return raised, stepped, poisoned, [kind for kind in kept if kind != "next"]


_ERRORS = {"error": lambda: sqlite3.OperationalError("injected"), "interrupt": lambda: KeyboardInterrupt("injected")}
_ALL = ["created", "outer", "step", "after"]
_NO_STEP = ["created", "outer", "after"]


@pytest.mark.parametrize("error", _ERRORS)
@pytest.mark.parametrize("after", [False, True], ids=["before it runs", "after it ran"])
class TestEveryControlThatFails:
    """Each statement of ``_CONTROLS`` failing, by an error and by an interrupt, before it runs and after it has run. What
    a failed transaction or step wrote is never kept, and what is kept is a whole transaction."""

    def test_the_begin(self, tmp_path, error, after):
        raised, _, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("BEGIN", _ERRORS[error](), after), nested=False)
        assert type(raised) is type(_ERRORS[error]()) and not poisoned and kept == ["created"]

    def test_the_commit(self, tmp_path, error, after):
        """Finding 5: a commit that failed left its transaction open for the next script to commit. A commit that says it
        failed after it took effect has kept the whole transaction, which is the one case a failure keeps anything."""
        raised, _, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("commit", _ERRORS[error](), after), nested=False)
        assert type(raised) is type(_ERRORS[error]()) and not poisoned
        assert kept == (["created", "outer"] if after else ["created"])

    def test_the_rollback_after_failed_work(self, tmp_path, error, after):
        raised, _, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("rollback", _ERRORS[error](), after),
                                                 nested=False, work_fails=True)
        assert type(raised) in (ValueError, KeyboardInterrupt) and poisoned and kept == ["created"]

    def test_the_rollback_after_a_failed_commit(self, tmp_path, error, after):
        def arm(conn):
            conn.arm("commit", sqlite3.OperationalError("the commit was refused"))
            conn.arm("rollback", _ERRORS[error](), after)

        raised, _, poisoned, kept = _transaction(tmp_path, arm, nested=False)
        assert raised is not None and poisoned and kept == ["created"]

    def test_the_savepoint(self, tmp_path, error, after):
        """Review wksfu3, finding 6. This expected the transaction to go on and commit after a step's savepoint failed. A
        savepoint that failed after it ran is still there, under the name the next step at that depth takes, and a later
        undo rolls back to it. So a failed savepoint marks its transaction, as a failed undo does: nothing of it is kept."""
        raised, stepped, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("SAVEPOINT", _ERRORS[error](), after), nested=True)
        assert stepped is False and not poisoned and kept == ["created"]
        assert isinstance(raised, sqlite3.OperationalError) and "nothing of this transaction is kept" in str(raised)

    def test_the_release_of_a_step_that_worked(self, tmp_path, error, after):
        """Before it runs, the step is rolled back and the rest stands. After it ran there is no savepoint left to roll back
        to, so the undo fails and the whole transaction goes."""
        raised, stepped, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("RELEASE", _ERRORS[error](), after), nested=True)
        assert stepped is False and not poisoned
        assert (raised is None and kept == _NO_STEP) if not after else (
            isinstance(raised, sqlite3.OperationalError) and kept == ["created"])

    def test_the_rollback_of_a_failed_step(self, tmp_path, error, after):
        """Finding 6: an interrupt during the undo left the transaction unmarked, and it committed the failed step."""
        raised, stepped, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("ROLLBACK TO", _ERRORS[error](), after),
                                                       nested=True, step_fails=True)
        assert stepped is False and not poisoned and kept == ["created"]
        assert isinstance(raised, sqlite3.OperationalError) and "nothing of this transaction is kept" in str(raised)

    def test_the_release_of_a_failed_step(self, tmp_path, error, after):
        raised, stepped, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("RELEASE", _ERRORS[error](), after),
                                                       nested=True, step_fails=True)
        assert stepped is False and not poisoned and kept == ["created"]
        assert isinstance(raised, sqlite3.OperationalError) and "nothing of this transaction is kept" in str(raised)


def test_a_transaction_with_nothing_failing_keeps_all_of_it(tmp_path):
    assert _transaction(tmp_path, lambda c: None, nested=True) == (None, True, False, _ALL)
    (tmp_path / "second").mkdir()
    raised, stepped, poisoned, kept = _transaction(tmp_path / "second", lambda c: None, nested=True, step_fails=True)
    assert (raised, stepped, poisoned, kept) == (None, False, False, _NO_STEP)


def test_a_rollback_that_returns_with_the_transaction_still_open_poisons_the_store(tmp_path):
    """The connection is asked whether the rollback happened: one that returns and leaves its transaction open is not one."""
    class Deaf(_Faulty):
        def rollback(self):
            return None

    store = OperatorStore(tmp_path / "op.db")
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 10, "cash": 0})["venture_id"]
        scope.conn = Deaf(scope.conn)
        with pytest.raises(ValueError, match="the work failed"):
            with scope.immediate():
                scope._append_evidence(venture, "outer", "", None)
                raise ValueError("the work failed")
        _poisoned(store, scope)
    finally:
        store.close()
    assert _kinds(tmp_path / "op.db") == ["created"]


def test_work_between_a_failed_savepoint_and_a_failed_release_is_never_lost_in_silence(tmp_path):
    """Review wksfu3, finding 6: a savepoint that failed after it ran stayed on the connection, and the undo of a later step
    whose own savepoint was gone rolled back to it: the transaction committed without what it wrote in between."""
    class AfterItRan(_Faulty):
        def execute(self, sql, *args):
            word = next((w for w in _WORDS if sql.upper().lstrip().startswith(w)), "")
            done = self.real.execute(sql, *args)
            if word in self.armed:
                raise self.armed.pop(word)[0]
            return done

    path = tmp_path / "op.db"
    store = OperatorStore(path)
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 10, "cash": 0})["venture_id"]
        faulty = scope.conn = AfterItRan(scope.conn)
        with pytest.raises(sqlite3.OperationalError, match="nothing of this transaction is kept"):
            with scope.immediate():
                scope._append_evidence(venture, "outer", "", None)
                for word in ("SAVEPOINT", "RELEASE"):
                    faulty.arm(word, sqlite3.OperationalError(f"injected after {word} ran"))
                    with pytest.raises(sqlite3.OperationalError):
                        with scope.immediate():
                            scope._append_evidence(venture, f"step {word}", "", None)
                    scope._append_evidence(venture, f"after {word}", "", None)
        assert not faulty.armed and not store.poisoned
    finally:
        store.close()
    assert _kinds(path) == ["created"]


def test_a_step_in_a_transaction_that_can_no_longer_commit_ends_in_that_error():
    store = OperatorStore(":memory:", clock=lambda: NOW)
    try:
        meter = _meter(store)
        scope = meter.scope
        faulty = scope.conn = _Faulty(scope.conn)
        with pytest.raises(sqlite3.OperationalError, match="nothing of this transaction is kept"):
            with scope.immediate():
                faulty.arm("ROLLBACK TO", KeyboardInterrupt("injected"))
                with pytest.raises(KeyboardInterrupt):
                    with scope.immediate():
                        raise ValueError("the step failed")
                meter.reserve("codex", NOW, 1)             # an admission is a step: it ends in the error, never told it was kept
        assert meter.reading("codex", NOW).used == 0 and not store.poisoned
        assert meter.reserve("codex", NOW, 1).admitted     # the next transaction is a fresh one
    finally:
        store.close()


def test_a_script_never_runs_while_the_connection_holds_a_transaction(tmp_path):
    """A script commits whatever is open before it runs, so it runs only when the connection itself says nothing is: the
    guard read the calling thread's depth alone, which a transaction left open by a failed commit is not in."""
    store = OperatorStore(tmp_path / "op.db")
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 10, "cash": 0})["venture_id"]
        store.conn.execute("BEGIN IMMEDIATE")
        scope._append_evidence(venture, "left open", "", None)
        with pytest.raises(RuntimeError, match="never runs inside a transaction"):
            store.script("CREATE TABLE IF NOT EXISTS probe (n INTEGER)")
        store.conn.rollback()
        store.script("CREATE TABLE IF NOT EXISTS probe (n INTEGER)")

        class Failing(_Faulty):
            def executescript(self, sql):
                raise sqlite3.OperationalError("injected")

        real, store.conn = store.conn, Failing(store.conn)
        with pytest.raises(sqlite3.OperationalError, match="injected"):
            store.script("CREATE TABLE IF NOT EXISTS probe2 (n INTEGER)")
        store.conn = real
        assert not store.poisoned and store.conn.in_transaction is False
    finally:
        store.close()
    assert _kinds(tmp_path / "op.db") == ["created"]


def test_a_store_whose_opening_commit_fails_is_no_store(tmp_path, monkeypatch):
    """The one commit outside a transaction of the store's own: the column a folder kept before readings were timed gains."""
    path = tmp_path / "op.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE week_grids (owner TEXT NOT NULL, provider TEXT NOT NULL, reset TEXT NOT NULL,"
                " PRIMARY KEY (owner, provider))")
    old.commit()
    old.close()
    opened, real = [], store_module.open_db

    class Refusing(_Faulty):
        def commit(self):
            raise sqlite3.OperationalError("injected")

    def refusing(target):
        opened.append(Refusing(real(target)))
        return opened[-1]

    with monkeypatch.context() as patch:
        patch.setattr(store_module, "open_db", refusing)
        with pytest.raises(sqlite3.OperationalError, match="injected"):
            OperatorStore(path)
    opened[-1].real.close()
    store = OperatorStore(path)
    try:
        assert store.for_owner(OWNER).kept_grid("codex") == (None, None)
    finally:
        store.close()


# 3. Numbers ----------------------------------------------------------------------------------------------------------------

class _LyingFloat(float):
    def __float__(self):
        return 1000.0


#: Every call of ``float``, ``int`` or ``bool`` in the four modules, as (module, function, text). Each is of a value that is
#: already plain where it stands: inside the one rule itself, a number this code computed or SQLite's own, or a wait read
#: from G6's own words. A new one fails here until it is shown to be the same.
_CONVERSIONS = {
    ("g6.operator.gates", "exact_number", "float(value)"), ("g6.operator.gates", "exact_number", "int(value)"),
    ("g6.operator.gates", "exact_number", "float(whole)"),
    ("g6.operator.store", "_append_evidence", "int(cur.lastrowid)"), ("g6.operator.store", "_settle", "float(metered)"),
    ("g6.operator.store", "_kept_times", "int(newest)"),
    ("g6.self_maintenance.usage", "limit_ends", "float(limit['seconds'] or 0)"),
    # A host program's time limit, taken as a plain number once, before the program starts, so nothing of a caller's own
    # class answers for it later; it bounds how long a launch waits and reaches no budget check (the refusals follow-up).
    ("g6.self_maintenance.usage", "_complete_host", "float(caller.timeout)"),
    ("g6.self_maintenance.usage", "_launch", "float(seconds)"),
}


def test_no_value_is_turned_into_a_number_on_its_way_to_a_check():
    found = {(name, _inside(tree, node), ast.unparse(node)) for name, tree in _TREES.items() for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("float", "int", "bool")}
    assert found == _CONVERSIONS, sorted(found ^ _CONVERSIONS)


_SETTINGS = {"provider": "codex", "allowance": 100, "week_start": START.isoformat(), "reserve_share": 0.25, "estimated": False}


def test_every_value_a_window_reads_from_settings_is_handed_on_as_it_was_given():
    """Listed from the code: every key ``windows_from`` reads of a window. None is converted there, by ``float``, ``str`` or
    anything else but the reading of a text as a moment: the window reads each by the one rule for its kind."""
    tree = _TREES[pacing.__name__]
    function = next(f for f in _functions(tree) if f.name == "windows_from")
    read = {node.slice.value for node in ast.walk(function) if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name) and node.value.id == "row" and isinstance(node.slice, ast.Constant)}
    read |= {node.args[0].value for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
             and node.func.value.id == "row"}
    assert read == set(_SETTINGS), read
    called = {node.func.id for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert called <= {"isinstance", "ValueError", "tuple", "ProviderWindow", "utc", "exact_text", "windows_by_provider"}, called
    (window,) = windows_from({"windows": [_SETTINGS]})
    assert (window.provider, window.allowance, window.reserve_share, window.estimated) == ("codex", 100.0, 0.25, False)
    assert window.week_start == START and type(window.allowance) is float


@pytest.mark.parametrize("key", ["allowance", "reserve_share"])
@pytest.mark.parametrize("bad", [True, False, "100", "0.2", None, float("nan"), float("inf"), float("-inf"), -1, 10 ** 400,
                                 _LyingFloat(0.5), [1], {"n": 1}],
                         ids=["true", "false", "text", "text share", "null", "nan", "inf", "-inf", "negative", "huge",
                              "lying float", "list", "object"])
def test_a_setting_that_is_not_a_plain_number_in_range_is_refused(key, bad):
    """Finding 7: ``float()`` took the allowance before the one rule saw it, so a number of a caller's class whose own
    conversion said 1,000 where it holds 100 became an allowance of 1,000; a text and a flag became numbers the same way."""
    with pytest.raises(ValueError):
        windows_from({"windows": [{**_SETTINGS, key: bad}]})


@pytest.mark.parametrize("key, bad", [("provider", 7), ("provider", None), ("provider", ""), ("provider", _Lying("codex")),
                                      ("week_start", 7), ("week_start", None), ("week_start", "2026-11-01T00:00:00"),
                                      ("week_start", _Lying(START.isoformat()))])
def test_a_setting_that_is_not_the_text_it_should_be_is_refused(key, bad):
    with pytest.raises(ValueError):
        windows_from({"windows": [{**_SETTINGS, key: bad}]})


def test_a_sum_is_compared_with_its_limit_in_one_place():
    """Listed from the code: the slack is read in ``gates.over`` alone, both admission paths call it (the operator's stop
    check and the meter's weekly ceiling), and no other comparison in either module orders anything against a cap or a
    ceiling."""
    slack = {(name, _inside(tree, node)) for name, tree in _TREES.items() for node in ast.walk(tree)
             if isinstance(node, ast.Name) and node.id == "_SLACK" and isinstance(node.ctx, ast.Load)}
    assert slack == {("g6.operator.gates", "over")}, slack
    calls = {(name, _inside(tree, node)) for name, tree in _TREES.items() for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "over"}
    assert calls == {("g6.operator.gates", "stop_reason"), ("g6.operator.gates", "spend_refusal"),
                     ("g6.self_maintenance.pacing", "decide")}, calls
    loose = []
    for name, tree in _TREES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and any(isinstance(op, ast.Gt | ast.GtE | ast.Lt | ast.LtE) for op in node.ops):
                sides = {part.id if isinstance(part, ast.Name) else part.attr for side in (node.left, *node.comparators)
                         for part in ast.walk(side) if isinstance(part, ast.Name | ast.Attribute)}
                if sides & {"cap", "ceiling", "allowance"} and _inside(tree, node) != "over":
                    loose.append(f"{name}.{_inside(tree, node)}:{node.lineno}: {ast.unparse(node)}")
    assert not loose, loose


#: Every ordering comparison in the four modules, as (module, function, text), in three kinds: of a sum with its limit, which
#: is the one place; of a number the one rule has already made plain, with a constant, another such number or a length the
#: same rule read; and of instants and counts, which are no amounts. A new one fails here until it is put where it belongs.
_WITH_A_LIMIT = {("g6.operator.gates", "over", "total - cap <= _SLACK * cap")}
_OF_PLAIN_NUMBERS = {
    ("g6.operator.gates", "spend_refusal", "value < 0"), ("g6.operator.gates", "amount_of", "number > 0"),
    ("g6.operator.gates", "cap_of", "number >= 0"), ("g6.operator.store", "stored_amount", "number < 0"),
    ("g6.operator.store", "settle", "metered < 0"), ("g6.operator.store", "_settle", "reported_value > charged"),
    ("g6.self_maintenance.pacing", "_used", "figure > charged"),
    ("g6.self_maintenance.pacing", "figure_of", "0 <= number <= most"),
    ("g6.self_maintenance.pacing", "__post_init__", "0 <= share < 1"),
    ("g6.self_maintenance.usage", "limits_reading", "w['usedPercent'] >= FULL"),
    ("g6.self_maintenance.usage", "limit_ends", "seconds > 0"),
    ("g6.self_maintenance.usage", "_reset", "abs((at - now).total_seconds()) <= longest"),
}
_OF_INSTANTS_AND_COUNTS = {
    ("g6.operator.store", "in_transaction", "getattr(self._held, 'depth', 0) > 0"),
    ("g6.operator.store", "_date_old_charge", "settled >= start"), ("g6.operator.store", "keep_grid", "read_at <= kept_at"),
    ("g6.operator.store", "reserve_span", "span[0] <= span[1] < span[2]"),
    ("g6.operator.store", "reserve_span", "span[1] + 1 < recorded"),
    ("g6.operator.store", "reserve_span", "_us(self.store.clock()) + 1 < recorded"),
    ("g6.operator.store", "keep_reading", "kept >= at"),
    ("g6.operator.store", "keep_reading", "kept < at"),
    ("g6.self_maintenance.pacing", "ceiling", "day >= 7"),
    ("g6.self_maintenance.pacing", "standing", "ends > now"),
    ("g6.self_maintenance.pacing", "standing", "reading.limited_until > now"),
    ("g6.self_maintenance.pacing", "_look", "now < wait"), ("g6.self_maintenance.pacing", "_used", "h.settled >= read_at"),
    ("g6.self_maintenance.usage", "limits_reading", "at > now"),
    ("g6.self_maintenance.usage", "limits_reading", "reset <= now"),
    ("g6.self_maintenance.usage", "refresh", "now - self._asked < REFRESH"),
    ("g6.self_maintenance.usage", "_no_reading", "kept.limited_until > now"),
    # positions and lengths in a host program's arguments, a path's buffer and a reply's hidden inputs (the refusals
    # follow-up): counts, no amounts
    ("g6.self_maintenance.usage", "_check_observation", "argv.index(option) >= end"),
    ("g6.self_maintenance.usage", "_check_observation", "at + 1 >= end"),
    ("g6.self_maintenance.usage", "_final_path", "size >= len(buffer)"),
    ("g6.self_maintenance.usage", "_received", "len(hide) > 1"),
}


def test_every_ordering_comparison_is_listed_and_one_is_with_a_limit():
    listed = _WITH_A_LIMIT | _OF_PLAIN_NUMBERS | _OF_INSTANTS_AND_COUNTS
    found = {(name, _inside(tree, node), ast.unparse(node)) for name, tree in _TREES.items() for node in ast.walk(tree)
             if isinstance(node, ast.Compare) and any(isinstance(op, ast.Gt | ast.GtE | ast.Lt | ast.LtE) for op in node.ops)}
    assert found == listed, sorted(found ^ listed)


def test_the_number_rules_at_their_edges():
    """Each rule of ``_OF_PLAIN_NUMBERS`` that reads a value given to it, at zero, the smallest and the largest number a
    float holds, either side of its limit, and what is no number."""
    most, least = sys.float_info.max, 5e-324
    assert [gates.amount_of(v) for v in (0, 0.0, -0.0, least, 1, most, -least)] == [None, None, None, least, 1.0, most, None]
    assert [gates.cap_of({"model": v}, "model") for v in (0, -0.0, least, most, -least)] == [0.0, 0.0, least, most, None]
    assert [pacing.figure_of(v, 100) for v in (0, least, 100, 100.00000000000001, -least)] == [0.0, least, 100.0, None, None]
    assert [store_module.stored_amount(v) for v in (0, least, most)] == [0.0, least, most]
    for share, taken in ((0, True), (least, True), (0.9999999999999999, True), (1, False), (1.0000000000000002, False),
                         (-least, False)):
        if taken:
            assert ProviderWindow("codex", 1, START, reserve_share=share).reserve_share == share
        else:
            with pytest.raises(ValueError, match="reserve_share"):
                ProviderWindow("codex", 1, START, reserve_share=share)
    for bad in (True, False, math.nan, math.inf, -math.inf, 10 ** 400, "1", b"1", None, _LyingFloat(1)):
        assert gates.amount_of(bad) is None and gates.cap_of({"model": bad}, "model") is None and pacing.figure_of(bad) is None
        with pytest.raises(ValueError):
            store_module.stored_amount(bad)
        with pytest.raises(ValueError):
            ProviderWindow("codex", 1, START, reserve_share=bad)
    with pytest.raises(ValueError):
        store_module.stored_amount(-least)


def test_the_one_comparison_at_its_edges():
    over = gates.over
    assert not over(0, 0) and not over(0.0, 5) and not over(5, 5)
    assert over(5e-324, 0) and over(1e-300, 0)                          # a cap of zero has no slack
    assert not over(0.1 + 0.2, 0.3) and 0.1 + 0.2 > 0.3                 # a float sum that meets its cap is not over it
    assert over(1e-9, 1e-12)                                            # finding 10: 1,000 weekly allowances
    for scale in (1e-300, 1e-12, 1e-6, 1.0, 1e6, 1e12, 1e300):
        assert not over(scale, scale) and not over(scale * (0.1 + 0.2), scale * 0.3)
        assert over(scale * (1 + 1e-6), scale) and over(scale * 1.01, scale) and not over(scale * 0.99, scale)
    assert over(1.79e308, 1.7e308)                                      # no slack overflows into room
    for total in (math.nan, math.inf, 1e308 * 10):
        assert over(total, 100)                                         # what does not compare is over
    assert over(5, math.nan)


_SCALES = [1e-12, 1e-6, 1.0, 1e6, 1e12, 1e300]


@pytest.mark.parametrize("scale", _SCALES)
def test_the_weekly_ceiling_holds_at_every_scale(scale):
    """Finding 10: the slack was 1e-9 whatever the allowance, so an allowance of 1e-12 admitted 1e-9."""
    for worst, admitted in ((scale, True), (scale * (1 + 1e-6), False), (scale * 1000, False), (scale * 0.999, True)):
        store = OperatorStore(":memory:", clock=lambda: LAST_DAY)
        try:
            assert _meter(store, scale).reserve("codex", LAST_DAY, worst).admitted is admitted, (scale, worst)
        finally:
            store.close()
    store = OperatorStore(":memory:", clock=lambda: LAST_DAY)
    try:
        meter = _meter(store, scale * 0.3)                                # 0.1 + 0.2 of it meets it, and is not over it
        assert meter.reserve("codex", LAST_DAY, scale * 0.1).admitted and meter.reserve("codex", LAST_DAY, scale * 0.2).admitted
        assert not meter.reserve("codex", LAST_DAY, scale * 1e-6).admitted
    finally:
        store.close()


@pytest.mark.parametrize("scale", _SCALES)
def test_the_operators_cap_holds_at_every_scale(scale):
    store = OperatorStore(":memory:")
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": scale * 0.3, "cash": 0})["venture_id"]
        for amount in (scale * 1e-300 or 5e-324, scale * 1e-12):
            with pytest.raises(BudgetRefused, match="budget_exhausted"):
                scope.reserve(venture, month="2026-11", account="cash", amount=amount)        # a cap of zero admits nothing
        with pytest.raises(BudgetRefused, match="budget_exhausted"):
            scope.reserve(venture, month="2026-11", account="model", amount=scale * 0.3 * (1 + 1e-6))
        scope.reserve(venture, month="2026-11", account="model", amount=scale * 0.1)
        scope.reserve(venture, month="2026-11", account="model", amount=scale * 0.2)
        with pytest.raises(BudgetRefused, match="budget_exhausted"):
            scope.reserve(venture, month="2026-11", account="model", amount=scale * 1e-6)
    finally:
        store.close()


@pytest.mark.parametrize("bad", [True, "5", b"5", None, float("nan"), float("inf"), _LyingFloat(5), [5], 10 ** 400],
                         ids=["flag", "text", "bytes", "none", "nan", "inf", "lying float", "list", "huge"])
def test_the_stop_check_reads_what_is_committed_and_what_is_asked_by_the_one_rule(bad):
    venture = {"status": "active", "caps": {"model": 100, "cash": 100}}
    assert gates.spend_refusal(venture, {"model": 5}, "model", 5) is None and gates.stop_reason(venture, {}) is None
    assert gates.stop_reason(venture, {"model": bad}), "a sum that is no number stopped nothing"
    assert gates.spend_refusal(venture, {"model": bad}, "model", 1)
    assert gates.spend_refusal(venture, {"model": 5}, "model", bad)
    assert gates.spend_refusal(venture, {"model": 5}, "model", -1)

    class Unreadable(dict):
        def get(self, *_):
            raise RuntimeError("cannot be read")

    assert gates.stop_reason(venture, Unreadable()) and gates.spend_refusal(venture, Unreadable(), "model", 1)


def test_the_ledger_writes_an_amount_by_the_rule_it_is_read_back_by():
    store = OperatorStore(":memory:")
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 100, "cash": 0})["venture_id"]
        for bad in (True, "5", None, float("nan"), float("inf"), -1, _LyingFloat(5)):
            with pytest.raises(ValueError, match="amount"):
                with scope.immediate():
                    scope._append_ledger(venture, "2026-11", "model", "charge", bad, "ref")
        assert scope.committed(venture, "2026-11") == {}
    finally:
        store.close()


def test_only_false_itself_recovers_reservations_no_tick_made():
    """``int()`` of the flag decided which reservations a recovery ends: a flag that is true and counts as zero ended those
    made outside a tick, whose makers may still be spending."""
    class Sly:
        def __bool__(self):
            return True

        def __int__(self):
            return 0

        __index__ = __int__

    store = OperatorStore(":memory:", clock=lambda: NOW)
    try:
        meter = _meter(store)
        assert meter.reserve("codex", NOW, 10).admitted and meter.reserve("codex", NOW, 20, tick="gone").admitted
        assert meter.scope.settle_open("codex", "model", made_by_a_tick=Sly()) == 20
        assert meter.reading("codex", NOW).used == 30
        assert meter.scope.settle_open("codex", "model", made_by_a_tick=False) == 10
    finally:
        store.close()


# The one rule for a stamp, for a charge found with no span too (finding 11) --------------------------------------------------

def test_a_charge_adopted_after_a_kept_reading_is_never_dated_inside_it(monkeypatch):
    """Finding 11: the operator's own ``reserve`` writes a reservation with no span, and admission reads it into one when it
    next looks. Its charge was dated from the ledger's text alone, so with the clock set back it was dated before a reading
    the store had already kept, and that reading's figure swallowed it."""
    now = [START + timedelta(days=6, hours=12)]
    monkeypatch.setattr(store_module, "_now", lambda: now[0].isoformat(timespec="seconds"))
    store = OperatorStore(":memory:", clock=lambda: now[0])
    try:
        reading = AccountReading(used=60, estimated=False, read_at=now[0], resets_at=START + WEEK)
        meter = _meter(store, read_account=lambda *_: reading)
        first = meter.reserve("codex", now[0], 10)
        meter.settle(first, 10)
        now[0] -= timedelta(minutes=1)
        late = meter.scope.reserve(first.venture, month=START.isoformat(), account="model", amount=20)
        assert meter.scope.settle(first.venture, late) == 20
        assert meter.reading("codex", now[0]).used == 90
        assert not meter.reserve("codex", now[0], 30).admitted
        stamps = [row[0] for row in store.conn.execute("SELECT at_us FROM week_settled ORDER BY rowid")]
        assert stamps == sorted(stamps) and all(store_module._moment(at) > reading.read_at for at in stamps)
    finally:
        store.close()


def test_a_charge_adopted_with_nothing_kept_is_dated_from_the_ledger(monkeypatch):
    """The control: with no reading kept and no settlement stamped, the ledger's own time, a second on, is the date."""
    monkeypatch.setattr(store_module, "_now", lambda: NOW.isoformat(timespec="seconds"))
    store = OperatorStore(":memory:", clock=lambda: NOW)
    try:
        scope = store.for_owner(OWNER)
        venture = scope.venture_named(f"codex@{START.isoformat()}", caps={"model": 100, "cash": 0})
        held = scope.reserve(venture, month=START.isoformat(), account="model", amount=20)
        scope.settle(venture, held)
        written = store.conn.execute("SELECT at FROM ledger WHERE kind = 'charge'").fetchone()[0]
        assert scope.adopt_books(("codex",)) == {}
        (stamp,) = (row[0] for row in store.conn.execute("SELECT at_us FROM week_settled"))
        assert store_module._moment(stamp) == datetime.fromisoformat(written) + timedelta(seconds=1)
    finally:
        store.close()


def _called_name(node) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""


def test_every_sum_of_amounts_is_the_one_sum():
    """Review wksfu3's suspected item: the month's sum was a running sum of floats, which stops moving once it is large, so
    at a met cap of 2**60 every further 1.0 was admitted and the sum never showed it. Review wksfu4's finding 4: summed by
    ``math.fsum`` where each sum is made, a sum past the largest float was an ``OverflowError`` out of every later look,
    where it had been an infinity and a refusal. ``gates.total`` is the one sum: exact, and an infinity on overflow.

    Listed from the code, in the four modules, every way amounts could be added up: a call of ``sum``, ``fsum``,
    ``reduce``, ``accumulate`` or ``prod`` by any spelling; an augmented assignment; an assignment that adds to what it
    assigns. The only ``fsum`` is inside ``total``; ``total`` is called where amounts are summed and nowhere is one summed
    another way; the augmented assignments left join text, bytes or names."""
    adders, running = set(), set()
    for name, tree in _TREES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in ("sum", "fsum", "reduce", "accumulate", "prod", "total"):
                adders.add((name, _inside(tree, node), _called_name(node)))
            if isinstance(node, ast.AugAssign):
                running.add((name, _inside(tree, node), ast.unparse(node.target)))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add | ast.Sub):
                target = ast.unparse(node.targets[0])
                if any(ast.unparse(part) == target for part in ast.walk(node.value)):
                    running.add((name, _inside(tree, node), target))
    assert adders == {("g6.operator.gates", "total", "fsum"), ("g6.operator.store", "week_held", "total"),
                      ("g6.operator.store", "settle_open", "total"), ("g6.operator.store", "_month", "total"),
                      ("g6.self_maintenance.pacing", "_used", "total")}, adders
    # The rest join text, bytes or a set of names, never an amount: the store's SQL and a provider's words, and from the
    # refusals follow-up a framed byte string, a path's name and the set of a host class's methods.
    assert running == {("g6.operator.store", "evidence", "sql"), ("g6.self_maintenance.usage", "refresh", "self.said[provider]"),
                       ("g6.self_maintenance.usage", "_text_bytes", "framed"),
                       ("g6.self_maintenance.usage", "_final_path", "name"),
                       ("g6.self_maintenance.usage", "_check_seam", "methods")}, running
    assert gates.total([0.1, 0.2, 0.3]) == 0.6 and gates.total([]) == 0.0 and gates.total(iter([2.0 ** 60, 1.0] + [1.0] * 299)) \
        == 2.0 ** 60 + 256
    assert gates.total([1e308, 1e308]) == math.inf and gates.total([1e308, 1e308, -1e308]) == math.inf
    assert math.isnan(gates.total([math.nan, 1.0])) and gates.over(gates.total([1e308, 1e308]), 1.7e308)
    store = OperatorStore(":memory:")
    try:
        scope = store.for_owner("ada")
        cap = float(2 ** 60)
        venture = scope.create_venture("v", caps={"model": cap, "cash": 0})["venture_id"]
        scope.reserve(venture, month="m", account="model", amount=cap)
        for _ in range(300):
            scope.reserve(venture, month="m", account="model", amount=1.0)
        assert scope.committed(venture, "m")["model"] == cap + 256          # 300 more, to the nearest float: it has moved
    finally:
        store.close()


# 4. Readings: the store is told of every reading before it stamps, and admits nothing behind what it has recorded ------------

def test_a_reading_reaches_the_store_in_one_place_and_a_decision_is_given_one_reading():
    """Listed from the code, the one way a reading flows. ``BudgetMeter._account`` is the one reader of ``read_account``
    (an inventory of ``test_week_boundaries``). ``BudgetMeter.tell`` is the one caller of ``keep_reading``, which alone
    writes ``week_readings``; ``tell`` is called where a reading is given (``_account``) and where the loop's reader takes
    one (``AccountReader._told``, before the reading is anyone's to use). The store asks before a stamp (``_hear``, which
    asks nobody inside a look) and asks nobody for a reservation (``ask=False``); the two places its own times are written
    from a clock, a settlement's stamp and a reservation's start, both read ``_kept_times``. Review wksfu3's finding 1 was
    the floor reading the kept grid alone; review wksfu4's findings 2 and 5 were a reading told to the store only when its
    holder was next asked, and a reservation asking a second time after it had decided."""
    callers = {(name, _inside(tree, node)) for name, tree in _TREES.items() for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "keep_reading"}
    assert callers == {("g6.self_maintenance.pacing", "tell")}, callers
    tellers = {(name, _inside(tree, node)) for name, tree in _TREES.items() for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "tell"}
    assert tellers == {("g6.self_maintenance.pacing", "_account"), ("g6.self_maintenance.usage", "_told")}, tellers
    usage_functions = {f.name: ast.unparse(f) for f in _functions(_TREES[usage.__name__])}
    refresh = usage_functions["refresh"]
    assert refresh.index("self._told(provider, taken)") < refresh.index("self.readings[provider] = taken")
    givers = {(name, _inside(tree, node)) for name, tree in _TREES.items() for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_account"}
    assert givers == {("g6.self_maintenance.pacing", "_look"), ("g6.self_maintenance.pacing", "next_room"),
                      ("g6.self_maintenance.pacing", "_hear")}, givers
    store_tree = _TREES[store_module.__name__]
    writes = {_inside(store_tree, node) for node in ast.walk(store_tree) if isinstance(node, ast.Constant)
              and isinstance(node.value, str) and "INTO week_readings" in node.value}
    assert writes == {"keep_reading"}, writes
    functions = {f.name: ast.unparse(f) for f in _functions(store_tree)}
    asks, reads = functions["_kept_times"].index("self.ask_reader("), functions["_kept_times"].index("self._read_time(")
    assert asks < reads
    floored = sorted(name for name, text in functions.items() if "self._kept_times(" in text)
    assert floored == ["_stamp", "reserve_span"], floored
    assert "ask=False" in functions["reserve_span"] and "ask=False" not in functions["_stamp"]
    meter = next(f for f in _functions(_TREES[pacing.__name__]) if f.name == "__init__"
                 and "ask_reader" in ast.unparse(f))
    assert "self.scope.ask_reader = self._hear" in ast.unparse(meter)
    look = ast.unparse(next(f for f in _functions(_TREES[pacing.__name__]) if f.name == "_look"))
    assert look.count("self._account(") == 1
    assert look.index("self._account(") < look.index("self._looking.add(") < look.index("adopt_books(")


class _Holder:
    """A reader of a caller's own: it hands back the reading it was last told to hold."""

    def __init__(self) -> None:
        self.held = None

    def take(self, used, at, reset) -> None:
        self.held = AccountReading(used=used, estimated=False, read_at=at, resets_at=reset)

    def __call__(self, provider, now):
        return self.held


class _Lambda(_Holder):
    """Another caller's: a plain function over a value, as most callers give."""

    @property
    def reader(self):
        return lambda provider, now: self.held


class _Tool:
    """The loop's own: an ``AccountReader`` over a tool's replies, read as a tick reads it (``read_now``)."""

    def __init__(self) -> None:
        self.reply = None
        self.reader = AccountReader((ProviderWindow("codex", 100, START, reserve_share=0),), ask={"codex": lambda: self.reply})

    def take(self, used, at, reset) -> None:
        week = {"usedPercent": used, "windowDurationMins": WEEK_MINUTES}
        if reset is not None:
            week["resetsAt"] = reset.timestamp()
        self.reply = {"rateLimits": {"primary": week}, "ordinaryUsageAllowed": True}
        self.reader.read_now(at)


def _reader(kind: str):
    made = {"a function": _Lambda, "a reader of its own": _Holder, "the loop's reader": _Tool}[kind]()
    return made, getattr(made, "reader", made)


_READERS = ["a function", "a reader of its own", "the loop's reader"]
_RESETS = {"with a reset": START + WEEK, "with no reset": None}
_SETTLED = ["meter.settle", "scope.settle", "recover", "settle_open", "found with no span"]


def _charge(meter, how: str, clock, monkeypatch):
    """Reserve 30 one way, and give the way to settle it at 30."""
    scope = meter.scope
    if how == "found with no span":                    # as the operator's own ``reserve`` still writes one
        monkeypatch.setattr(store_module, "_now", lambda: clock[0].isoformat(timespec="seconds"))
        venture = scope.venture_named(f"codex@{START.isoformat()}", caps={"model": 100, "cash": 0})
        held = scope.reserve(venture, month=START.isoformat(), account="model", amount=30)
        return lambda: scope.settle(venture, held)
    spend = meter.reserve("codex", clock[0], 30, tick="gone")
    assert spend.admitted
    return {"meter.settle": lambda: meter.settle(spend, 30),
            "scope.settle": lambda: scope.settle(spend.venture, spend.reservation, metered=30),
            "recover": lambda: meter.recover("codex"), "settle_open": lambda: meter.settle_open("codex")}[how]


@pytest.mark.parametrize("how", _SETTLED)
@pytest.mark.parametrize("order", ["in the reading's own instant", "with the clock set back"])
@pytest.mark.parametrize("looked", [False, True], ids=["not looked at", "looked at"])
@pytest.mark.parametrize("reset", _RESETS)
@pytest.mark.parametrize("kind", _READERS)
def test_a_charge_written_after_a_reading_was_taken_goes_on_top_of_it(kind, reset, looked, order, how, monkeypatch):
    """Every way a reading reaches the meter (a function, a reader of a caller's own, the loop's reader; naming a reset or
    not; looked at before the charge is written or not) against every way a charge is written after it (settled through
    the meter or straight on its scope, recovered, settled with all that is open, found with no span and dated when it is
    read in), in the reading's own instant and with the clock set back before it. 60 was read while 30 was still open, so
    the charge of 30 is not in the 60: the week stands at 90, and 20 more is refused."""
    clock = [LAST_DAY]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    try:
        holder, reader = _reader(kind)
        holder.take(0, clock[0], _RESETS[reset])
        meter = _meter(store, read_account=reader)
        settle = _charge(meter, how, clock, monkeypatch)
        clock[0] += timedelta(minutes=2)
        taken = clock[0]
        holder.take(60, taken, _RESETS[reset])                         # read while the 30 is still open
        if looked:
            assert meter.reading("codex", clock[0]).used == 90
        if order == "with the clock set back":
            clock[0] -= timedelta(minutes=1)
        assert settle() == 30
        clock[0] = taken + timedelta(seconds=1)
        extra = meter.reserve("codex", clock[0], 20)
        assert not extra.admitted and extra.used == 90, (extra.used, extra.reason)
        assert all(store_module._moment(at) > taken for (at,) in store.conn.execute("SELECT at_us FROM week_settled"))
    finally:
        store.close()


@pytest.mark.parametrize("how", _SETTLED[:4])
@pytest.mark.parametrize("reset", _RESETS)
@pytest.mark.parametrize("kind", _READERS)
def test_a_charge_settled_before_a_reading_was_taken_may_be_in_it(kind, reset, how, monkeypatch):
    """The control, in the honest order: the 30 is settled, and the reading of 60 is taken a minute later. It may hold the
    30, so the week stands at 60, and 20 more is admitted."""
    clock = [LAST_DAY]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    try:
        holder, reader = _reader(kind)
        holder.take(0, clock[0], _RESETS[reset])
        meter = _meter(store, read_account=reader)
        settle = _charge(meter, how, clock, monkeypatch)
        clock[0] += timedelta(minutes=2)
        assert settle() == 30
        clock[0] += timedelta(minutes=1)
        holder.take(60, clock[0], _RESETS[reset])
        extra = meter.reserve("codex", clock[0], 20)
        assert extra.admitted and extra.used == 60, (extra.used, extra.reason)
    finally:
        store.close()


def test_the_newest_reading_time_only_moves_forward_and_none_records_nothing():
    store = OperatorStore(":memory:", clock=lambda: NOW)
    try:
        scope = store.for_owner(OWNER)
        scope.keep_reading("codex", None)
        assert scope._read_time(OWNER, "codex") is None
        for at, kept in ((NOW, NOW), (NOW - timedelta(hours=1), NOW), (NOW + timedelta(microseconds=1), NOW + timedelta(microseconds=1))):
            scope.keep_reading("codex", at)
            assert store_module._moment(scope._read_time(OWNER, "codex")) == kept
        assert scope._read_time(OWNER, "claude") is None and scope._read_time("another", "codex") is None
        with pytest.raises(ValueError):
            scope.keep_reading("codex", datetime(2026, 11, 1))               # a time with no zone is no instant
    finally:
        store.close()


def test_an_open_reservation_counts_in_whatever_week_the_clock_says():
    """Review wksfu3, finding 3: with the clock set back over a week's end, a reservation open in the week the store is
    really in started after the end of the week being read, and was left out."""
    clock = [START + WEEK + timedelta(minutes=30)]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    try:
        meter = _meter(store)
        first = meter.reserve("codex", clock[0], 20)
        clock[0] -= timedelta(hours=1)
        assert meter.reading("codex", clock[0]).used == 20
        assert not meter.reserve("codex", clock[0], 90).admitted           # 20 open and 90 more is over the 100
        clock[0] += timedelta(hours=1)
        assert meter.settle(first, 5) == 5
        clock[0] -= timedelta(hours=1)
        assert meter.reading("codex", clock[0]).used == 0                  # settled in the second week, it is not in the first
    finally:
        store.close()


@pytest.mark.parametrize("recorded", ["a settlement", "a reading with a reset", "a reading with no reset"])
def test_nothing_is_reserved_while_the_clock_reads_earlier_than_what_the_store_has_recorded(recorded):
    """Review wksfu3, finding 3: a stamp never went backwards, and the week an admission is checked in was the caller's
    ``now`` alone. A reservation takes the floors a stamp takes: behind a settlement's stamp or a reading's time it is
    refused, in the same instant it is not, and one microsecond behind is the store's own doing and is not behind."""
    clock = [START + WEEK + timedelta(hours=2)]
    readings = [None]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    try:
        meter = _meter(store, read_account=lambda *_: readings[0])
        if recorded == "a settlement":
            assert meter.settle(meter.reserve("codex", clock[0], 10), 10) == 10
        else:
            readings[0] = AccountReading(used=10, estimated=False, read_at=clock[0],
                                         resets_at=START + 2 * WEEK if recorded == "a reading with a reset" else None)
            assert meter.reading("codex", clock[0]).used == 10
        mark = clock[0]
        for back in (timedelta(hours=3), timedelta(seconds=1), timedelta(microseconds=2)):
            clock[0] = mark - back
            refused = meter.reserve("codex", clock[0], 1)
            assert not refused.admitted and "the clock reads earlier" in refused.reason, (back, refused.reason)
        for back in (timedelta(microseconds=1), timedelta(0)):
            clock[0] = mark - back
            assert meter.reserve("codex", clock[0], 1).admitted, back
        assert meter.reading("codex", mark).used == 12
    finally:
        store.close()


def test_a_time_a_caller_gives_is_taken_when_the_stores_own_clock_is_not_behind():
    """The edge of the rule above, as it stands: nothing is reserved while the clock is behind the book, and a store and a
    caller on two clocks are not one clock set back. Here the store runs on a clock a week ahead of the times its caller
    gives, as an earlier review's probe does with the wall clock; the caller's time is taken, as it always was, and the
    week's ceiling holds."""
    ahead = START + 2 * WEEK
    store = OperatorStore(":memory:", clock=lambda: ahead)
    try:
        meter = _meter(store)
        day_five = START + timedelta(days=4)
        held = meter.reserve("codex", day_five, 100)
        assert held.admitted and not meter.reserve("codex", day_five, 1).admitted
        assert meter.settle(held, 99) == 99                              # stamped by the store's clock, a week ahead
        assert meter.reserve("codex", day_five, 1).admitted and not meter.reserve("codex", day_five, 1).admitted
    finally:
        store.close()


# 5. The week: only a reset the provider gave moves the kept grid, and the store's is the one copy ----------------------------

def _reply(percent, resets=None, minutes=WEEK_MINUTES):
    week = {"usedPercent": percent, "windowDurationMins": minutes}
    if resets is not None:
        week["resetsAt"] = resets.timestamp()
    return {"rateLimits": {"primary": week}, "ordinaryUsageAllowed": True}


def test_the_reader_keeps_no_week_and_makes_up_no_reset():
    """Listed from the code: nothing in ``usage`` asks a window when its week ends or moves a window to another week. The
    reader made up a reset from its own copy of the week when a reply named none, and the meter took it for the provider's
    word; after a restart that copy was the settings' week again (review wksfu3, finding 2)."""
    tree = _TREES[usage.__name__]
    asked = {f"{_inside(tree, node)}:{node.func.attr}" for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr in ("week_end", "aligned", "place", "next_day")}
    assert not asked, asked
    window = ProviderWindow("codex", 100, START, reserve_share=0)
    now = START + timedelta(days=4, hours=12)
    for reply in (_reply(10), _reply(10, START + timedelta(days=30)), _reply(10, now - timedelta(days=30))):
        reading = usage.limits_reading(reply, window, now)
        assert reading is not None and reading.resets_at is None
    assert usage.limits_reading(_reply(10, START + WEEK), window, now).resets_at == START + WEEK


@pytest.mark.parametrize("second", ["no reset", "a reset past its window"])
def test_after_a_restart_a_reply_with_no_reset_of_its_own_leaves_the_providers_week(tmp_path, second):
    now, given = START + timedelta(days=4, hours=12), START + timedelta(days=10)       # the provider's week: its day 2
    windows = (ProviderWindow("codex", 100, START, reserve_share=0),)
    path = tmp_path / "op.db"
    store = OperatorStore(path, clock=lambda: now)
    try:
        reader = AccountReader(windows, ask={"codex": lambda: _reply(10, given)})
        meter = BudgetMeter(windows, store=store, read_account=reader)
        reader.refresh(now)
        assert (meter.reading("codex", now).day, meter.scope.week_grid("codex")) == (2, given)
    finally:
        store.close()
    later = now + timedelta(hours=1)
    store = OperatorStore(path, clock=lambda: later)
    try:
        reply = _reply(10) if second == "no reset" else _reply(10, START + timedelta(days=40))
        reader = AccountReader(windows, ask={"codex": lambda: reply})
        meter = BudgetMeter(windows, store=store, read_account=reader)
        reader.refresh(later)
        spend = meter.reserve("codex", later, 80)
        assert not spend.admitted and (spend.day, spend.ceiling, spend.used) == (2, 40.0, 10.0)
        assert meter.scope.week_grid("codex") == given
        gone = meter.reserve("codex", given + timedelta(minutes=1), 1)       # the figure is of the week the store's grid says
        assert not gone.admitted and "not known" in gone.reason
    finally:
        store.close()


# 6. Windows: one provider, one window -----------------------------------------------------------------------------------------

def test_a_list_of_windows_becomes_a_mapping_in_one_place_which_refuses_a_provider_named_twice():
    """Listed from the code: every mapping keyed by a window's provider, anywhere in the package, is made by
    ``windows_by_provider``. The meter and the reader each built ``{w.provider: w}``, so of two windows for one provider the
    later replaced the earlier in silence: 100 then 1000 ran on 1000 (review wksfu3, finding 4)."""
    package = Path(pacing.__file__).resolve().parents[1]
    loose = []
    for path in sorted(package.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "self_maintenance.pacing" not in text and path.name != "pacing.py":      # only what can hold a window
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            keyed = (isinstance(node, ast.DictComp) and isinstance(node.key, ast.Attribute) and node.key.attr == "provider") or (
                isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.slice, ast.Attribute)
                and node.slice.attr == "provider")
            if keyed and _inside(tree, node) != "windows_by_provider":
                loose.append(f"{path.relative_to(package).as_posix()}:{node.lineno}")
    assert not loose, loose
    low, high = (ProviderWindow("codex", allowance, START, reserve_share=0) for allowance in (100, 1000))
    other = ProviderWindow("claude", 5, START)
    assert list(pacing.windows_by_provider((low, other))) == ["codex", "claude"]
    row = {"provider": "codex", "week_start": START.isoformat(), "reserve_share": 0}
    store = OperatorStore(":memory:")
    try:
        for make in (lambda: pacing.windows_by_provider((low, other, high)), lambda: BudgetMeter((low, high), store=store),
                     lambda: AccountReader((low, high), ask={}),
                     lambda: windows_from({"windows": [{**row, "allowance": 100}, {**row, "allowance": 1000}]}),
                     lambda: windows_from({"windows": [{**row, "allowance": 100}, {**row, "allowance": 100}]})):
            with pytest.raises(ValueError, match="two windows"):
                make()
    finally:
        store.close()


# 7. A reader starts unread for every provider it asks ---------------------------------------------------------------------------

def test_every_provider_a_reader_asks_is_unread_until_it_is_read():
    """Review wksfu3, finding 5: a read that ended without a reading and without the reader marking it (its error's own
    rendering raised) left the reader holding nothing for the provider, which it handed back as "no reading": the answer
    of a provider whose tool reports nothing, on which the meter admits by its own count. A provider that is asked now
    starts unread, so whatever a read does, the account is not known until one succeeds."""
    class Unprintable(OSError):
        def __str__(self):
            raise RuntimeError("no rendering")

    def escapes():
        raise Unprintable()

    windows = (ProviderWindow("codex", 100, START, reserve_share=0), ProviderWindow("claude", 100, START, reserve_share=0))
    store = OperatorStore(":memory:", clock=lambda: LAST_DAY)
    try:
        reader = AccountReader(windows, ask={"codex": escapes})
        meter = BudgetMeter(windows, store=store, read_account=reader)
        assert reader("codex", LAST_DAY).unread and reader("claude", LAST_DAY) is None
        assert reader.said["claude"] == NOT_REPORTED and "not been read" in reader.said["codex"]
        before = meter.reserve("codex", LAST_DAY, 1)
        assert not before.admitted and "not known" in before.reason          # never read: nothing is reserved on it
        with pytest.raises(RuntimeError, match="no rendering"):
            reader.refresh(LAST_DAY)
        later = LAST_DAY + timedelta(minutes=1)
        reader.refresh(later)
        after = meter.reserve("codex", later, 100)
        assert not after.admitted and "not known" in after.reason
        assert meter.reserve("claude", later, 1).admitted                    # its tool reports nothing: the meter's own count
        reader.ask["codex"] = lambda: _reply(10, START + WEEK)
        reader.read_now(later)
        assert meter.reserve("codex", later, 50).admitted
    finally:
        store.close()


# 8. A reset is believed by one rule in both directions, and a week's end already past never moves a kept week ----------------

GRID_END = datetime(2026, 11, 10, tzinfo=UTC)                 # the provider's own week ends on a Tuesday
DAY_TWO = datetime(2026, 11, 4, 12, tzinfo=UTC)               # day 2 of it: a ceiling of 40 of 100
_HOUR = timedelta(hours=1)
_SECOND = timedelta(seconds=1)
#: A reset across the whole line, as what the tool's reply holds for it (``now`` is the moment of the read), and what the
#: one rule makes of it: ``lost`` (the reply is no reading), ``none`` (the figure, with no week's end named), ``ended`` (a
#: week's end already past: last week's figure is left out) or ``ahead`` (the reset of the week the figure is of).
_LINE = {
    "zero": (lambda now: 0, "none"),
    "five weeks back": (lambda now: (now - 5 * WEEK).timestamp(), "none"),
    "a second past one window back": (lambda now: (now - WEEK - _SECOND).timestamp(), "none"),
    "one window back": (lambda now: (now - WEEK).timestamp(), "ended"),
    "a second inside one window back": (lambda now: (now - WEEK + _SECOND).timestamp(), "ended"),
    "an hour back": (lambda now: (now - _HOUR).timestamp(), "ended"),
    "now": (lambda now: now.timestamp(), "ended"),
    "a second ahead": (lambda now: (now + _SECOND).timestamp(), "ahead"),
    "seven days ahead": (lambda now: (now + WEEK).timestamp(), "ahead"),
    "a second past seven days ahead": (lambda now: (now + WEEK + _SECOND).timestamp(), "none"),
    "far ahead": (lambda now: datetime(2050, 1, 1, tzinfo=UTC).timestamp(), "none"),
    "negative": (lambda now: -1, "lost or none"),
    "past any clock": (lambda now: 1e300, "lost"),
    "not a number": (lambda now: math.nan, "lost"),
    "an infinity": (lambda now: math.inf, "lost"),
    "a negative infinity": (lambda now: -math.inf, "lost"),
    "text": (lambda now: "2026-11-10T00:00:00+00:00", "lost"),
    "a flag": (lambda now: True, "lost"),
    "a list": (lambda now: [1], "lost"),
}


def _week_reply(percent, resets=None):
    week = {"usedPercent": percent, "windowDurationMins": WEEK_MINUTES}
    if resets is not None:
        week["resetsAt"] = resets
    return {"rateLimits": {"primary": week}, "ordinaryUsageAllowed": True}


def _kind(reading) -> str:
    if reading is None:
        return "lost"
    if reading.resets_at is not None:
        return "ahead"
    return "ended" if reading.ended_at is not None else "none"


def test_a_reply_reaches_the_kept_week_through_the_one_rule_for_a_reset():
    """Listed from the code: every place a value of the provider's reply can reach the kept grid, the ceiling's day or a
    figure's lapse. A window's reset is read from the reply in ``_window`` (converted once) and from the converted window
    in ``_reset`` alone, the one rule; ``limits_reading`` takes a reading's ``resets_at`` and ``ended_at`` from that rule's
    answer and nothing else; the store's grid is written in one statement (``keep_grid``), which the meter calls in one
    place (``_look``) and tells whether the moment is a week's end already past. Review wksfu4's finding 1 was a second
    rule for a reset behind now, under which any past value at all was a week's end and moved the kept grid."""
    tree = _TREES[usage.__name__]
    raw = {_inside(tree, node) for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == "resetsAt"}
    assert raw == {"_window", "_reset"}, raw
    assert {_inside(tree, node) for node in ast.walk(tree)
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "resetsAt"
            and isinstance(node.ctx, ast.Load)} == {"_reset"}
    reading = next(f for f in _functions(tree) if f.name == "limits_reading")
    given = {(keyword.arg, ast.unparse(keyword.value)) for node in ast.walk(reading) if isinstance(node, ast.Call)
             and _called_name(node) == "AccountReading" for keyword in node.keywords if keyword.arg in ("resets_at", "ended_at")}
    assert given == {("resets_at", "reset"), ("ended_at", "reset")}, given
    assert [ast.unparse(node.value) for node in ast.walk(reading) if isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]) == "reset"] == ["_reset(week, now)"]
    writes = {(name, _inside(tree_, node)) for name, tree_ in _TREES.items() for node in ast.walk(tree_)
              if isinstance(node, ast.Constant) and isinstance(node.value, str) and "INTO week_grids" in node.value}
    assert writes == {("g6.operator.store", "keep_grid")}, writes
    keepers = {(name, _inside(tree_, node)): ast.unparse(node) for name, tree_ in _TREES.items() for node in ast.walk(tree_)
               if isinstance(node, ast.Call) and _called_name(node) == "keep_grid"}
    assert list(keepers) == [("g6.self_maintenance.pacing", "_look")] and "past=not named" in next(iter(keepers.values()))


@pytest.mark.parametrize("where", _LINE)
def test_the_one_rule_for_a_reset_across_the_whole_line(where):
    now = DAY_TWO
    value, kind = _LINE[where]
    at = value(now)
    reading = usage.limits_reading(_week_reply(60, at), ProviderWindow("codex", 100, START, reserve_share=0), now)
    assert _kind(reading) in kind.split(" or "), (where, reading)
    if reading is None:
        return
    moment = datetime.fromtimestamp(at, UTC) if _kind(reading) in ("ended", "ahead") else None
    assert (reading.resets_at, reading.ended_at) == ((moment, None) if _kind(reading) == "ahead" else (None, moment))
    assert (reading.used, reading.estimated) == ((0.0, True) if _kind(reading) == "ended" else (60.0, False))


def _first_run(path) -> None:
    """The provider says when its week ends, and the store keeps it: day 2, a ceiling of 40, 10 used."""
    store = OperatorStore(path, clock=lambda: DAY_TWO)
    try:
        reader = AccountReader((ProviderWindow("codex", 100, START, reserve_share=0),),
                               ask={"codex": lambda: _week_reply(10, GRID_END.timestamp())})
        meter = _meter(store, read_account=reader)
        reader.refresh(DAY_TWO)
        seen = meter.reading("codex", DAY_TWO)
        assert (seen.day, seen.ceiling, seen.used, meter.scope.week_grid("codex")) == (2, 40.0, 10.0, GRID_END)
    finally:
        store.close()


@pytest.mark.parametrize("where", _LINE)
@pytest.mark.parametrize("when", ["after a restart", "at the first reply of a new week"])
def test_only_a_reset_ahead_and_within_a_week_moves_a_kept_week(tmp_path, when, where):
    """Review wksfu4, finding 1, across the whole line, through the loop's own reader. The store keeps the provider's week.
    Whatever the next reply holds for its reset, the kept week stays where the provider put it unless the reply names a
    reset still ahead and no further than a week, and with the week unmoved nothing is admitted past the provider's day:
    80 on day 2 (ceiling 40) after a restart, 30 on day 1 (ceiling 20) at a new week's first reply."""
    path = tmp_path / "operator.db"
    _first_run(path)
    now, amount, day = (DAY_TWO + _HOUR, 80, 2) if when == "after a restart" else (GRID_END + timedelta(minutes=1), 30, 1)
    at = _LINE[where][0](now)
    store = OperatorStore(path, clock=lambda: now)
    try:
        reader = AccountReader((ProviderWindow("codex", 100, START, reserve_share=0),), ask={"codex": lambda: _week_reply(60, at)})
        meter = _meter(store, read_account=reader)
        reader.refresh(now)
        ahead = _kind(usage.limits_reading(_week_reply(60, at), meter.windows["codex"], now)) == "ahead"
        spend = meter.reserve("codex", now, amount)
        grid = meter.scope.week_grid("codex")
        if ahead:
            assert grid == datetime.fromtimestamp(at, UTC)
            return
        assert grid == GRID_END and spend.day == day and not spend.admitted, (
            f"the kept week ends at {grid}; day {spend.day}, ceiling {spend.ceiling:g}, {spend.used:g} used, "
            f"{spend.amount:g} admitted")
    finally:
        store.close()


@pytest.mark.parametrize("where", _LINE)
def test_a_weeks_end_already_past_says_where_the_weeks_fall_only_while_the_store_keeps_none(tmp_path, where):
    """With no week kept, as in a folder no reply has reached, a week's end already past that can be believed is the only
    word there is of where the provider's weeks fall, and is kept; a reset that cannot be believed is still none."""
    now = DAY_TWO
    at = _LINE[where][0](now)
    store = OperatorStore(tmp_path / "operator.db", clock=lambda: now)
    try:
        reader = AccountReader((ProviderWindow("codex", 100, START, reserve_share=0),), ask={"codex": lambda: _week_reply(60, at)})
        meter = _meter(store, read_account=reader)
        reader.refresh(now)
        kind = _kind(usage.limits_reading(_week_reply(60, at), meter.windows["codex"], now))
        meter.reading("codex", now)
        kept = meter.scope.week_grid("codex")
        assert kept == (datetime.fromtimestamp(at, UTC) if kind in ("ended", "ahead") else None), (where, kind, kept)
        if kind == "ended":                     # and once a week is kept, a later week's end already past does not move it
            later = now + _HOUR
            assert meter.scope.keep_grid("codex", later - timedelta(days=3), later, past=True)[0] == kept
            assert meter.scope.keep_grid("codex", later + timedelta(days=3), later)[0] == later + timedelta(days=3)
    finally:
        store.close()


# 9. A reading flows one way: taken, told to the store, given once to a decision ---------------------------------------------

class _Live:
    """A reader that reads the account as it is asked, and counts how often: each answer is a new reading."""

    def __init__(self, clock, figures=()) -> None:
        self.clock, self.figures, self.said = clock, list(figures), []

    def __call__(self, provider, now):
        self.said.append(self.figures.pop(0) if self.figures else (self.said[-1] if self.said else 0))
        return AccountReading(used=self.said[-1], estimated=False, read_at=self.clock[0], resets_at=START + WEEK)


def test_one_decision_is_given_one_reading_and_the_store_is_told_of_that_one(monkeypatch):
    """Review wksfu4, finding 5: a reserve read the account twice, decided on the first reading and recorded the second.
    Each call that decides or stamps reads the account once: a reserve, a look, a look that finds a charge with no span
    and dates it, a settlement, a schedule."""
    clock = [LAST_DAY]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    try:
        live = _Live(clock, figures=[0, 90])
        meter = _meter(store, read_account=live)
        spend = meter.reserve("codex", clock[0], 50)
        assert spend.admitted and live.said == [0]                 # decided on the one reading it was given
        assert meter.reserve("codex", clock[0], 50).used == 140 and live.said == [0, 90]      # the next one sees the 90
        counted = len(live.said)
        meter.reading("codex", clock[0])
        assert len(live.said) == counted + 1
        monkeypatch.setattr(store_module, "_now", lambda: clock[0].isoformat(timespec="seconds"))
        found = meter.scope.reserve(spend.venture, month=START.isoformat(), account="model", amount=1)
        meter.scope.settle(spend.venture, found)                   # a charge with no span: the next look dates it
        counted = len(live.said)
        meter.reading("codex", clock[0])
        assert len(live.said) == counted + 1 and store.conn.execute("SELECT COUNT(*) FROM week_settled").fetchone()[0] == 1
        counted = len(live.said)
        assert meter.settle(spend, 5) == 5
        assert len(live.said) == counted + 1
        counted = len(live.said)
        meter.next_room(clock[0])
        assert len(live.said) == counted + 1
    finally:
        store.close()


def _both(tmp_path, clock):
    path = tmp_path / "operator.db"
    return OperatorStore(path, clock=lambda: clock[0]), OperatorStore(path, clock=lambda: clock[0])


@pytest.mark.parametrize("reset", _RESETS)
@pytest.mark.parametrize("order", ["in the reading's own instant", "with the clock set back"])
def test_the_loops_reader_tells_the_store_as_it_reads_so_another_meter_stamps_after_it(tmp_path, order, reset):
    """Review wksfu4, finding 2, for the loop's own reader: a reading's time reached the store only when the meter holding it
    was next asked, so a second meter on the same store stamped a charge without knowing of it. The reader now tells the
    store in the step that makes the reading anyone's to use, so the other meter's stamp is after it, in the reading's
    own instant and with the clock set back. 60 was read with 30 open; the other meter settles the 30; 20 more is refused."""
    clock = [LAST_DAY]
    one, two = _both(tmp_path, clock)
    try:
        window = ProviderWindow("codex", 100, START, reserve_share=0)
        replies = [_week_reply(0, None if _RESETS[reset] is None else _RESETS[reset].timestamp())]
        reader = AccountReader((window,), ask={"codex": lambda: replies[-1]})
        first, second = _meter(one), _meter(two, read_account=reader)
        reader.refresh(clock[0])
        spend = first.reserve("codex", clock[0], 30)
        assert spend.admitted
        clock[0] += timedelta(minutes=2)
        taken = clock[0]
        replies.append(_week_reply(60, None if _RESETS[reset] is None else _RESETS[reset].timestamp()))
        reader.read_now(taken)                                      # the 30 is still open; nobody asks the second meter
        assert store_module._moment(two.for_owner(OWNER)._read_time(OWNER, "codex")) == taken
        if order == "with the clock set back":
            clock[0] -= timedelta(minutes=1)
        assert first.settle(spend, 30) == 30
        assert all(store_module._moment(at) > taken for (at,) in one.conn.execute("SELECT at_us FROM week_settled"))
        clock[0] = taken + _SECOND
        extra = second.reserve("codex", clock[0], 20)
        assert not extra.admitted and extra.used == 90, (extra.used, extra.reason)
    finally:
        one.close()
        two.close()


@pytest.mark.parametrize("reset", _RESETS)
def test_a_charge_another_meter_settles_in_a_readings_own_instant_goes_on_top_of_it(tmp_path, reset):
    """Review wksfu4, finding 2, for a reader the store cannot hear until it is asked (a caller's own callback): a stamp
    and a reading's time in one microsecond do not say which came first, so the charge is counted: on top unless settled
    strictly before the reading. With the clock set back between such a reading and another meter's settlement, the store
    has nothing that says the reading was taken first: that case stays a race, as the report says."""
    clock = [LAST_DAY]
    one, two = _both(tmp_path, clock)
    try:
        held = [None]
        first, second = _meter(one), _meter(two, read_account=lambda *_: held[0])
        spend = first.reserve("codex", clock[0], 30)
        clock[0] += timedelta(minutes=2)
        held[0] = AccountReading(used=60, estimated=False, read_at=clock[0], resets_at=_RESETS[reset])
        assert first.settle(spend, 30) == 30                       # in the reading's own instant, by the other meter
        clock[0] += _SECOND
        extra = second.reserve("codex", clock[0], 20)
        assert not extra.admitted and extra.used == 90
    finally:
        one.close()
        two.close()


def test_a_charge_is_on_top_of_a_reading_unless_it_was_settled_strictly_before_it():
    read_at = LAST_DAY
    micro = timedelta(microseconds=1)
    reading = AccountReading(used=60, estimated=False, read_at=read_at)
    for settled, used in ((read_at - micro, 60.0), (read_at, 90.0), (read_at + micro, 90.0), (None, 90.0)):
        assert pacing._used([store_module.Held(30.0, 30.0, settled)], reading) == (used, True), settled
    assert pacing._used([store_module.Held(30.0, 30.0, read_at - micro)], AccountReading(used=60, estimated=False)) == (90.0, True)
    assert pacing._used([store_module.Held(30.0, None, None)], reading) == (90.0, True)           # still open


def test_a_reader_tells_the_store_of_what_it_holds_when_a_meter_binds_it_and_holds_nothing_the_store_refused():
    window = ProviderWindow("codex", 100, START, reserve_share=0)
    store = OperatorStore(":memory:", clock=lambda: LAST_DAY)
    try:
        reader = AccountReader((window,), ask={"codex": lambda: _week_reply(10)})
        reader.refresh(LAST_DAY)                                    # read before any meter is there
        scope = store.for_owner(OWNER)
        assert scope._read_time(OWNER, "codex") is None
        meter = _meter(store, read_account=reader)
        assert store_module._moment(scope._read_time(OWNER, "codex")) == LAST_DAY

        def refuses(provider, read_at):
            raise sqlite3.OperationalError("the store is not to be written")

        meter.tell = refuses
        later = LAST_DAY + _HOUR
        reader.read_now(later)
        assert reader.readings["codex"].read_at == LAST_DAY        # the reading the store was not told of is nobody's to use
        assert "could not be recorded" in reader.said["codex"]
        del meter.tell
        reader.read_now(later)
        assert reader.readings["codex"].read_at == later and store_module._moment(scope._read_time(OWNER, "codex")) == later
    finally:
        store.close()


# 10. Whether a figure still stands is one rule for the meter, its schedule and its reader -----------------------------------

def test_whether_a_figure_stands_is_decided_in_one_place():
    """Listed from the code: ``standing`` is the one rule, and it is called with the week's end the store gives in one
    place (``BudgetMeter.stands``), which the meter's look, its ``next_room`` and a bound reader all ask. The two other
    calls are by a reading's own word alone, and say so: what may move the kept week (``_look``) and what makes the reader
    ask its tool early (``_lapsed``). Review wksfu4's finding 3 was the meter lapsing a figure with no reset by a rule the
    reader and ``next_room`` did not have."""
    calls = {(name, _inside(tree, node)): len(node.args) for name, tree in _TREES.items() for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "standing"}
    assert calls == {("g6.self_maintenance.pacing", "stands"): 3, ("g6.self_maintenance.pacing", "_look"): 2,
                     ("g6.self_maintenance.usage", "_stands"): 2, ("g6.self_maintenance.usage", "_lapsed"): 2}, calls
    askers = {(name, _inside(tree, node)) for name, tree in _TREES.items() for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("stands", "_stands")}
    assert askers == {("g6.self_maintenance.pacing", "_look"), ("g6.self_maintenance.pacing", "next_room"),
                      ("g6.self_maintenance.usage", "_stands"), ("g6.self_maintenance.usage", "refresh"),
                      ("g6.self_maintenance.usage", "_no_reading"), ("g6.self_maintenance.usage", "__call__")}, askers


def test_the_meter_its_schedule_and_its_reader_agree_on_a_figure_with_no_reset():
    window = ProviderWindow("codex", 100, START, reserve_share=0)
    week_end = START + WEEK
    replies = [_week_reply(10)]
    reader = AccountReader((window,), ask={"codex": lambda: replies[-1]})
    clock = [week_end - timedelta(minutes=5)]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    try:
        meter = _meter(store, read_account=reader)
        reader.refresh(clock[0])
        micro = timedelta(microseconds=1)
        for now, stands in ((week_end - micro, True), (week_end, False), (week_end + timedelta(minutes=1), False)):
            clock[0] = now
            held = reader("codex", now)
            seen = meter.reading("codex", now)
            assert (not held.unread, not seen.reason) == (stands, stands), (now, held, seen.reason)
            if not stands:                                         # an account to read again is looked at a refresh later
                assert meter.next_room(now) <= now + pacing.REFRESH
        replies.append({"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 300}}, "ordinaryUsageAllowed": True})
        reader.read_now(clock[0])                                  # a reply with no week in it replaces what no longer stands
        assert "stands until" not in reader.said["codex"] and not reader("codex", clock[0]).unread
        assert not meter.reading("codex", clock[0]).reason
    finally:
        store.close()


# 11. A sum that overflows and a time with nothing after it are refusals ------------------------------------------------------

_HUGE = 1e308                                  # finite; two of them are past the largest float
END_OF_TIME = datetime.max.replace(tzinfo=UTC)


def test_a_ledger_that_already_holds_a_sum_past_every_float_refuses_and_is_read():
    """Review wksfu4, finding 4: the ledger only grows, so a month or a week whose sum overflowed raised out of every later
    look. It is an infinity now, and over every cap."""
    store = OperatorStore(":memory:", clock=lambda: LAST_DAY)
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 1.7e308, "cash": 0})["venture_id"]
        for _ in range(2):
            assert scope.settle(venture, scope.reserve(venture, month="m", account="model", amount=1), reported=_HUGE) == _HUGE
        assert scope.committed(venture, "m") == {"model": math.inf} and scope.outstanding(venture, "m") == {"model": 0.0}
        with pytest.raises(BudgetRefused, match="budget_exhausted"):
            scope.reserve(venture, month="m", account="model", amount=1)
        meter = _meter(store, allowance=1.7e308)
        for tick in ("one", "two"):
            spend = meter.reserve("codex", LAST_DAY, 1, tick=tick)
            assert spend.admitted and meter.settle(spend, _HUGE) == _HUGE
        seen, more = meter.reading("codex", LAST_DAY), meter.reserve("codex", LAST_DAY, 1, tick="three")
        assert seen.used == math.inf and not more.admitted and "over" in more.reason
        assert meter.recover("codex") == 0 and meter.settle_open("codex") == 0
    finally:
        store.close()


@pytest.mark.parametrize("reset", _RESETS)
@pytest.mark.parametrize("back", [timedelta(0), timedelta(microseconds=1)], ids=["the last moment", "the one before it"])
def test_a_reading_dated_at_the_end_of_time_is_unread_or_stamped_after_and_never_raises(back, reset):
    """Review wksfu4, finding 6. A reading taken at the last moment a clock can show leaves no moment for a stamp after it:
    the store refuses its time, and the reading is unread. One taken a microsecond earlier can be stamped after; with no
    reset of its own, the week it would be put in ends past what a clock can show, and it is unread too."""
    read_at = END_OF_TIME - back
    held = [None]
    store = OperatorStore(":memory:", clock=lambda: LAST_DAY)
    try:
        meter = _meter(store, read_account=lambda *_: held[0])
        spend = meter.reserve("codex", LAST_DAY, 30)
        assert spend.admitted
        held[0] = AccountReading(used=10, estimated=False, read_at=read_at, resets_at=_RESETS[reset])
        refused = meter.reserve("codex", LAST_DAY, 1)
        assert not refused.admitted
        assert meter.reading("codex", LAST_DAY) is not None and meter.next_room(LAST_DAY) is not None      # neither raises
        if not back:
            with pytest.raises(ValueError, match="no moment after it"):
                meter.scope.keep_reading("codex", read_at)
            assert meter.scope._read_time(OWNER, "codex") is None
        assert meter.settle(spend, 30) == 30                       # a stamp that can be read back
        held[0] = None
        assert meter.reading("codex", LAST_DAY).used >= 30
        stamp = store.conn.execute("SELECT MAX(at_us) FROM week_settled").fetchone()[0]
        assert store_module._moment(stamp) <= END_OF_TIME
    finally:
        store.close()


def test_a_store_that_already_holds_a_stamp_past_the_end_of_time_refuses_and_does_not_raise():
    """What 358e286 could write, and a table that only grows cannot lose: a reading's time at the last moment, and a
    settlement stamped a microsecond after it. Every look at the provider then raised. Now the look says why nothing is
    reserved, a reservation is refused, a settlement is refused with a ``ValueError``, and a recovery settles none."""
    store = OperatorStore(":memory:", clock=lambda: LAST_DAY)
    try:
        meter = _meter(store)
        first, second = meter.reserve("codex", LAST_DAY, 10, tick="one"), meter.reserve("codex", LAST_DAY, 10, tick="two")
        last = store_module._us(END_OF_TIME)
        with store.conn:
            store.conn.execute("INSERT INTO week_readings VALUES (?, ?, ?)", (OWNER, "codex", last))
            store.conn.execute("INSERT INTO week_settled VALUES (?, ?)", (first.reservation, last + 1))
        seen = meter.reading("codex", LAST_DAY)
        assert "cannot be read" in seen.reason and "person looks" in seen.reason
        more = meter.reserve("codex", LAST_DAY, 1)
        assert not more.admitted and more.reason
        with pytest.raises(ValueError, match="past what a clock can show"):
            meter.settle(second, 1)
        assert meter.recover("codex") == 0.0
        assert store.conn.execute("SELECT COUNT(*) FROM week_settled").fetchone()[0] == 1 and not store.poisoned
    finally:
        store.close()
