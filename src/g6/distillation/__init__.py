# SPDX-License-Identifier: Apache-2.0
"""g6.distillation — check and run a small distilled function, and test it on held-out cases before anyone relies on it.

g6.distillation — check and run a small distilled function, and test it on held-out cases before anyone trusts it
(audit 025 §11).

Capability label: ``implemented``.

    from g6.distillation import Candidate, HoldoutCase, evaluate
    report = evaluate(Candidate("def solve(x):\\n    return x[::-1]\\n", training_case_ids=("t1",)),
                      [HoldoutCase("h1", "abc", "cba"), HoldoutCase("h2", "", "")],
                      negative_control=Candidate("def solve(x):\\n    return x\\n"))
    report.verdict, report.completion_state      # ("passed", "qualified-draft")

``check_source`` and ``run_sandboxed`` / ``run_batch`` (``sandbox``) are the shared AST pre-filter and a bounded
out-of-process run with JSON input and output. ``evaluate`` (``admission``) runs a frozen ``Candidate`` on
evaluator-owned ``HoldoutCase`` s with a required failing negative control, and refuses a "held-out" case the
candidate was made from.

Not ported from the archive's ``hyperdistillation`` (audit 025): the capture of traces, the synthesis strategies,
the matcher and its signatures, the serving library, the seeds, manual confidence and the 26-tool MCP server. This
module serves nothing and promotes nothing. Whether a candidate is used is the caller's decision, on a report whose
best state is ``qualified-draft``.
"""
from g6.distillation.admission import Candidate, HoldoutCase, HoldoutReport, evaluate, same
from g6.distillation.sandbox import Run, check_source, run_batch, run_sandboxed

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("brittle_shortcut",)
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["Candidate", "HoldoutCase", "HoldoutReport", "Run", "check_source", "evaluate", "run_batch", "run_sandboxed",
           "same"]
