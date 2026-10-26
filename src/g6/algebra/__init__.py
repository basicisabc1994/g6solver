# SPDX-License-Identifier: Apache-2.0
"""g6.algebra — computer algebra with SymPy: simplify, factor, solve, differentiate, integrate and more, every answer re-checked independently before it is reported.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the computer algebra
one, SymPy in process and SageMath only as an optional external program.

Capability label: ``implemented``.

    from g6.algebra import differentiate, integrate, solve
    r = differentiate("sin(x)^2", "x")
    r.answer, r.completion_state, r.recheck["status"]       # ("2*sin(x)*cos(x)", "qualified-draft", "passed")
    integrate("sin(x)", "x", lower=0, upper="pi").answer   # "2", re-checked by numeric quadrature
    s = solve("x^2 - 5*x + 6 = 0", unknowns=["x"])
    [a["values"]["x"] for a in s.answers], s.complete       # (["2", "3"], True)

What it reads. SymPy's string readers evaluate their input as Python, so caller text reaches nothing but G6's own
parser (:mod:`g6.algebra.language`): numbers (decimals read as exact rationals), declared symbols with optional
assumptions (``real``, ``positive``, ``integer``), ``+ - * / ^`` (or ``**``), parentheses, a fixed list of functions
(sin, cos, tan, cot, sec, csc, asin, acos, atan, sinh, cosh, tanh, asinh, acosh, atanh, exp, log, sqrt, root, abs,
sign, floor, ceiling, re, im, conjugate, factorial, binomial) and the constants pi, E and I. Everything else (strings,
attributes, brackets, keywords, unknown names) is refused with its code, the line and the column, and size limits
refuse an expression that would make SymPy compute without end (``2^(10^10)``, ``factorial(10^6)``). The SymPy objects
are built from G6's parse tree through SymPy's constructors (:mod:`g6.algebra.build`).

What it does (``pip install 'g6[cas]'``; SymPy is imported only in G6's worker): ``simplify``, ``expand``, ``factor``,
``differentiate``, ``integrate`` (indefinite, or definite with bounds that may be ``oo``), ``solve`` (one equation, or
a small system), ``limit``, ``series``, ``determinant``, ``inverse``, ``eigenvalues``, ``factor_integer``,
``is_prime`` and ``gcd``. Answers come back as text in SymPy's canonical form and as LaTeX.

How each answer is re-checked (:mod:`g6.algebra.checks`, :mod:`g6.algebra.integers`), before it is reported: the
original is evaluated by G6's own evaluator on mpmath from G6's parse tree, never by SymPy, and compared with SymPy's
answer at seeded random points (complex where a symbol has no assumption), skipping points near a singularity; where
the input is rational, also exactly in fractions at random integer points. A simplification must agree there and
simplify to zero against the original; an expansion or factorisation must expand back to it; a derivative must agree
with numeric differentiation; an antiderivative must differentiate back to the integrand, and a definite integral agree
with numeric quadrature; every solution must satisfy every equation when substituted back, and ``solve`` says whether
the set is complete, as SymPy's solvers report; a limit must be approached numerically; series coefficients must
match the derivatives; an inverse must multiply back to the identity and each eigenvalue make ``det(A - lambda I)``
vanish; a factorisation must multiply back with every factor prime by G6's own test; ``is_prime`` says whether SymPy's
answer is definitive at that size (below 2^64), and a greatest common divisor is shown by Bezout coefficients. A
failed re-check blocks the answer (``G6_E_ALGEBRA_CERTIFICATE_REFUTED``); a re-check that cannot decide reports the
answer as not re-checked (``G6_E_ALGEBRA_NOT_RECHECKED``), never as passed.

Time limits. Each SymPy op runs in G6's own worker process (``python -I -m g6.algebra.worker``) under
:mod:`g6.core.component_process`: a time limit (30 seconds by default, at most 120), a memory cap (none on macOS, which
refuses it) and an output cap,
its process tree killed when one is passed (:mod:`g6.algebra.runner`). The worker reports its answer before it
re-checks, so an answer whose re-check runs out of time is reported as not re-checked. Caller text reaches nothing in
the worker but G6's parser, so the SymPy ops are not sensitive: they answer over MCP.

SageMath (:mod:`g6.algebra.sage`) is GPL, so G6 never installs, bundles, vendors or imports it: you install it from
sagemath.org, and G6 runs the ``sage`` that ``G6_SAGE`` names or that is on PATH. ``sage_evaluate`` screens one
expression in SageMath's syntax through G6's parser with a fixed table of SageMath functions, and SageMath reads only
G6's rendering of the parse tree, out of process with the same caps (120 seconds by default, at most 600); its answer
is not re-checked, and says so. ``sage_version`` reads ``sage --version``. Both are sensitive: they run from Python, or
over MCP only when the person running the server lists them in ``G6_MCP_SENSITIVE``.

The best state is ``qualified-draft``: a computer algebra system's answer on your input, re-checked or not, is not a
proof a person has inspected, so nothing here is ``verified``. Codes are ``G6_E_ALGEBRA_*``.

Not ported from the archive's ``sagemath_mcp``, and why:

* ``run_code``, which executed caller Python in process, and ``compute``, which passed caller text to ``sage -c``;
* the container transport, and the check for its image that ran when the module was imported;
* the pure-Python fallbacks that answered when SageMath was absent (trial-division factoring, a three-point polynomial
  solver, a formula evaluator), marked only by a prefix on the text: SymPy now answers, re-checked, and SageMath
  answers only for SageMath;
* the group theory, graph theory, combinatorics and polynomial ring ops, which assembled SageMath code from caller
  fields; and the result store, ``compare_results``, ``search``, ``inspect_object``, ``explain_computation``,
  ``latex_render`` and ``export_sage``, which did no algebra;
* the stale install hints (system package managers and image pulls).
"""
from g6.core.outside import OutsideProgram
from g6.algebra import inputs, integers, language, ops, runner, sage
from g6.algebra.algebra_block import SENSITIVE_OPS, AlgebraBlock, AlgebraInput
from g6.algebra.envelope import INSTALL_CAS, INSTALL_SAGE, SAGE_ENV, Refusal, finish
from g6.algebra.language import declare, parse, render
from g6.algebra.ops import (
    determinant,
    differentiate,
    eigenvalues,
    expand,
    factor,
    factor_integer,
    gcd,
    integrate,
    inverse,
    is_prime,
    limit,
    series,
    simplify,
    solve,
)
from g6.algebra.sage import sage_evaluate, sage_version
from g6.algebra.schema import AlgebraResult, Recheck

__g6_component__ = True
__g6_block__ = "AlgebraBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they run SageMath, a program on your machine
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("wrong_symbolic_result",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (OutsideProgram(
    name="SageMath", purpose="sage_evaluate and sage_version run SageMath's own algebra", install=INSTALL_SAGE,
    licence="GPL (so G6 never bundles, imports or installs it)", command="sage", env=SAGE_ENV),)
__g6_extras__: dict[str, tuple[str, ...]] = {"cas": ("sympy",)}

__all__ = ["INSTALL_CAS", "INSTALL_SAGE", "SAGE_ENV", "SENSITIVE_OPS", "AlgebraBlock", "AlgebraInput", "AlgebraResult",
           "Recheck", "Refusal", "declare", "determinant", "differentiate", "eigenvalues", "expand", "factor",
           "factor_integer", "finish", "gcd", "inputs", "integers", "integrate", "inverse", "is_prime", "language",
           "limit", "ops", "parse", "render", "runner", "sage", "sage_evaluate", "sage_version", "series", "simplify",
           "solve"]
