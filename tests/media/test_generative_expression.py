# SPDX-License-Identifier: Apache-2.0
"""Domain colouring takes a caller's expression in ``z``. It is parsed against an allow-list of syntax and walked by
G6's own evaluator: nothing is ever passed to ``eval``, ``exec`` or ``compile``, so the attribute walks that escape
emptied builtins (ADAPT_GENERATIVE_ART-02) are refused before anything runs, and a failed expression is a refusal,
never a silent render of ``z`` (ADAPT_GENERATIVE_ART-05)."""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

import g6.media.adapt_generative_art as package  # noqa: E402
from g6.media.adapt_generative_art.expression import ExpressionError, evaluate, parse  # noqa: E402

Z = np.array([0.5 + 0.25j, -1.0 + 2.0j, 3.0 - 0.5j])


@pytest.mark.parametrize(("text", "expected"), [
    ("z**2", Z**2),
    ("z**3 - 1", Z**3 - 1),
    ("(z - 1) / (z + 1)", (Z - 1) / (Z + 1)),
    ("sin(z) * exp(-z) + 2.5j", np.sin(Z) * np.exp(-Z) + 2.5j),
    ("conj(z) + abs(z) - pi * i", np.conj(Z) + np.abs(Z) - np.pi * 1j),
    ("-z + +z", -Z + Z),
    ("sqrt(log(z))", np.sqrt(np.log(Z))),
    ("real(z) + imag(z) * j", Z.real + Z.imag * 1j),
])
def test_an_allowed_expression_evaluates_as_numpy_would(text: str, expected) -> None:
    assert np.allclose(evaluate(parse(text), Z), expected)


@pytest.mark.parametrize("payload", [
    "().__class__.__bases__[0].__subclasses__()",          # the attribute walk that escapes emptied builtins
    "z.__class__.__mro__[-1].__subclasses__()",
    "sin.__globals__",
    "__import__('os').system('echo hacked')",
    "open('secret.txt')",
    "breakpoint()",
    "[c for c in ().__class__.__mro__]",
    "(lambda: 1)()",
    "z if z else 1",
    "z.real",
    "'text'",
    "exp(z, 2)",
    "sin(x=z)",
    "sin(*[z])",
    "z < 1",
    "z and 1",
    "x + y",
    "(w := 3)",
    "f'{z}'",
    "None",
    "True",
    "z[0]",
    "z @ z",
    "z // 2",
    "z % 2",
    "",
    "z +",
    "z" + " + z" * 200,
    "sin(" * 40 + "z" + ")" * 40,
])
def test_anything_outside_the_allow_list_is_refused_before_it_runs(payload: str) -> None:
    with pytest.raises(ExpressionError):
        parse(payload)


def test_a_huge_power_is_numpy_arithmetic_never_a_python_big_integer() -> None:
    started = time.monotonic()
    found = evaluate(parse("9**9**9**9 * z"), Z)
    assert time.monotonic() - started < 1.0 and not np.isfinite(found).any()


def test_the_package_never_calls_eval_exec_compile_or_starts_a_program() -> None:
    for path in Path(package.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert not {"eval", "exec", "compile", "__import__", "system", "Popen", "run_script"} & called, path.name
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not {"subprocess", "socket", "urllib", "http", "requests", "httpx"} & imported, path.name
