# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Would the customer's tests notice if their code were wrong?

Founder, 2026-09-24, on the run_tests result card: the alternatives and evidence
lines must be real and "actually bring real value to the end user", not be
filled in for theatre. Measured before this module: a run_tests card recorded
``evidence_sources: 0`` for a run that DID execute, and no alternatives at all.

A passing test run says the tests passed. It does not say the tests would have
caught a mistake -- an agent can write tests that pass whatever the code does.
So G6 makes a few small, deliberate changes to the customer's code (``a < b``
to ``a <= b``, ``+`` to ``-``, a return value to ``None`` ...), runs the
customer's OWN tests against each changed version, and records what happened:

* a changed version the tests FAIL is an alternative implementation that was
  executed and rejected -- the card's "alternative approaches rejected";
* a changed version the tests still PASS is named, by line and change, as a
  gap: the tests do not check that behaviour. The next step says which test to
  add. (A change can occasionally be equivalent to the original; the gap says
  so rather than calling the tests wrong.)

Nothing here is estimated: every count is a run that executed. A run the
sandbox did not execute (busy, refused) is not counted either way, and the
check stops there. No LLM, no platform spend: sandbox time only, charged to the
customer's own test-runner allowance.

Python only; standard library only.
"""
from __future__ import annotations

import ast
import copy
import itertools
import math
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

#: Changed versions tried per check. Each one is a full run of the customer's tests.
MAX_MUTANTS = 6
_SNIPPET = 80

_CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
             ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv}
_BOOL_SWAP = {ast.And: ast.Or, ast.Or: ast.And}
#: The executor's labels for a run whose tests did not pass the changed code.
_REJECTING_REASONS = {"timed_out": "the tests timed out", "collection_error": "the tests could not import it"}


@dataclass(frozen=True)
class Mutant:
    line: int
    before: str
    after: str
    source: str

    def describe(self) -> str:
        return f"line {self.line}: `{self.before}` -> `{self.after}`"


def _snip(node: ast.AST) -> str:
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= _SNIPPET else text[: _SNIPPET - 1] + "…"


def _transform(node: ast.AST, calls: bool = False) -> ast.AST | None:
    """The one change this node admits (applied to a copy), or None. ``calls``: a call on one positional argument
    becomes that argument (``list(items)`` -> ``items``)."""
    if (calls and isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords
            and not isinstance(node.args[0], ast.Starred)):
        return node.args[0]
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP_SWAP:
        node.ops = [_CMP_SWAP[type(node.ops[0])]()]
        return node
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_SWAP:
        node.op = _BIN_SWAP[type(node.op)]()
        return node
    if isinstance(node, ast.BoolOp) and type(node.op) in _BOOL_SWAP:
        node.op = _BOOL_SWAP[type(node.op)]()
        return node
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return node.operand
    if isinstance(node, ast.Constant) and type(node.value) is int:  # never text: docstrings stay
        node.value = node.value + 1
        return node
    if isinstance(node, ast.Return) and node.value is not None and not (
            isinstance(node.value, ast.Constant) and node.value.value is None):
        node.value = ast.Constant(value=None)
        return node
    return None


def _on(node: ast.AST, lines: Collection[int] | None) -> bool:
    """Whether ``node`` is on ``lines`` (every node is when None): its first line,
    or, for an operator (a BinOp, a Compare, a BoolOp), any line between two of
    its operands, where the operator itself sits in a multi-line expression."""
    if lines is None or node.lineno in lines:
        return True
    if isinstance(node, ast.BinOp):
        operands = [node.left, node.right]
    elif isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
    elif isinstance(node, ast.BoolOp):
        operands = list(node.values)
    else:
        return False
    return any(line in lines for first, then in itertools.pairwise(operands)
               for line in range(first.end_lineno or first.lineno, then.lineno + 1))


def mutants(code: str, limit: int = MAX_MUTANTS, lines: Collection[int] | None = None, *,
            calls: bool = False) -> list[Mutant]:
    """Up to ``limit`` distinct single-change versions of ``code``, spread
    evenly across it, in source order; only on ``lines`` when given (a
    repository's changed lines, plan 2026-10-05-verify-task-real-repos; an
    operator counts on the lines between its operands, ``_on``). ``calls``
    adds one change, a call on one argument to its argument (RVC R6.7: a
    mission certificate's controls). Unparseable code has none."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    original = ast.unparse(tree)
    nodes = list(ast.walk(tree))
    found: list[Mutant] = []
    seen = {original}
    for index, node in enumerate(nodes):
        if not hasattr(node, "lineno") or not _on(node, lines):
            continue
        copy_tree = copy.deepcopy(tree)
        target = list(ast.walk(copy_tree))[index]
        before = _snip(target)
        replacement = _transform(target, calls)
        if replacement is None:
            continue
        if replacement is not target:  # the node itself is replaced (``not x`` -> ``x``)
            for parent in ast.walk(copy_tree):
                for field, value in ast.iter_fields(parent):
                    if value is target:
                        setattr(parent, field, replacement)
                    elif isinstance(value, list):
                        setattr(parent, field, [replacement if v is target else v for v in value])
        try:
            source = ast.unparse(ast.fix_missing_locations(copy_tree))
            compile(source, "<mutant>", "exec")
        except (SyntaxError, ValueError, TypeError):
            continue
        if source in seen:
            continue
        seen.add(source)
        found.append(Mutant(node.lineno, before, _snip(replacement), source))
    found.sort(key=lambda m: m.line)
    if len(found) <= limit:
        return found
    step = len(found) / limit
    return [found[math.floor(i * step)] for i in range(limit)]


RunFn = Callable[[str, int], Any]  # (changed code, timeout_s) -> TestExecutionResult


def check(code: str, run: RunFn, *, budget_s: float, base_duration_s: float,
          limit: int = MAX_MUTANTS, clock: Callable[[], float] = time.monotonic,
          lines: Collection[int] | None = None) -> dict:
    """Run the customer's tests against changed versions of their code.

    ``run`` executes the tests against the given code (the caller binds the
    tests and the sandbox). ``budget_s`` bounds the whole check's wall time.
    ``lines``: only changes on these lines (``mutants``).
    Returns the record the run_tests payload carries as ``mutation_check``.
    """
    candidates = mutants(code, limit, lines)
    record: dict[str, Any] = {"ran": False, "tried": 0, "rejected": 0, "survived": [],
                              "rejections": [], "execution_ids": [], "stopped": ""}
    if not candidates:
        record["stopped"] = "G6 found nothing in this code it could change and still run"
        return record
    per_run = max(2, math.ceil(base_duration_s * 3) + 5)
    started = clock()
    for mutant in candidates:
        remaining = budget_s - (clock() - started)
        if remaining < per_run:
            record["stopped"] = "the time budget for this check ran out"
            break
        result = run(mutant.source, int(min(per_run, remaining)))
        executed = bool(getattr(result, "execution_id", ""))
        reason = getattr(result, "reason", "") or ""
        if executed:
            record["execution_ids"].append(result.execution_id)
            caught = result.failed > 0 or getattr(result, "not_passed", 0) > 0 or result.passed == 0
            how = f"{result.failed} of your tests failed" if result.failed else "your tests did not pass"
        elif reason in _REJECTING_REASONS:
            caught, how = True, _REJECTING_REASONS[reason]
        else:
            record["stopped"] = f"the sandbox did not run a changed version ({reason or 'no execution'})"
            break
        record["tried"] += 1
        if caught:
            record["rejected"] += 1
            record["rejections"].append(f"{mutant.describe()} ({how})")
        else:
            record["survived"].append(mutant.describe())
    record["ran"] = record["tried"] > 0
    return record


def limitations(record: dict) -> list[str]:
    """Card lines for the changes the customer's tests did not catch."""
    return [f"Your tests still passed when G6 changed {s}; no test checks that behaviour "
            "(or the change happens to be equivalent)." for s in record.get("survived") or []]
