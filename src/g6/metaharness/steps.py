# SPDX-License-Identifier: Apache-2.0
"""Built-in pipeline steps for recovery remedies (DECISIONS #27).

A person declares one in ``Workflow.pipeline_steps`` (it is part of what they confirm); the meta-harness can then try
it like any other profile option (``Profile(pipeline=[name])``), and the gate judges it on held-out cases at equal
compute. Each step is a bounded multi-call procedure: every extra model call goes through the profile's metered caller
(``g6.reasoning``), so its cost shows in the comparison, and a spent budget keeps the answer it has.

* ``repair_with_tests``: runs the example tests the task text shows on the answer's Python code, out of process
  (``g6.core.sandbox``), and gives the model each failure to fix, a few rounds at most. It never sees a hidden test.
* ``second_look``: when the answer is a label that the dev failures show is over-used, asks the model once more to
  confirm it against the input or choose another listed label.
"""
from __future__ import annotations

import ast
import re
import warnings
import textwrap
from collections.abc import Sequence
from typing import Any

from g6.metaharness.schema import PipelineStep

from g6.core.markdown import parse
PRELUDE = "import sys\nsys.path.insert(0, '.')\n"
REPAIR_SYSTEM = "You fix Python code. Reply with the complete corrected code in one Python code block, and nothing else."


def _caller(ctx: Any) -> Any:
    return ctx.caller(next(iter(ctx.callers)))


def python_code(text: str) -> str:
    """The first fenced block that defines a function, else the first fenced block, else the whole reply."""
    blocks = [b.content for b in parse(text or "").code_blocks]
    return next((b for b in blocks if _has_function(b)), blocks[0] if blocks else (text or ""))


def _has_function(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return False
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))


def shown_tests(task: str) -> tuple[list[str], list[str]]:
    """Shown imports and assertions, parsed as Python statements, never run here: wherever they stand, inside a shown
    ``def test_...():`` too (the line reading this replaced took each such line, so that is the floor). A code block
    that does not parse whole is read line by line, as the prose around the blocks is."""
    doc = parse(task or "")
    regions = [b.content for b in doc.code_blocks]
    rest = task or ""
    for block in reversed(doc.code_blocks):
        rest = rest[:block.start] + "\n" + rest[block.end:]
    regions += rest.splitlines()
    imports, assertions = [], []
    while regions:
        source = textwrap.dedent(regions.pop(0))
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            if "\n" in source.strip():
                regions[:0] = source.splitlines()
            continue
        shown = (n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom, ast.Assert)))
        for node in sorted(shown, key=lambda n: (n.lineno, n.col_offset)):   # in the order the task shows them
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(ast.get_source_segment(source, node))
            elif isinstance(node, ast.Assert):
                assertions.append(ast.get_source_segment(source, node))
    return imports, assertions


def run_shown(code: str, imports: Sequence[str], asserts: Sequence[str], *, timeout_s: float) -> str:
    """'' when the code parses, defines a function and passes the shown asserts out of process; else the failure. A
    run no operating-system boundary could confine, where isolation is required, raises ``confine.IsolationRefused``:
    the code never ran, so there is no failure for the model to fix."""
    if not _has_function(code):
        return "the reply has no Python function"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ast.parse(code)                      # parsed, never run in process
    except (SyntaxError, ValueError) as exc:
        return f"SyntaxError: {exc}"
    from g6.core.confine import IsolationRefused
    from g6.core.sandbox import isolation_refused, run_script

    source = PRELUDE + "".join(f"{i}\n" for i in imports) + "from solution import *\n" + "".join(f"{a}\n" for a in asserts)
    ran = run_script(source, {"solution.py": code}, timeout_s=timeout_s, mem_mb=512)
    if isolation_refused(ran):
        raise IsolationRefused(ran.stderr, ran.isolation)
    return "" if ran.status == "ok" else (ran.stderr or ran.status)[-600:]


def repair_with_tests(rounds: int = 3, *, timeout_s: float = 10.0, name: str = "repair_with_tests") -> PipelineStep:
    """A step that runs the task's shown example tests and repairs a failing answer, at most ``rounds`` times."""
    if not 1 <= rounds <= 5:
        raise ValueError("rounds must be 1 to 5")

    def run(output: str, contract: Any, ctx: Any) -> str:
        imports, asserts = shown_tests(contract.task)
        if not asserts:
            return output
        for _ in range(rounds):
            error = run_shown(python_code(output), imports, asserts, timeout_s=timeout_s)
            if not error:
                return output
            try:
                fixed = _caller(ctx).complete(REPAIR_SYSTEM, f"TASK:\n{contract.task}\n\nCODE:\n{output}\n\n"
                                                             f"It fails the example test. Error:\n{error}")
            except Exception:  # noqa: BLE001 - a spent budget or a failed call keeps the answer it has
                return output
            if not fixed.strip():
                return output
            output = fixed
        return output

    return PipelineStep(name, "reasoning", "test_repair", run, authority="A2", effect="read")


def _label_pattern(label: str) -> re.Pattern[str]:
    body = r"[ _\-]+".join(re.escape(w) for w in re.split(r"[ _\-]+", label.strip()))
    return re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", re.IGNORECASE)


def labels_named(text: str, labels: Sequence[str]) -> list[str]:
    """The listed labels the text names (a label inside a longer named label does not count)."""
    found = [lab for lab in labels if _label_pattern(lab).search(text or "")]
    return [lab for lab in found if not any(lab != other and _label_pattern(lab).search(other) for other in found)]


def second_look(label: str, labels: Sequence[str], *, question: str = "", name: str = "") -> PipelineStep:
    """A step that, when the answer is ``label``, asks once more and keeps a revision naming exactly one label."""
    if label not in labels:
        raise ValueError("label must be one of labels")
    system = question or (f"A first reading of this task gave the answer {label}. Check it against the task's input "
                          f"alone. Reply {label} only if the input directly shows it; otherwise reply with the one "
                          f"label from this list that does apply: {', '.join(labels)}. Reply with the label only.")

    def run(output: str, contract: Any, ctx: Any) -> str:
        if labels_named(output, labels) != [label]:
            return output
        try:
            revised = _caller(ctx).complete(system, contract.task)
        except Exception:  # noqa: BLE001 - a spent budget or a failed call keeps the answer it has
            return output
        return revised if len(labels_named(revised, labels)) == 1 else output

    return PipelineStep(name or f"second_look_{re.sub(r'[^A-Za-z0-9]+', '_', label).lower()}", "reasoning",
                        "second_look", run, authority="A2", effect="read")


__all__ = ["REPAIR_SYSTEM", "labels_named", "python_code", "repair_with_tests", "run_shown", "second_look", "shown_tests"]
