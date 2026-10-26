# SPDX-License-Identifier: Apache-2.0
"""g6.cegis — fill the holes in a code sketch by counterexample-guided synthesis, running every candidate out of process.

g6.cegis — counterexample-guided synthesis of sketch holes, with candidates run out of process (audit 027 §11).

Capability label: ``implemented``.

    sketch = Sketch.from_src("def f(x):\\n    return x * ?? + ??\\n")
    result = cegis(sketch, lambda x: 3 * x + 1, domain=InputDomain.ints(["x"], -10, 10))
    result.status        # "no_counterexample_in_domain"
    result.program       # the filled-in function
    result.to_envelope() # qualified-draft at most, with the boundary it rests on

What it is: ``??`` / ``??b`` / ``??{"a","b"}`` holes; an exhaustive search (or a seeded hill-climb on large spaces);
a check of each candidate on an explicit input domain; the loop. Every candidate runs in a child process
(``SubprocessRunner``) with an AST pre-filter (``g6.core.source_check``, shared with ``g6.distillation``), a small
builtins table, a wall-clock allowance and a whole-tree kill; a memory cap (POSIX rlimit, a Windows Job Object), and on
POSIX also CPU and file-size limits.

An optional ``verifier`` (the ``Verifier`` protocol) checks a candidate the sampled check passed. ``Z3Verifier(spec)``
asks Z3, through ``g6.formal`` (``pip install 'g6[formal]'``; imported only when a check runs), for an input of the
whole domain on which the candidate breaks ``spec``, an SMT-LIB term over the arguments and ``result``. It answers
``Counterexample`` (fed back only after the candidate, run out of process, disagrees with the oracle there),
``NoCounterexample`` (a solver decision on your spec, so ``qualified-draft`` at most) or ``Inconclusive``: degraded,
with ``z3_unknown``, ``z3_timeout``, ``z3_unavailable`` or ``z3_untranslatable``, and never read as a pass (CG-008).

Limits: the child is not an operating-system sandbox (it runs as the same user); a result holds only on the
domain it names; the oracle is your own callable and runs in your process; Z3 reads only int and bool arithmetic,
comparisons and conditionals. Not ported from the archive: the ``cegis_block`` spec-expression path (CG-003: it
verified a spec against itself), the LLM agency legs, the MCP servers, the algorithm catalogue, the Z3 synthesizer (the
out-of-process exhaustive search fills the holes; a Z3 synthesis leg is not ported) and the Rosette synthesizer and
verifier (Racket is not installed where this was ported, so no Rosette disclosure path could be tested).
"""
from g6.cegis.hole import Hole, HoleKind
from g6.cegis.loop import CegisResult, cegis
from g6.cegis.runner import Runner, RunnerError, RunnerTimeout, SearchResult, SubprocessRunner
from g6.cegis.sketch import Control, Sketch
from g6.cegis.verify import (
    Counterexample,
    Inconclusive,
    InputDomain,
    NoCounterexample,
    Oracle,
    Outcome,
    TableOracle,
    Verifier,
)
from g6.cegis.z3_verifier import Z3Verifier
from g6.cegis.templates import TemplateFit, fit_template

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ("formal", "distillation")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("overfit_to_examples",)
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"formal": ("z3",), "optim": ("numpy", "scipy")}

__all__ = ["CegisResult", "Control", "Counterexample", "Hole", "HoleKind", "Inconclusive", "InputDomain",
           "NoCounterexample", "Oracle", "Outcome", "Runner", "RunnerError", "RunnerTimeout", "SearchResult", "Sketch",
           "SubprocessRunner", "TableOracle", "TemplateFit", "Verifier", "Z3Verifier", "cegis", "fit_template"]
