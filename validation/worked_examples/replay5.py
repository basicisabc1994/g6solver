# SPDX-License-Identifier: Apache-2.0
"""Replay 5 (PLAN.md, 2026-11-04, afternoon): replay 3's machinery, run the same way, with tests that assert only a relation between
the code's own outputs, on the issue's example and on its variants. It changes no run, no verdict and no rule.

Why: replay 4's symptom test never flagged a right patch on stage 1 and 2 (0 of 477), but it caught about one wrong patch in five,
because most wrong patches remove the symptom and return a wrong value, which the symptom's absence cannot see. A model writing what
the right value is writes the reading the wrong patches share, so here, too, no model writes a right result: the reviewer names a
relation the issue or the repository states (two paths agree, a round trip returns its input, equivalent inputs give the same
output, the same change of input moves the output the same way), and both sides come from calls to the code. Running it on the
parent says which variants break it; a patch on which such a case still fails left that variant's defect in place, and is
"reproduced".
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence

import replay3 as r3
import run as we

#: The task, in general words only: no bug, file or issue of any stage. Its limits, its reply's form and its grounding rules are
#: replay 3's own text.
RELATION_FIRST = """<task>
You are writing tests for a reported bug, before anyone shows you a fix. Your cwd has:
- issue.txt: the issue and its discussion.
- parent/: the repository just before the fix.

No fix is in your cwd, and none is described to you. A fix that makes only the issue's own example pass is not a fix: the same
defect usually shows on other inputs and other paths too, and those are what your tests are for.

1. Name the rule the issue says the code breaks, and state it as a relation between outputs of the code itself, one that needs no
   expected value written by you. For example: two public paths that should agree on the same input agree; a conversion and its
   inverse return the input; an input and an equivalent form of it (another type, spelling, order or call form that the docs or
   the code treat as the same) give the same result; doing the operation twice gives what doing it once gives, where the docs or
   the code say so; the same change of input moves the output the same way on a variant as on the issue's own example. On the
   RULE line, give the relation and where it is stated: the issue's words, or a docstring, doc or test in parent/ with file and
   line.
2. Find in parent/ each public path the relation covers: the function, class, method, command or option the issue uses, and the
   other public entry points that reach the same code or handle the same kind of input.
3. Write one pytest file of 6 to 16 cases, each its own test: the issue's own example first, then its variants, each changing one
   thing the rule covers: another value or type of the same kind, an edge of the input's range, another entry point or sibling
   path, another call form or option. Each case computes both sides of the relation by calling the code, and asserts only that
   they agree. Write no expected value of your own: every value an assertion compares comes from a call to the code under test or
   is an input you passed in. Wrap each call so that an exception the issue does not report calls pytest.skip, never a failure: a
   case must fail only when the relation breaks. A variant on which the relation holds on parent/ is harmless. If the issue's rule
   cannot be stated as such a relation, say so and write no test. That is a valid and useful answer.
</task>

""" + r3.RULE_FIRST[r3.RULE_FIRST.index("<hard_limits>"):]
INSTRUCTION = hashlib.sha256(RELATION_FIRST.encode("utf-8")).hexdigest()[:16]
RELATION = r3.Instruction(RELATION_FIRST, INSTRUCTION, f"final_review_relation-{INSTRUCTION}", f"{we.WE}/relation", "test_relation.py")
FOLDER = RELATION.folder


def main(argv: Sequence[str] | None = None) -> None:
    r3.main(argv, ins=RELATION)


if __name__ == "__main__":
    main()
