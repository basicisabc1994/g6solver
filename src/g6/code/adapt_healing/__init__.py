# SPDX-License-Identifier: Apache-2.0
"""g6.code.adapt_healing — repair a failing step of Python code, keeping a patch only if it passes held-back tests its proposer never saw.

An extension in the code family. ``heal(code, error, tests_shown, tests_held_back, rounds <= 3)`` reproduces the failure on the shown tests and classifies its observed exception
with ``g6.triage`` (an error that touches files, the network or memory, or whose class is not known, goes to a person
and no model is asked); asks the ``g6.core.llm`` caller you give the block for a patch; parses it here and runs it with
the shown tests in ``g6.core.sandbox``; feeds the shown failures back for at most three rounds; and judges the first
patch that passes them once on the held-back tests. A held-back test reads ``assert <expression> == <literal>``: only
the expression goes into the child process, and the literal is compared in G6's own, so the patch never sees an
expected answer. The patch shares the runner process and can forge a pass by altering the runner. Any held-back failure is ``blocked-escalated``; a
patch that passes them all is a ``qualified-draft``. ``check_tests`` reads the tests and the error and runs nothing.

    from g6.code.adapt_healing import AdaptHealingBlock, AdaptHealingInput
    block = AdaptHealingBlock(caller=my_model)          # any g6.core.llm caller
    out = block.infer(AdaptHealingInput(op="heal", code=code, error="ZeroDivisionError: division by zero",
                                        tests_shown=("assert mean([2, 4]) == 3",),
                                        tests_held_back=("assert mean([]) == 0",))).unwrap()

Not ported from the archive's adapt_healing, and why: running caller and model code in an unrestricted child process
by default; judging a repair on the very tests whose expected values were fed back to the patcher (it called that
verified); detect's lint and the regular-expression patches, which also rewrote text inside strings; the first-match
diagnosis table (``g6.triage`` instead); drift, backups, rules, retries and the 32-op MCP block with its store in the
home folder; the model calls through a dispatcher that used litellm; and the parts adapted from outside projects whose
licences were not established.
"""
from g6.code.adapt_healing import cases, heal
from g6.code.adapt_healing.block import SENSITIVE_OPS, AdaptHealingBlock, AdaptHealingInput

__g6_component__ = True
__g6_block__ = "AdaptHealingBlock"
__g6_model__ = "caller"  # the block's model keyword: g6_invoke passes the model the person configured, if any
__g6_family__ = "code"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # heal runs the patch, with your tests, in a bounded child process
__g6_requires__: tuple[str, ...] = ("triage",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a patch can pass
# every held-back test the caller wrote and still be wrong on cases nobody wrote).
__g6_mitigates__: tuple[str, ...] = ("cannot_self_repair", "careless_auto_fix")
__g6_introduces__: tuple[str, ...] = ("overfit_to_examples",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "AdaptHealingBlock", "AdaptHealingInput", "cases", "heal"]
