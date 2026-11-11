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
        raised, stepped, poisoned, kept = _transaction(tmp_path, lambda c: c.arm("SAVEPOINT", _ERRORS[error](), after), nested=True)
        assert raised is None and stepped is False and not poisoned and kept == _NO_STEP

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


def test_no_step_starts_in_a_transaction_that_can_no_longer_commit():
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
                meter.reserve("codex", NOW, 1)             # an admission is a step: it is refused, never told it was admitted
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
    assert called <= {"isinstance", "ValueError", "tuple", "ProviderWindow", "utc", "exact_text"}, called
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
    ("g6.self_maintenance.usage", "_reset", "0 < (at - now).total_seconds() <= longest"),
}
_OF_INSTANTS_AND_COUNTS = {
    ("g6.operator.store", "in_transaction", "getattr(self._held, 'depth', 0) > 0"),
    ("g6.operator.store", "_date_old_charge", "settled >= start"), ("g6.operator.store", "keep_grid", "read_at <= kept_at"),
    ("g6.operator.store", "reserve_span", "span[0] <= span[1] < span[2]"),
    ("g6.self_maintenance.pacing", "ceiling", "day >= 7"),
    ("g6.self_maintenance.pacing", "standing", "reading.resets_at > now"),
    ("g6.self_maintenance.pacing", "standing", "reading.limited_until > now"),
    ("g6.self_maintenance.pacing", "_look", "now < wait"), ("g6.self_maintenance.pacing", "_used", "h.settled > read_at"),
    ("g6.self_maintenance.usage", "limits_reading", "(week['resetsAt'] or now + WEEK) <= now"),
    ("g6.self_maintenance.usage", "refresh", "now - self._asked < REFRESH"),
    ("g6.self_maintenance.usage", "_no_reading", "kept.limited_until > now"),
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
