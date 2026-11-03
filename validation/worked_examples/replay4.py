# SPDX-License-Identifier: Apache-2.0
"""Replay 4 (PLAN.md, 2026-11-04, midday): replay 3's machinery, run the same way, with tests that assert only that the issue's own
symptom is absent, on the issue's example and on its variants. It changes no run, no verdict and no rule.

Why: on stage 1 and 2 the commonest wrong patch of a bug is wrong the way many runs are, and both model families' reviewers passed it
about as often as a right patch (32% flagged against 29%), while catching the patches wrong in their own way. Both raters of the
contrastive blind labels put it as a fix shaped by the issue's example, shown by running a variant of that example. A model writing
what a variant should return writes the same reading the patches share, so here no model writes a right result: the reviewer gives
inputs, the issue gives the symptom, and running them on the parent says which variants show it. A case that fails on the parent
shows the symptom there; a patch on which such a case still fails left that variant's defect in place, and is "reproduced".
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence

import replay3 as r3
import run as we

#: The task, in general words only: no bug, file or issue of any stage. Its limits, its reply's form and its grounding rules are
#: replay 3's own text.
SYMPTOM_FIRST = """<task>
You are writing tests for a reported bug, before anyone shows you a fix. Your cwd has:
- issue.txt: the issue and its discussion.
- parent/: the repository just before the fix.

No fix is in your cwd, and none is described to you. A fix that makes only the issue's own example pass is not a fix: the same
defect usually shows on other inputs and other paths too, and those are what your tests are for.

1. Name the symptom the issue reports: the exception raised, the crash or hang, the state changed that should not be, or the output
   it calls wrong. Take it from the issue's own words. On the RULE line, give the symptom and the rule it breaks.
2. Find in parent/ each public path where the same defect could show: the function, class, method, command or option the issue
   uses, and the other public entry points that reach the same code or handle the same kind of input, such as its siblings in the
   same module and the other types, formats or options it supports.
3. Write one pytest file of 6 to 16 cases, each its own test: the issue's own example first, then its variants, each changing one
   thing the issue's rule covers: another value or type of the same kind, an edge of the input's range, another entry point or
   sibling path, another call form or option. Each case asserts only that the reported symptom does not occur: the exception is not
   raised, the state is not changed, the output the issue calls wrong is not produced. Assert no other value: do not decide what
   the right result is beyond the symptom's absence. Wrap each call so that anything other than the symptom calls pytest.skip,
   never a failure: a case must fail only on the symptom. A variant that does not show the symptom on parent/ is harmless. If the
   issue reports no symptom a test can observe in one process, say so and write no test. That is a valid and useful answer.
</task>

""" + r3.RULE_FIRST[r3.RULE_FIRST.index("<hard_limits>"):]
INSTRUCTION = hashlib.sha256(SYMPTOM_FIRST.encode("utf-8")).hexdigest()[:16]
SYMPTOM = r3.Instruction(SYMPTOM_FIRST, INSTRUCTION, f"final_review_symptom-{INSTRUCTION}", f"{we.WE}/symptom", "test_symptom.py")
FOLDER = SYMPTOM.folder


def main(argv: Sequence[str] | None = None) -> None:
    r3.main(argv, ins=SYMPTOM)


if __name__ == "__main__":
    main()
