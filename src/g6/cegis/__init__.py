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
builtins table, a wall-clock allowance and a whole-tree kill; a memory cap (POSIX rlimit, a Windows Job Object; none on
macOS, which refuses the address-space limit), and on POSIX also CPU and file-size limits.

A ``??f`` floating-point hole has no candidate grid: it refuses with ``G6_E_CEGIS_HOLE_UNSUPPORTED``, a limit the
agentic override kernel may follow with a model proposal (``g6_agentic_run``), keeping the original answer beside it.
It records provider consent for remote admission, local model for local admission, allowed in code for process
callers, or refused by the consent check.
The block (``CegisBlock``, op ``synthesize``, sensitive) takes the oracle as a table of inputs and outputs.

An optional ``verifier`` (the ``Verifier`` protocol) checks a candidate the sampled check passed. ``Z3Verifier(spec)``
asks Z3, through ``g6.formal`` (``pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'``; imported only when a check runs), for an input of the
whole domain on which the candidate breaks ``spec``, an SMT-LIB term over the arguments and ``result``. It answers
``Counterexample`` (fed back only after the candidate, run out of process, disagrees with the oracle there),
``NoCounterexample`` (a solver decision on your spec, so ``qualified-draft`` at most) or ``Inconclusive``: degraded,
with ``z3_unknown``, ``z3_timeout``, ``z3_unavailable`` or ``z3_untranslatable``, and never read as a pass (CG-008).

Limits: the child runs inside an operating-system boundary only where ``g6.core.confine`` has one ready (elsewhere
as the same user; ``boundary["isolation"]`` says which); a result holds only on the
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
from g6.cegis.block import CegisBlock, CegisInput

__g6_component__ = True
__g6_maturity__ = "beta"
__g6_block__ = "CegisBlock"
# synthesize runs every candidate it tries, a program built from the caller's sketch
__g6_sensitive_ops__: tuple[str, ...] = ("synthesize",)
# The agentic override kernel (g6._agentic, design 10.5-10.8). A limit: a code that says what cegis cannot do, the one
# kind of refusal (with an exception inside its own code) an override may follow. A part: functions of cegis's own code
# the kernel may substitute for one call of the pure-computation ops named, with the validator that checks a value
# before it is used. The verifiers and the checker are cegis's own, never substituted: the checker re-checks a final
# answer in a fresh confined run. The seam runs candidates in process inside the kernel's confined child.
__g6_limits__: tuple[str, ...] = ("G6_E_CEGIS_HOLE_UNSUPPORTED",)
__g6_agentic_parts__: dict[str, dict] = {
    "float_grid": {"ops": ("synthesize",), "functions": ("g6.cegis.hole:float_grid",),
                   "validator": "g6.cegis.hole:valid_float_grid"},
}
__g6_verifiers__: tuple[str, ...] = ("g6.cegis.verify", "g6.cegis.z3_verifier", "g6.cegis.loop:_verifier_says",
                                     "g6.cegis.block:recheck")
__g6_agentic_check__ = "g6.cegis.block:recheck"
__g6_agentic_inside__ = "g6.cegis.runner:inside_kernel_child"
__g6_requires__: tuple[str, ...] = ("formal", "distillation")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("overfit_to_examples",)
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"formal": ("z3",), "optim": ("numpy", "scipy")}

__all__ = ["CegisBlock", "CegisInput", "CegisResult", "Control", "Counterexample", "Hole", "HoleKind", "Inconclusive",
           "InputDomain",
           "NoCounterexample", "Oracle", "Outcome", "Runner", "RunnerError", "RunnerTimeout", "SearchResult", "Sketch",
           "SubprocessRunner", "TableOracle", "TemplateFit", "Verifier", "Z3Verifier", "cegis", "fit_template"]

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_CEGIS_BUDGET_EXHAUSTED": "person",
    "G6_E_CEGIS_DOMAIN_BOUNDED": "none",
    "G6_E_CEGIS_HOLE_UNSUPPORTED": "none",      # a declared limit (__g6_limits__): the selector offers the override
    "G6_E_CEGIS_INCONCLUSIVE": "none",
    "G6_E_CEGIS_TEMPLATE_EXTRA": "person",
    "G6_E_CEGIS_TEMPLATE_FAILED": "none",
    "G6_E_CEGIS_TEMPLATE_INPUT": "input",
    "G6_E_CEGIS_TEMPLATE_LEAK": "none",
    "G6_E_CEGIS_TEMPLATE_TRAINING": "none",
    "G6_E_CEGIS_TIMEOUT": "person",
    "G6_E_CEGIS_UNSAT": "none",
    "G6_E_INPUT_VALIDATION": "input",
    "G6_E_ISOLATION_UNAVAILABLE": ("person", "No operating-system boundary can confine the run here. Tell the person: "
                                   "g6 doctor says what this computer needs. Never allow an unconfined run yourself.",
                                   "g6 doctor"),
}
