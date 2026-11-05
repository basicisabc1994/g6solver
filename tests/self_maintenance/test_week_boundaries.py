# SPDX-License-Identifier: Apache-2.0
"""The loop's budget, held to three rules by inventories read from its code (review wks1's three classes met before).

1. Every instant entering ``g6.operator.store``, ``g6.self_maintenance.pacing`` or ``g6.self_maintenance.usage`` goes through
   ``utc`` at the boundary, so two renderings of one instant are one instant (identity_from_rendering).
2. One function reads every process result in ``g6.self_maintenance``, and only a clean exit is a reading
   (failure_read_as_success).
3. Everything admission reads is read inside the one transaction that writes the reservation (check_then_act_race).
"""
from __future__ import annotations

import ast
import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from g6.operator import store as store_module
from g6.operator.store import OperatorStore
from g6.self_maintenance import pacing, usage
from g6.self_maintenance.pacing import AccountReading, BudgetMeter, ProviderWindow, standing
from g6.self_maintenance.usage import AccountReader, limit_ends, limits_reading

MODULES = (store_module, pacing, usage)
NEW_YORK = ZoneInfo("America/New_York")
#: 01:45 the first time (daylight time, fold 0) and 01:30 the second (standard time, fold 1) on the night New York's clocks
#: go back: the later instant has the earlier wall clock.
EARLY = datetime(2026, 11, 1, 5, 45, tzinfo=UTC)
LATE = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
RENDERINGS = {
    "utc": lambda moment: moment,
    "new york": lambda moment: moment.astimezone(NEW_YORK),
    "fixed offset": lambda moment: moment.astimezone(timezone(timedelta(hours=10))),
}
_DATETIME = {"datetime", "datetime | None", "None | datetime"}
_MAKES_A_TIME = {"now", "utcnow", "today", "fromisoformat", "fromtimestamp", "utcfromtimestamp", "combine", "strptime"}


# 1. One instant, one result, in any rendering -------------------------------------------------------------------------------

def _plain(value):
    """``value`` with every moment as its UTC text and every reservation id left out (they are random)."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items() if k not in ("venture", "reservation")}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _refused(call) -> str:
    try:
        call()
    except ValueError as exc:
        return f"refused: {type(exc).__name__}"
    return "taken"


def _scenario(render, folder: Path) -> list:
    """Every public entry point of the three modules, on instants on either side of the repeated hour, as ``render`` writes
    them."""
    r, out = render, []
    start = EARLY - timedelta(days=6, hours=2)              # a week whose last day holds the repeated hour
    window = ProviderWindow("codex", 1000, r(start))
    out += [window.place(r(EARLY)), window.place(r(LATE)), window.next_day(r(EARLY)), window.week_end(r(LATE)),
            window.aligned(r(LATE)).week_start, window.aligned(r(start + timedelta(days=7))).week_start]
    reading = AccountReading(used=950, estimated=False, read_at=r(EARLY - timedelta(hours=1)),
                             limited_until=r(EARLY + timedelta(minutes=10)), resets_at=r(LATE))
    out += [standing(reading, r(EARLY)), standing(reading, r(LATE - timedelta(microseconds=1))), standing(reading, r(LATE))]
    told = [None]
    store = OperatorStore(folder / "operator.db")
    try:
        meter = BudgetMeter((window,), store=store, read_account=lambda provider, now: told[0])
        first = meter.reserve("codex", r(EARLY), 100)
        out += [first, meter.settle(first, 60, r(EARLY + timedelta(minutes=10)))]
        second = meter.reserve("codex", r(LATE), 50)
        out += [second, _refused(lambda: meter.settle(second, 50, r(EARLY + timedelta(minutes=30))))]
        told[0] = AccountReading(used=300, estimated=False, read_at=r(EARLY + timedelta(minutes=5)), resets_at=r(LATE))
        out += [meter.reading("codex", r(EARLY + timedelta(minutes=20))), meter.reading("codex", r(LATE))]
        told[0] = AccountReading(used=300, estimated=False, read_at=r(EARLY + timedelta(minutes=15)),
                                 resets_at=r(LATE + timedelta(days=7)))
        out += [meter.reading("codex", r(LATE + timedelta(minutes=1)))]
        meter.limit_reached("codex", r(LATE + timedelta(minutes=5)))
        out += [meter.reserve("codex", r(LATE + timedelta(minutes=1)), 1), meter.next_room(r(EARLY))]
        scope = store.for_owner(pacing.OWNER)
        scope.keep_grid("codex", r(LATE))
        out += [scope.week_grid("codex"), bool(scope.week_venture("codex", window.place(r(LATE))[0])),
                scope.week_held("codex", "model", r(LATE - timedelta(days=7)), r(LATE)),
                scope.week_held("codex", "model", r(LATE), r(LATE + timedelta(days=7)))]
    finally:
        store.close()
    limits = {"rateLimits": {"primary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": LATE.timestamp()}}}
    out += [limit_ends("HTTP 429: slow down", r(EARLY)), limits_reading(limits, window, r(EARLY)),
            limits_reading(limits, window, r(LATE))]
    answers = iter([limits, {"rateLimits": {"primary": {"usedPercent": 5, "windowDurationMins": 10080,
                                                         "resetsAt": (LATE + timedelta(days=7)).timestamp()}}}])
    reader = AccountReader((window,), ask={"codex": lambda: next(answers)})
    reader.refresh(r(EARLY))
    out += [reader("codex", r(LATE - timedelta(microseconds=1))), reader("codex", r(LATE))]
    reader.refresh(r(EARLY + timedelta(minutes=1)))           # inside its refresh: not asked again
    reader.read_now(r(LATE + timedelta(minutes=1)))
    out += [reader("codex", r(LATE + timedelta(minutes=1))), reader.windows["codex"].week_start]
    return _plain(out)


def test_every_entry_point_gives_one_result_for_one_instant_in_any_rendering(tmp_path):
    results = {}
    for name, render in RENDERINGS.items():
        folder = tmp_path / name.replace(" ", "-")
        folder.mkdir()
        results[name] = _scenario(render, folder)
    assert results["new york"] == results["utc"] == results["fixed offset"]


def test_the_fold_on_both_sides_is_rendered_as_it_should_be():
    assert (RENDERINGS["new york"](EARLY).fold, RENDERINGS["new york"](LATE).fold) == (0, 1)
    assert RENDERINGS["new york"](LATE).replace(tzinfo=None) < RENDERINGS["new york"](EARLY).replace(tzinfo=None)


# 1. The inventory: every instant enters through ``utc``, and every comparison and sum reads only those ------------------------

def _trees():
    return {module.__name__: ast.parse(Path(module.__file__).read_text(encoding="utf-8")) for module in MODULES}


def _functions(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]


def _is_utc_call(node) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "utc"


def _converted_at_entry(function) -> set[str]:
    """The parameters the leading statements of ``function`` (after its docstring) pass through ``utc``."""
    body = function.body[1:] if (function.body and isinstance(function.body[0], ast.Expr)
                                 and isinstance(function.body[0].value, ast.Constant)) else function.body
    done = set()
    for statement in body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            break
        target, value = statement.targets[0], statement.value
        pairs = (list(zip(target.elts, value.elts, strict=True)) if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
                 else [(target, value)])
        if not all(isinstance(t, ast.Name) and _is_utc_call(v) and isinstance(v.args[0], ast.Name) and v.args[0].id == t.id
                   for t, v in pairs):
            break
        done |= {t.id for t, _ in pairs}
    return done


def _datetime_parameters(function) -> set[str]:
    args = function.args
    return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            if a.annotation is not None and ast.unparse(a.annotation) in _DATETIME}


def _datetime_fields() -> dict[str, set[str]]:
    """Each dataclass of the three modules, and its fields that hold a moment."""
    found = {}
    for tree in _trees().values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any("dataclass" in ast.unparse(d) for d in node.decorator_list):
                fields = {s.target.id for s in node.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
                          and ast.unparse(s.annotation) in _DATETIME}
                if fields:
                    found[node.name] = fields
    return found


def test_every_parameter_that_takes_a_moment_goes_through_utc_first():
    entries, missing = [], []
    for name, tree in _trees().items():
        for function in _functions(tree):
            wanted = _datetime_parameters(function)
            entries += [f"{name}.{function.name}({p})" for p in sorted(wanted)]
            missing += [f"{name}.{function.name}({p})" for p in sorted(wanted - _converted_at_entry(function))]
    known = {"g6.self_maintenance.pacing.place(now)", "g6.self_maintenance.pacing.standing(now)",
             "g6.self_maintenance.pacing.reserve(now)", "g6.self_maintenance.pacing.settle(now)",
             "g6.self_maintenance.usage.refresh(now)", "g6.self_maintenance.usage.__call__(now)",
             "g6.self_maintenance.usage.limits_reading(now)", "g6.operator.store.settle(at)", "g6.operator.store.week_held(start)"}
    assert known <= set(entries), sorted(known - set(entries))       # the inventory is read from the code, and holds these
    assert not missing, missing


def test_every_field_that_holds_a_moment_goes_through_utc_when_it_is_made():
    fields = _datetime_fields()
    assert {"ProviderWindow", "AccountReading", "Admission", "Held"} <= set(fields), fields
    missing = []
    for tree in _trees().values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in fields:
                post = next((f for f in node.body if isinstance(f, ast.FunctionDef) and f.name == "__post_init__"), None)
                text = ast.unparse(post) if post is not None else ""
                missing += [f"{node.name}.{field}" for field in sorted(fields[node.name])
                            if f"'{field}'" not in text or "utc(" not in text]
    assert not missing, missing


def test_every_moment_made_in_the_three_modules_is_made_through_utc():
    made, loose = [], []
    for name, tree in _trees().items():
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            making = ((isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "datetime"
                       and func.attr in _MAKES_A_TIME) or (isinstance(func, ast.Name) and func.id == "datetime"))
            moving = isinstance(func, ast.Attribute) and func.attr == "astimezone"
            if making or moving:
                where = f"{name}:{node.lineno}: {ast.unparse(node)}"
                made.append(where)
                parent = parents.get(node)
                inside_utc = any(isinstance(f, ast.FunctionDef) and f.name == "utc" and f.lineno <= node.lineno <= f.end_lineno
                                 for f in _functions(tree))
                if not ((_is_utc_call(parent) and parent.args and parent.args[0] is node) or (moving and inside_utc)):
                    loose.append(where)
    assert len(made) >= 6, made
    assert not loose, loose


def _moment_names(function, fields: set[str], attributes: set[str], makers: set[str]) -> set[str]:
    """The names in ``function`` that hold a moment: its parameters that do, and every name given one, to a fixed point."""
    names = set(_datetime_parameters(function))
    while True:
        before = len(names)
        for node in ast.walk(function):
            targets, value = ((node.targets, node.value) if isinstance(node, ast.Assign) else
                              ([node.target], node.value) if isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value else
                              ([node.target], node.iter) if isinstance(node, ast.For | ast.comprehension) else ([], None))
            if value is not None and _holds_a_moment(value, names, fields, attributes, makers):
                for target in targets:
                    names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        if len(names) == before:
            return names


def _holds_a_moment(node, names, fields, attributes, makers) -> bool:
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and part.id in names:
            return True
        if isinstance(part, ast.Attribute) and (part.attr in fields or part.attr in attributes):
            return True
        if isinstance(part, ast.Call) and ((isinstance(part.func, ast.Name) and part.func.id in makers)
                                           or (isinstance(part.func, ast.Attribute) and part.func.attr in makers)):
            return True
    return False


def test_every_comparison_and_sum_of_moments_reads_only_moments_that_came_through_utc():
    """The inventory of every comparison, ``+`` and ``-`` in the three modules with a moment on either side, and where each
    moment on it comes from: a parameter converted at entry, a field converted when it was made, a name given only such
    values, ``utc`` itself, or a function of these modules that returns a moment made of them."""
    trees, fields = _trees(), set().union(*_datetime_fields().values())
    makers = {"utc", "_moment", "_instant", "_time", "_reset", "limit_ends", "place", "next_day", "week_end", "_wall_clock",
              "clock"}
    attributes = {"_asked", "_EPOCH"}
    inventory, unsure = [], []
    for name, tree in trees.items():
        for function in _functions(tree):
            names = _moment_names(function, fields, attributes, makers) | {"_EPOCH"}
            converted = _converted_at_entry(function)
            for node in ast.walk(function):
                sides = ([node.left, *node.comparators] if isinstance(node, ast.Compare) else
                         [node.left, node.right] if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub)
                         else [])
                moments = [side for side in sides if _holds_a_moment(side, names, fields, attributes, makers)]
                if not moments:
                    continue
                inventory.append(f"{name}.{function.name}:{node.lineno}: {ast.unparse(node)}")
                for side in moments:
                    for part in ast.walk(side):
                        if (isinstance(part, ast.Name) and part.id in _datetime_parameters(function)
                                and part.id not in converted):
                            unsure.append(f"{name}.{function.name}:{node.lineno}: {part.id} never went through utc")
    assert len(inventory) >= 40, inventory
    assert not unsure, unsure


# 2. One function reads every process result, and only a clean exit is a reading ---------------------------------------------

_STARTS_A_PROCESS = {"run_process", "run_contained", "Popen", "check_output", "check_call", "system", "popen", "startfile",
                     "create_subprocess_exec", "create_subprocess_shell", "spawnv", "spawnve", "spawnl", "spawnle", "execv",
                     "execve", "execl", "execle", "execvp", "execlp"}


def test_every_process_self_maintenance_starts_is_read_by_the_one_function():
    root = Path(usage.__file__).parent
    sites, outside = [], []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = _functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            receiver = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
            if called in _STARTS_A_PROCESS or (receiver == "subprocess" and called in {"run", "call"}):
                inside = [f.name for f in functions if f.lineno <= node.lineno <= f.end_lineno]
                site = f"{path.relative_to(root)}:{node.lineno}: {ast.unparse(node.func)} in {inside}"
                sites.append(site)
                if path.name != "usage.py" or "_ran" not in inside:
                    outside.append(site)
    assert sites, "the inventory found no process at all"
    assert not outside, outside


def _ends_with(status, answer):
    import os

    def ran(argv, *, stdin, stdout, **kwargs):
        os.read(stdin, 4096)
        os.write(stdout, (json.dumps({"id": 2, "result": answer}) + "\n").encode())
        return status

    return ran


def test_a_clean_exit_is_the_one_success(monkeypatch, tmp_path):
    from contextlib import nullcontext

    monkeypatch.setattr(usage.tempfile, "TemporaryDirectory", lambda **_: nullcontext(str(tmp_path)))
    monkeypatch.setattr(usage.component_process, "run_process", _ends_with(0, {"answer": 1}))
    assert usage.read_limits(["fake-usage-tool"]) == {"answer": 1}
    for status in (1, -1, 255, 2 ** 31, None, "0", 0.0, False, b"0"):      # 0.0 and False are equal to 0, and are not it
        monkeypatch.setattr(usage.component_process, "run_process", _ends_with(status, {"answer": 1}))
        with pytest.raises(usage.UsageUnavailable, match="status"):
            usage.read_limits(["fake-usage-tool"])


def test_a_process_that_could_not_be_run_or_ended_is_no_reading(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from g6.core.component_process import ContainmentFailure

    monkeypatch.setattr(usage.tempfile, "TemporaryDirectory", lambda **_: nullcontext(str(tmp_path)))
    for failure in (OSError("no such program"), ContainmentFailure("the tree was not ended"), TimeoutError("window")):
        def fails(argv, *, failure=failure, **kwargs):
            raise failure

        monkeypatch.setattr(usage.component_process, "run_process", fails)
        with pytest.raises(usage.UsageUnavailable):
            usage.read_limits(["fake-usage-tool"])


# 3. Everything admission reads is read inside the transaction that writes ----------------------------------------------------

def _scope_calls(function_name: str) -> set[str]:
    tree = ast.parse(Path(pacing.__file__).read_text(encoding="utf-8"))
    meter = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "BudgetMeter")
    function = next(f for f in meter.body if isinstance(f, ast.FunctionDef) and f.name == function_name)
    return {node.func.attr for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "scope"}


def test_everything_admission_reads_is_read_inside_the_transaction_that_writes(tmp_path, monkeypatch):
    """The store calls admission makes, listed from ``BudgetMeter.reserve`` and the look it decides with, are each made with
    the ``BEGIN IMMEDIATE`` transaction open: the kept grid, the week's venture and every reservation overlapping it, and so
    the reading's baseline; the grid a reading moves is kept inside it too."""
    inputs = (_scope_calls("_look") | _scope_calls("reserve")) - {"reserve_span"}
    assert {"week_grid", "keep_grid", "week_held", "week_venture"} <= inputs, inputs
    start = datetime(2026, 10, 26, tzinfo=UTC)
    store = OperatorStore(tmp_path / "operator.db")
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store,
                            read_account=lambda *_: AccountReading(used=100, read_at=start, resets_at=start + timedelta(days=8)))
        seen: dict[str, list[bool]] = {}
        for name in inputs:
            real = getattr(meter.scope, name)

            def noted(*args, real=real, name=name, **kwargs):
                seen.setdefault(name, []).append(bool(store._depth) and store.conn.in_transaction)
                return real(*args, **kwargs)

            monkeypatch.setattr(meter.scope, name, noted)
        assert meter.reserve("codex", start + timedelta(days=2), 100).admitted
    finally:
        store.close()
    assert set(seen) == inputs, (inputs, seen)
    assert all(all(inside) for inside in seen.values()), seen


def test_another_connection_cannot_change_what_admission_read_before_it_writes(tmp_path):
    """Review wks1 finding 2, the other way round: a grid another connection keeps while admission is deciding waits for it,
    so the admission stands on what it read, and the new grid is kept after it."""
    monday = datetime(2026, 10, 26, tzinfo=UTC)
    now, path = monday + timedelta(days=6, hours=1), tmp_path / "operator.db"
    first, second = OperatorStore(path), OperatorStore(path)
    try:
        window = ProviderWindow("codex", 1000, monday)
        deciding, other = BudgetMeter((window,), store=first), BudgetMeter((window,), store=second)
        order: list[str] = []
        look = deciding._look

        def while_deciding(provider, at):
            seen = look(provider, at)
            if not order:
                order.append("read")
                keeper = threading.Thread(target=lambda: (other.scope.keep_grid("codex", monday + timedelta(days=10)),
                                                          order.append("grid kept")))
                keeper.start()
                keeper.join(0.5)
                assert keeper.is_alive(), "another connection kept a grid inside the transaction admission reads in"
                order.append(keeper)
            return seen

        deciding._look = while_deciding
        assert deciding.reserve("codex", now, 100).admitted
        order[1].join(10)
        assert [step for step in order if isinstance(step, str)] == ["read", "grid kept"]
    finally:
        first.close()
        second.close()
