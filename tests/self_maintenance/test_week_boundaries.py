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
import functools
import json
import re
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
    told, clock = [None], [r(EARLY)]
    store = OperatorStore(folder / "operator.db", clock=lambda: clock[0])
    try:
        meter = BudgetMeter((window,), store=store, read_account=lambda provider, now: told[0])
        first = meter.reserve("codex", r(EARLY), 100)
        clock[0] = r(EARLY + timedelta(minutes=10))
        out += [first, meter.settle(first, 60)]
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
             "g6.self_maintenance.usage.limits_reading(now)", "g6.operator.store.week_held(start)"}
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


# 1. The readers' inventory, drawn from the producers' own listing, across the whole package -------------------------------

_PACKAGE = Path(store_module.__file__).resolve().parents[1]
_PRODUCERS = ("g6.operator.store", "g6.self_maintenance.pacing", "g6.self_maintenance.usage")
#: The modules whose objects carry those instants out: the loop holds a tick's reservation and its request cutoff.
_CARRIERS = (*_PRODUCERS, "g6.metaharness.loop")
_DURATION = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
#: The boundary itself: ``utc``, and the store's turning of a UTC instant into whole microseconds and back.
_BOUNDARY = ("utc", "_us", "_moment", "_instant")
#: Of the boundary, what gives whole microseconds or a moment fresh from ``utc``: no instant from a field to compare.
_FRESH = ("_us", "_moment", "_instant")


@functools.cache
def _package_trees() -> dict[str, ast.Module]:
    """Every module of the package, parsed once for every inventory (none of them changes a tree)."""
    found = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        name = "g6." + ".".join(path.relative_to(_PACKAGE).with_suffix("").parts).removesuffix(".__init__")
        found[name] = ast.parse(path.read_text(encoding="utf-8"))
    return found


def _imports(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
    return names


def _producer_functions(trees) -> set[str]:
    """The functions and methods of the producers, whose instants all came through ``utc`` (the inventories above)."""
    return {f.name for name in _PRODUCERS for f in _functions(trees[name])}


def _pairs(node):
    """What an assignment gives each thing it assigns: element by element when both sides are tuples of one length."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple) and len(target.elts) == len(node.value.elts):
            yield from zip(target.elts, node.value.elts, strict=True)
        elif isinstance(target, ast.Tuple):
            yield from ((element, node.value) for element in target.elts)
        else:
            yield target, node.value


def _assigned(where) -> list[tuple[ast.expr, ast.expr]]:
    return [pair for node in ast.walk(where) if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None
            for pair in _pairs(node)]


def _called(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


class _Readers:
    """Within one module: what holds an instant from a reservation, a span, a reading or a reset (tainted), followed from
    each read of a producer's field through names, ``self`` attributes, returns and the parameters of calls in the
    module; and whether an expression came through ``utc`` first (acceptable)."""

    def __init__(self, tree: ast.Module, fields: set[str], producers: set[str]) -> None:
        self.tree, self.fields, self.producers = tree, set(fields), producers
        self.functions = _functions(tree)
        self.classes = {id(f): node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                        for f in node.body if isinstance(f, ast.FunctionDef)}
        self.returning: set[str] = set()
        self.params: dict[int, set[str]] = {id(f): set() for f in self.functions}
        self.names: dict[int, set[str]] = {id(f): set() for f in self.functions}
        self.attributes = set(fields)
        self._settle()

    def tainted(self, node, names: set[str]) -> bool:
        if isinstance(node, ast.Name):
            return node.id in names
        if isinstance(node, ast.Attribute):
            return node.attr in self.attributes
        if isinstance(node, ast.Subscript):
            key = node.slice
            return (isinstance(key, ast.Constant) and key.value in self.fields) or self.tainted(node.value, names)
        if isinstance(node, ast.Call):
            called = _called(node)
            if called == "get" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in self.fields:
                return True
            if called in _FRESH:
                return False
            if called in ("utc", "min", "max", "fromisoformat", "fromtimestamp", "astimezone", "replace"):
                receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                return any(self.tainted(a, names) for a in (*node.args, *([receiver] if receiver is not None else [])))
            return called in self.returning
        if isinstance(node, ast.GeneratorExp | ast.ListComp | ast.SetComp):
            return self.tainted(node.elt, names | self._comprehended(node, names)) or any(
                self.tainted(g.iter, names) for g in node.generators)
        if isinstance(node, ast.BinOp | ast.BoolOp | ast.IfExp | ast.Tuple | ast.List | ast.Set):
            return any(self.tainted(child, names) for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr))
        return False

    def _comprehended(self, node, names: set[str]) -> set[str]:
        """The names a comprehension binds from what it walks over that holds such an instant."""
        return {part.id for g in node.generators if self.tainted(g.iter, names) for part in ast.walk(g.target)
                if isinstance(part, ast.Name)}

    def _settle(self) -> None:
        def size():
            return (len(self.returning), len(self.attributes), sum(map(len, self.names.values())),
                    sum(map(len, self.params.values())))

        while True:
            before = size()
            for function in self.functions:
                names = self.names[id(function)] | self.params[id(function)]
                for comprehension in (n for n in ast.walk(function) if isinstance(n, ast.GeneratorExp | ast.ListComp | ast.SetComp)):
                    names |= self._comprehended(comprehension, names)
                for target, value in _assigned(function):
                    if self.tainted(value, names):
                        if isinstance(target, ast.Name):
                            names.add(target.id)
                        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                                and target.value.id == "self":
                            self.attributes.add(target.attr)
                for node in ast.walk(function):
                    if isinstance(node, ast.Return) and node.value is not None and self.tainted(node.value, names):
                        self.returning.add(function.name)
                    elif isinstance(node, ast.Call):
                        self._into_callee(node, names)
                self.names[id(function)] = names - self.params[id(function)]
            if size() == before:
                return

    def _into_callee(self, call: ast.Call, names: set[str]) -> None:
        called = _called(call)
        for target in (f for f in self.functions
                       if f.name == called or (f.name == "__init__" and self.classes.get(id(f)) == called)):
            given = [a for a in (*target.args.posonlyargs, *target.args.args)]
            if given and given[0].arg in ("self", "cls"):
                given = given[1:]
            kinds = {a.arg: ast.unparse(a.annotation) if a.annotation is not None else ""
                     for a in (*given, *target.args.kwonlyargs)}
            holds = {name for name, kind in kinds.items() if not kind or "datetime" in kind or kind == "Any"}  # an int is not one
            for argument, value in zip(given, call.args, strict=False):
                if argument.arg in holds and self.tainted(value, names):
                    self.params[id(target)].add(argument.arg)
            for keyword in call.keywords:
                if keyword.arg in holds and self.tainted(keyword.value, names):
                    self.params[id(target)].add(keyword.arg)

    def acceptable(self, node, function, seen=None) -> bool:
        """Came through ``utc`` first: a call to it, a producer's field (made through it), a producer's function, a
        parameter converted at entry, a name or ``self`` attribute given only such values, or a duration or constant."""
        seen = seen if seen is not None else set()
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Call):
            called = _called(node)
            if called in ("utc", "timedelta") or called in self.producers:
                return True
            return called in ("min", "max") and all(self.acceptable(a, function, seen) for a in node.args)
        if isinstance(node, ast.Name):
            if _DURATION.match(node.id) or node.id in _converted_at_entry(function) or ("name", node.id) in seen:
                return True
            seen.add(("name", node.id))
            values = [v for t, v in _assigned(function) if isinstance(t, ast.Name) and t.id == node.id]
            for generator in (g for n in ast.walk(function) if isinstance(n, ast.GeneratorExp | ast.ListComp | ast.SetComp)
                              for g in n.generators):
                if any(isinstance(p, ast.Name) and p.id == node.id for p in ast.walk(generator.target)):
                    values += (generator.iter.elts if isinstance(generator.iter, ast.Tuple | ast.List | ast.Set)
                               else [generator.iter])
            return bool(values) and all(self.acceptable(v, function, seen) for v in values)
        if isinstance(node, ast.Attribute):
            if ("attribute", node.attr) in seen:
                return True
            seen.add(("attribute", node.attr))
            given = [(f, v) for f in self.functions for t, v in _assigned(f)
                     if isinstance(t, ast.Attribute) and t.attr == node.attr]
            if not given:
                return node.attr in self.fields                 # a producer's field, made through ``utc``
            return all(self.acceptable(v, f, seen) for f, v in given)
        if isinstance(node, ast.BinOp | ast.BoolOp | ast.IfExp):
            parts = ([node.body, node.orelse] if isinstance(node, ast.IfExp) else
                     node.values if isinstance(node, ast.BoolOp) else [node.left, node.right])
            return all(self.acceptable(p, function, seen) for p in parts)
        if isinstance(node, ast.Subscript):
            return self.acceptable(node.value, function, seen)
        if isinstance(node, ast.GeneratorExp | ast.ListComp | ast.SetComp):
            return self.acceptable(node.elt, function, seen)
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return all(self.acceptable(e, function, seen) for e in node.elts)
        return False

def test_every_comparison_and_sum_with_a_reservation_span_reading_or_reset_instant_takes_both_sides_through_utc():
    """The fields are the producers' own listing of what holds an instant (every datetime field of their dataclasses). Their
    readers are found wherever they are in the package, in any module that carries the producers' objects, and followed;
    every comparison, ``+``, ``-``, ``min`` and ``max`` with such an instant on it must take every side through ``utc``."""
    trees = _package_trees()
    fields = set().union(*_datetime_fields().values())
    producers = _producer_functions(trees)
    inventory, loose = [], []
    for name, tree in trees.items():
        if name not in _CARRIERS and not _imports(tree) & set(_CARRIERS):
            continue
        readers = _Readers(tree, fields, producers)
        for function in readers.functions:
            if name == "g6.operator.store" and function.name in _BOUNDARY:
                continue
            names = readers.names[id(function)] | readers.params[id(function)]
            for node in ast.walk(function):
                if isinstance(node, ast.Compare) and all(isinstance(op, ast.Is | ast.IsNot) for op in node.ops):
                    continue                                          # whether there is one, not when it is
                if isinstance(node, ast.Compare):
                    sides = [node.left, *node.comparators]
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub):
                    sides = [node.left, node.right]
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max"):
                    sides = list(node.args)
                else:
                    continue
                if not any(readers.tainted(side, names) for side in sides):
                    continue
                where = f"{name}.{function.name}:{node.lineno}: {ast.unparse(node)}"
                inventory.append(where)
                if not all(readers.acceptable(side, function) for side in sides):
                    loose.append(where)
    readers = {line.split(":", 1)[0] for line in inventory}
    known = {"g6.metaharness.loop.admit", "g6.metaharness.loop.ends_by",                          # the request cutoff
             "g6.metaharness.runner._after", "g6.metaharness.runner._due", "g6.metaharness.runner.step",  # a budget wait
             "g6.self_maintenance.pacing._used", "g6.self_maintenance.pacing.standing", "g6.self_maintenance.pacing._look"}
    assert known <= readers, sorted(known - readers)
    assert not loose, loose


# 1. The writers' inventory: every statement that writes a reservation writes its span, or admission reads it into one -----

_WRITES_THE_LEDGER = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+ledger\b", re.I)


def _enclosing(tree, node) -> str:
    return next((f.name for f in _functions(tree) if f.lineno <= node.lineno <= f.end_lineno
                 and not any(g is not f and f.lineno <= g.lineno and g.end_lineno <= f.end_lineno
                             and g.lineno <= node.lineno <= g.end_lineno for g in _functions(tree))), "")


def test_every_statement_that_writes_a_reservation_writes_its_span_or_admission_reads_it_into_one():
    trees = _package_trees()
    statements = sorted({(name, _enclosing(tree, node)) for name, tree in trees.items() for node in ast.walk(tree)
                         if isinstance(node, ast.Constant) and isinstance(node.value, str)
                         and _WRITES_THE_LEDGER.search(node.value)})
    assert statements == [("g6.operator.store", "_append_ledger")], statements
    reservations = sorted({(name, _enclosing(tree, node)) for name, tree in trees.items() for node in ast.walk(tree)
                           if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                           and node.func.attr == "_append_ledger" and len(node.args) > 3
                           and isinstance(node.args[3], ast.Constant) and node.args[3].value == "reserve"})
    assert reservations == [("g6.operator.store", "reserve"), ("g6.operator.store", "reserve_span")], reservations
    store_tree = trees["g6.operator.store"]
    span_writer = next(f for f in _functions(store_tree) if f.name == "reserve_span")
    blocks = [n for n in ast.walk(span_writer) if isinstance(n, ast.With) and "immediate" in ast.unparse(n.items[0].context_expr)]
    assert len(blocks) == 1, "reserve_span writes in one transaction"
    text = ast.unparse(blocks[0])
    assert "_append_ledger(" in text and "INSERT INTO week_spans" in text and "decide()" in text
    # The operator's own ``reserve`` writes no span: whatever it writes for a provider, admission reads into a span first,
    # inside its own transaction (``decide`` runs ``_look`` inside ``reserve_span``'s), before it reads any span.
    pacing_tree = trees["g6.self_maintenance.pacing"]
    meter = next(n for n in ast.walk(pacing_tree) if isinstance(n, ast.ClassDef) and n.name == "BudgetMeter")
    look = next(f for f in meter.body if isinstance(f, ast.FunctionDef) and f.name == "_look")
    calls = [(n.lineno, n.func.attr) for n in ast.walk(look) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "scope"]
    first = {attr: line for line, attr in sorted(calls, reverse=True)}
    assert first["adopt_books"] < first["week_held"], calls
    reserve = next(f for f in meter.body if isinstance(f, ast.FunctionDef) and f.name == "reserve")
    decide = next(f for f in ast.walk(reserve) if isinstance(f, ast.FunctionDef) and f.name == "decide")
    assert "self._look(" in ast.unparse(decide) and "decide=decide" in ast.unparse(reserve)

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
    assert {"kept_grid", "keep_grid", "week_held", "week_venture", "adopt_books"} <= inputs, inputs
    start = datetime(2026, 10, 26, tzinfo=UTC)
    store = OperatorStore(tmp_path / "operator.db")
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store,
                            read_account=lambda *_: AccountReading(used=100, read_at=start, resets_at=start + timedelta(days=8)))
        seen: dict[str, list[bool]] = {}
        for name in inputs:
            real = getattr(meter.scope, name)

            def noted(*args, real=real, name=name, **kwargs):
                seen.setdefault(name, []).append(store.in_transaction() and store.conn.in_transaction)
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


# 1. Every clock the budget reads gives UTC where it is defined (review wks3, finding 2) -----------------------------------------

_CLOCK = "Callable[[], datetime]"
_READS_THE_CLOCK = {"now", "utcnow", "today"}


def _budget_modules() -> dict[str, ast.Module]:
    """The modules that make or carry the budget's instants, and every module that imports one of them."""
    return {name: tree for name, tree in _package_trees().items() if name in _CARRIERS or _imports(tree) & set(_CARRIERS)}


def _clock_parameters(tree: ast.Module) -> list[tuple[ast.FunctionDef, str]]:
    return [(f, a.arg) for f in _functions(tree) for a in (*f.args.posonlyargs, *f.args.args, *f.args.kwonlyargs)
            if a.annotation is not None and _CLOCK in ast.unparse(a.annotation)]


def _within(node, ancestor, parents) -> bool:
    while node is not None and node is not ancestor:
        node = parents.get(node)
    return node is ancestor


def test_every_clock_the_budget_reads_gives_utc_where_it_is_defined():
    """Listed from the code: each read of the machine's clock (``datetime.now``, ``utcnow``, ``today``) and each clock a
    function is given (a parameter typed ``Callable[[], datetime]``, and so the attribute it is kept in). Each read is made
    through ``utc``; each given clock is taken in through ``utc_clock``, or handed on, by name, to a clock parameter of the
    same listing, so no instant the budget reads is a local one (identity_from_rendering, met a third time here)."""
    trees = _budget_modules()
    takes: dict[str, set[str]] = {}               # a function or class, and the clock parameters it takes in
    for tree in trees.values():
        for function, parameter in _clock_parameters(tree):
            takes.setdefault(function.name, set()).add(parameter)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                init = next((f for f in node.body if isinstance(f, ast.FunctionDef) and f.name == "__init__"), None)
                takes[node.name] = {p for _, p in _clock_parameters(init)} if init is not None else set()
    inventory, loose = [], []
    for name, tree in trees.items():
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _READS_THE_CLOCK
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "datetime"):
                where = f"{name}:{node.lineno}: {ast.unparse(node)}"
                inventory.append(where)
                parent = parents.get(node)
                if not (_is_utc_call(parent) and parent.args and parent.args[0] is node):
                    loose.append(where)
        for function, parameter in _clock_parameters(tree):
            if name == "g6.operator.store" and function.name == "utc_clock":
                continue                                              # the boundary itself
            where = f"{name}.{function.name}({parameter})"
            inventory.append(where)
            rebound = next((s.lineno for s in function.body if isinstance(s, ast.Assign) and len(s.targets) == 1
                            and isinstance(s.targets[0], ast.Name) and s.targets[0].id == parameter
                            and isinstance(s.value, ast.Call) and _called(s.value) == "utc_clock"
                            and ast.unparse(s.value.args[0]) == parameter), None)
            for use in ast.walk(function):
                if not (isinstance(use, ast.Name) and use.id == parameter and isinstance(use.ctx, ast.Load)):
                    continue
                wrapped = any(isinstance(call, ast.Call) and _called(call) == "utc_clock"
                              and any(_within(use, arg, parents) for arg in call.args)
                              for call in ast.walk(function))
                parent = parents.get(use)
                handed_on = (isinstance(parent, ast.keyword) and isinstance(parents.get(parent), ast.Call)
                             and parent.arg in takes.get(_called(parents[parent]), set()))
                if not (wrapped or handed_on or (rebound is not None and use.lineno > rebound)):
                    loose.append(f"{where}:{use.lineno}: {ast.unparse(parent)}")
    known = {"g6.operator.store.__init__(clock)", "g6.operator.queue.__init__(clock)", "g6.metaharness.loop.__init__(now)",
             "g6.metaharness.runner.__init__(now)", "g6.metaharness.runner.run(now)", "g6.metaharness.runner.open_loop(now)"}
    assert known <= set(inventory), sorted(known - set(inventory))
    assert any(line.startswith("g6.mcp.improve:") for line in inventory), inventory    # the start records, read as UTC
    assert sum(".now(" in line for line in inventory) >= 8, inventory
    assert not loose, loose


def test_a_clock_in_any_zone_reads_as_utc_where_it_is_taken_in(tmp_path):
    from g6.metaharness.loop import _Requests
    from g6.operator.queue import ActionQueue
    from g6.operator.store import utc_clock

    for render in RENDERINGS.values():
        for moment in (EARLY, LATE):
            store = OperatorStore(tmp_path / "op.db", clock=lambda moment=moment, render=render: render(moment))
            try:
                queue = ActionQueue(store.for_owner("ada"), clock=lambda moment=moment, render=render: render(moment))
                for clock in (store.clock, queue._clock, _Requests(now=lambda moment=moment, render=render: render(moment)).now):
                    assert clock() == moment and clock().tzinfo is UTC
            finally:
                store.close()
    with pytest.raises(ValueError):
        utc_clock(lambda: datetime(2026, 11, 1, 1, 30))()                   # a clock with no zone is refused, never guessed


# 3. The store's lock owns every write and every commit on its connection (review wks3, finding 1) -----------------------------

_WRITES = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|BEGIN)\b", re.I)
_LOCKS = ("immediate()", "_writing")


def _store_modules() -> dict[str, ast.Module]:
    return {name: tree for name, tree in _package_trees().items()
            if name == "g6.operator.store" or "g6.operator.store" in _imports(tree)}


def _on_the_connection(node) -> bool:
    return isinstance(node, ast.Attribute) and node.attr in ("conn", "_conn")


def _leading(text, function, depth: int = 0) -> str | None:
    """How the text of a statement begins, where it is written out: a literal, an f-string, a sum that starts with one, or
    a name every assignment in its function gives one of those; ``None`` when that cannot be read from the code."""
    if isinstance(text, ast.Constant) and isinstance(text.value, str):
        return text.value
    if isinstance(text, ast.JoinedStr) and text.values and isinstance(text.values[0], ast.Constant):
        return text.values[0].value
    if isinstance(text, ast.BinOp) and isinstance(text.op, ast.Add):
        return _leading(text.left, function, depth)
    if isinstance(text, ast.Name) and depth < 3:
        given = {_leading(value, function, depth + 1) for target, value in _assigned(function)
                 if isinstance(target, ast.Name) and target.id == text.id}
        return given.pop() if len(given) == 1 else None
    return None


def _writes(call: ast.Call, function) -> bool:
    """Whether a statement on the connection writes or ends a transaction. One whose text cannot be read from the code, or
    that does not begin with ``SELECT``, counts as a write."""
    if call.func.attr in ("commit", "rollback", "executescript"):
        return True
    text = _leading(call.args[0], function) if call.args else None
    return text is None or bool(_WRITES.match(text)) or not re.match(r"^\s*(SELECT|PRAGMA)\b", text, re.I)


def _inside(node, parents) -> str:
    """The name of the innermost function ``node`` is in, or ``""``."""
    node = parents.get(node)
    while node is not None and not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        node = parents.get(node)
    return node.name if node is not None else ""


def _held(node, parents) -> bool:
    """Whether ``node`` is inside a ``with`` that holds the store's lock (``immediate()`` or ``_writing``)."""
    node = parents.get(node)
    while node is not None:
        if isinstance(node, ast.With) and any(ast.unparse(item.context_expr).endswith(_LOCKS) for item in node.items):
            return True
        node = parents.get(node)
    return False


def test_every_write_and_commit_on_the_stores_connection_holds_its_lock():
    """Listed from the code: every statement run on the store's connection (``conn``, and the queue's ``_conn``, which is
    the same) that writes, commits or rolls back, and every ``with`` on the connection, which commits. Each holds the store's
    lock: inside ``immediate()`` or ``_writing``, or in a function every call of which does, so no thread can commit another
    thread's admission half way (check_then_act_race)."""
    trees = _store_modules()
    parents = {name: {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
               for name, tree in trees.items()}
    statements, unheld = [], {}
    for name, tree in trees.items():
        functions = {f.name: f for f in _functions(tree)}
        for node in ast.walk(tree):
            run = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _on_the_connection(node.func.value)
                   and node.func.attr in ("execute", "executemany", "executescript", "commit", "rollback")
                   and _writes(node, functions.get(_inside(node, parents[name]), tree)))
            commits = isinstance(node, ast.With) and any(_on_the_connection(item.context_expr) for item in node.items)
            if run or commits:
                where = f"{name}.{_inside(node, parents[name])}:{node.lineno}: {ast.unparse(node.func if run else node.items[0])}"
                statements.append(where)
                if not _held(node, parents[name]):
                    unheld.setdefault((name, _inside(node, parents[name])), []).append(where)
    everything = {(name, f.name) for name, tree in trees.items() for f in _functions(tree)}
    calls: dict[str, list[tuple[bool, tuple[str, str]]]] = {}     # each call of a name: whether it holds, and where it is
    for name, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                calls.setdefault(_called(node), []).append((_held(node, parents[name]), (name, _inside(node, parents[name]))))
    held_by_callers: set[tuple[str, str]] = set()
    while True:                                   # a function whose every call holds the lock holds it too
        more = set()
        for name, function in everything - held_by_callers:
            found = calls.get(function, [])
            if found and all(held or caller in held_by_callers for held, caller in found):
                more.add((name, function))
        if not more:
            break
        held_by_callers |= more
    loose = [line for key, lines in unheld.items() if key not in held_by_callers for line in lines]
    functions = {line.split(":", 1)[0] for line in statements}
    known = {"g6.operator.store.immediate", "g6.operator.store.script", "g6.operator.store._append_ledger",
             "g6.operator.store._append_evidence", "g6.operator.store.set_status", "g6.operator.store.keep_grid",
             "g6.operator.store.reserve_span", "g6.operator.store._stamp", "g6.operator.store._abandon",
             "g6.operator.store.__init__"}                       # ``_settle``'s one statement of its own is ``_stamp``'s now
    assert known <= functions, sorted(known - functions)
    assert any(line.startswith("g6.operator.queue.") for line in statements), statements
    assert not loose, loose


def test_a_write_from_another_thread_waits_for_an_open_transaction(tmp_path):
    store = OperatorStore(tmp_path / "op.db")
    try:
        scope = store.for_owner("ada")
        venture = scope.create_venture("v", caps={"model": 10, "cash": 0})["venture_id"]
        inside, release, done = threading.Event(), threading.Event(), threading.Event()

        def hold():
            with scope.immediate():
                scope._append_evidence(venture, "first", "", None)
                inside.set()
                assert release.wait(5)
                raise RuntimeError("the first thread's transaction is rolled back")

        holder = threading.Thread(target=lambda: pytest.raises(RuntimeError, hold))
        holder.start()
        assert inside.wait(5)
        writer = threading.Thread(target=lambda: (scope.record(venture, "second"), done.set()))
        writer.start()
        assert not done.wait(0.5), "a write on the shared connection did not wait for the open transaction"
        release.set()
        holder.join(5)
        writer.join(5)
        assert [e["kind"] for e in scope.evidence(venture)] == ["created", "second"]    # nothing of the first was committed
    finally:
        store.close()


# 2. One reader of a stored amount (review wks3, finding 3) -----------------------------------------------------------------------

_AGGREGATE = re.compile(r"\b(SUM|TOTAL|AVG|MIN|MAX)\s*\([^)]*\bamount\b", re.I)


def test_every_amount_read_from_the_store_is_read_by_the_one_function():
    """Listed from the code: every read of a row's ``amount``, and every statement that would sum amounts in SQL, where a
    text reads as zero. Each read is the argument of ``stored_amount``; no statement sums an amount (failure_read_as_success)."""
    reads, loose = [], []
    for name, tree in _store_modules().items():
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _AGGREGATE.search(node.value):
                loose.append(f"{name}.{_enclosing(tree, node)}:{node.lineno}: sums amounts in SQL")
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "amount":
                where = f"{name}.{_enclosing(tree, node)}:{node.lineno}: {ast.unparse(node)}"
                reads.append(where)
                parent = parents.get(node)
                if not (isinstance(parent, ast.Call) and _called(parent) == "stored_amount" and parent.args[0] is node):
                    loose.append(where)
    functions = {line.split(":", 1)[0] for line in reads}
    assert {"g6.operator.store.week_held", "g6.operator.store._month", "g6.operator.store._old_spans",
            "g6.operator.store._settle"} <= functions, sorted(functions)
    assert not loose, loose


def test_the_one_reader_of_an_amount_admits_only_a_finite_number_of_zero_or_more():
    from g6.operator.store import stored_amount

    assert [stored_amount(v) for v in (0, 0.0, 3, 1.5)] == [0.0, 0.0, 3.0, 1.5]
    for bad in ("1", "unreadable", b"1", None, True, False, -1, -0.5, float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError):
            stored_amount(bad)


# 2. One check of an account's figure, however a reading is made (review wks4, finding 1) -----------------------------------------

def test_every_figure_of_an_account_reading_goes_through_the_one_check():
    """Listed from the code: every number field of ``AccountReading`` is read by ``figure_of`` where the reading is made, and
    the tool's percentage and window length are read by it too; nothing in ``usage`` checks a figure by a rule of its own
    (failure_read_as_success, met again in review wks4)."""
    trees = _trees()
    reading = next(n for n in ast.walk(trees["g6.self_maintenance.pacing"])
                   if isinstance(n, ast.ClassDef) and n.name == "AccountReading")
    numbers = {s.target.id for s in reading.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
               and ast.unparse(s.annotation) in ("float", "int", "float | int")}
    post = next(f for f in reading.body if isinstance(f, ast.FunctionDef) and f.name == "__post_init__")
    checked = {ast.unparse(c.args[0]) for c in ast.walk(post) if isinstance(c, ast.Call) and _called(c) == "figure_of"}
    assert numbers == {"used"}, numbers
    assert {f"self.{name}" for name in numbers} <= checked, checked
    usage_tree = trees["g6.self_maintenance.usage"]
    reads = next(f for f in _functions(usage_tree) if f.name == "_window")         # every window of the reply, once
    assert {ast.unparse(c.args[0]) for c in ast.walk(reads) if isinstance(c, ast.Call) and _called(c) == "figure_of"} \
        == {"percent", "minutes"}
    own = [f"{n.lineno}: {ast.unparse(n)}" for n in ast.walk(usage_tree) if isinstance(n, ast.Call) and _called(n) == "isfinite"]
    assert not own, own


def test_the_one_check_of_a_figure():
    from g6.self_maintenance.pacing import figure_of

    assert [figure_of(v) for v in (0, 0.0, 7, 2.5, 10 ** 300)] == [0.0, 0.0, 7.0, 2.5, float(10 ** 300)]
    assert figure_of(100, 100) == 100.0 and figure_of(100.0001, 100) is None
    for bad in (float("nan"), float("inf"), float("-inf"), -1, -1e-300, 10 ** 400, True, False, None, "1", b"1", [1]):
        assert figure_of(bad) is None, bad


# 1. The store's stamps never go backwards (review wks4, finding 3) -----------------------------------------------------------------

def test_every_settlement_stamp_is_written_over_the_floor_the_store_already_holds():
    """Listed from the code: the statements that stamp a settlement. There is one, ``_stamp``'s, which takes the floor of
    what the store already recorded for the provider (``_kept_times``), and both ways a settlement is dated go through it: one
    settled now (``_settle``) and a charge found with no span (``_date_old_charge``). Review wksfu2's finding 11 was the second
    writing its own stamp with no floor: such a charge is not always in the past of what is recorded, as the operator's own
    ``reserve`` still writes a reservation with no span."""
    trees = _package_trees()
    stamps = sorted({(name, _enclosing(tree, node)) for name, tree in trees.items() for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)
                     and re.search(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+week_settled\b", node.value, re.I)})
    assert stamps == [("g6.operator.store", "_stamp")], stamps
    functions = {f.name: ast.unparse(f) for f in _functions(trees["g6.operator.store"])}
    assert "_kept_times(" in functions["_stamp"]
    callers = sorted(name for name, text in functions.items() if "self._stamp(" in text)
    assert callers == ["_date_old_charge", "_settle"], callers


# 4. A caller's value is converted once, where it enters, and only that is used (review wksfu1) ------------------------------------

class _Sly(float):
    """A number whose every sum is 0 and every comparison says there is room, after any check; it holds its value."""

    def _zero(self, *_):
        return 0.0

    __add__ = __radd__ = __mul__ = __rmul__ = __sub__ = __rsub__ = _zero

    def __gt__(self, other):
        return False

    def __lt__(self, other):
        return True


class _FalseText(str):
    def __bool__(self):
        return False


class _OwnMoment(datetime):
    pass


def _of_its_own(annotation: str):
    """A value of a caller's class for a field of this type."""
    if "datetime" in annotation:
        return _OwnMoment(2026, 11, 1, tzinfo=UTC)
    return {"float": _Sly(5), "bool": 1, "str": _FalseText("why")}[annotation]


def _plain(value) -> bool:
    return type(value) in (float, bool, str, type(None)) or (type(value) is datetime and value.tzinfo is UTC)


def _fields(tree, name) -> dict[str, str]:
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name)
    return {s.target.id: ast.unparse(s.annotation) for s in node.body if isinstance(s, ast.AnnAssign)
            and isinstance(s.target, ast.Name)}


def test_every_field_of_a_reading_is_a_plain_value_once_it_is_made():
    """Listed from the code: every field of ``AccountReading``. Made with a value of a caller's class in any one of them,
    every field of the reading is a plain ``float``, ``bool``, ``str``, ``None`` or a plain ``datetime`` in UTC: nothing of
    the caller's object is kept to answer an operation its own way."""
    fields = _fields(_trees()["g6.self_maintenance.pacing"], "AccountReading")
    assert set(fields) == {"used", "estimated", "limited_until", "read_at", "resets_at", "ended_at", "unread"}, fields
    for name, annotation in fields.items():
        reading = AccountReading(**{name: _of_its_own(annotation.split(" | ")[0])})
        assert all(_plain(getattr(reading, f)) for f in fields), (name, reading)
    plain = AccountReading(used=_Sly(5), estimated=False, unread=_FalseText(""))
    assert (plain.used, plain.unread, plain.estimated) == (5.0, "", False) and _plain(plain.used) and _plain(plain.unread)


def _amount_entries() -> set[str]:
    """Every public method of the meter and of the store's owner scope that takes an amount (a parameter typed ``float``,
    or caps of them; a callback that is handed amounts takes none), and every field of a provider window that holds one."""
    trees = _trees()
    found = set()
    for module, name in (("g6.self_maintenance.pacing", "BudgetMeter"), ("g6.operator.store", "OwnerScope")):
        node = next(n for n in ast.walk(trees[module]) if isinstance(n, ast.ClassDef) and n.name == name)
        for f in node.body:
            if isinstance(f, ast.FunctionDef) and not f.name.startswith("_"):
                found |= {f"{name}.{f.name}({a.arg})" for a in (*f.args.args, *f.args.kwonlyargs)
                          if a.annotation is not None and "float" in ast.unparse(a.annotation)
                          and not ast.unparse(a.annotation).startswith("Callable")}
    found |= {f"ProviderWindow.{f}" for f, annotation in _fields(trees["g6.self_maintenance.pacing"], "ProviderWindow").items()
              if annotation == "float"}
    return found


def test_every_amount_given_reaches_the_ceiling_and_the_store_as_one_plain_float(tmp_path, monkeypatch):
    """Listed from the code: every entry that takes an amount. Each is given a number of a caller's class whose sums and
    comparisons say there is room, and what reaches the ceiling check (the operator's ``spend_refusal`` and the meter's own),
    ``reserve_span``, ``_settle`` and the ledger's rows is a plain ``float`` every time, and the ceiling holds."""
    entries = _amount_entries()
    assert entries == {"BudgetMeter.reserve(worst_case)", "BudgetMeter.settle(used)", "OwnerScope.reserve(amount)",
                       "OwnerScope.reserve_span(amount)", "OwnerScope.reserve_span(caps)", "OwnerScope.settle(reported)",
                       "OwnerScope.settle(metered)", "OwnerScope.create_venture(caps)", "OwnerScope.venture_named(caps)",
                       "ProviderWindow.allowance", "ProviderWindow.reserve_share"}, sorted(entries)
    from g6.operator import store as store_module

    seen: list[tuple[str, type]] = []
    now = datetime(2026, 11, 7, tzinfo=UTC)
    store = OperatorStore(tmp_path / "op.db", clock=lambda: now)
    try:
        window = ProviderWindow("codex", _Sly(100), datetime(2026, 11, 1, tzinfo=UTC), reserve_share=_Sly(0.0))
        assert _plain(window.allowance) and _plain(window.reserve_share)
        meter = BudgetMeter((window,), store=store, read_account=lambda *_: AccountReading(used=90, estimated=False))
        scope = meter.scope
        for name in ("_append_ledger", "_settle", "reserve_span"):
            real = getattr(scope, name)

            def noted(*args, real=real, name=name, **kwargs):
                seen.extend((name, type(v)) for v in (*args, *kwargs.values()) if isinstance(v, int | float))
                return real(*args, **kwargs)

            monkeypatch.setattr(scope, name, noted)
        refusal = store_module.gates.spend_refusal
        monkeypatch.setattr(store_module.gates, "spend_refusal", lambda venture, committed, account, amount: (
            seen.append(("spend_refusal", type(amount))), refusal(venture, committed, account, amount))[1])
        assert not meter.reserve("codex", now, _Sly(20)).admitted                      # 90 + 20 is over 100
        spend = meter.reserve("codex", now, _Sly(10))
        assert spend.admitted and _plain(spend.amount)
        assert meter.settle(spend, _Sly(10)) == 10.0
        venture = scope.create_venture("monthly", caps={"model": _Sly(50), "cash": _Sly(0)})["venture_id"]
        assert scope.venture(venture)["caps"] == {"model": 50.0, "cash": 0.0}
        named = scope.venture_named("named", caps={"model": _Sly(7), "cash": _Sly(0)})
        assert scope.venture(named)["caps"] == {"model": 7.0, "cash": 0.0}
        held = scope.reserve(venture, month="2026-11", account="model", amount=_Sly(30))
        with pytest.raises(store_module.BudgetRefused):
            scope.reserve(venture, month="2026-11", account="model", amount=_Sly(30))        # 30 + 30 is over 50
        assert scope.settle(venture, held, reported=_Sly(40)) == 40.0
    finally:
        store.close()
    assert {name for name, _ in seen} >= {"_append_ledger", "_settle", "reserve_span", "spend_refusal"}, seen
    assert all(kind is float for _, kind in seen), [entry for entry in seen if entry[1] is not float]


def test_every_use_of_what_reads_the_account_goes_through_the_one_reader():
    """Listed from the code: every call of a ``read_account`` the meter was given, anywhere in the package. Each is in
    ``BudgetMeter._account``, the one step that makes anything but a reading, and any failure, an unread reading
    (one_fact_two_rules: finding 3 of review wksfu1 was scheduling reading it raw)."""
    calls, outside = [], []
    for name, tree in _package_trees().items():
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_read_account" and isinstance(node.ctx, ast.Load):
                where = f"{name}.{_inside(node, parents)}:{node.lineno}"
                calls.append(where)
                if (name, _inside(node, parents)) != ("g6.self_maintenance.pacing", "_account"):
                    outside.append(where)
    assert calls, "the inventory found no use of read_account at all"
    assert not outside, outside
