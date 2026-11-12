# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The draft and repair prompts of the earlier repository's autoformaliser, ported (design section 15.4).

Four prompts are kept: a first draft and a repair, for SMT-LIB and for Lean 4. Their wording is the earlier
repository's, with one change in each SMT prompt: where it asked for a free SMT-LIB program, it now asks for the
typed reply G6 reads (``REPLY_SMT``), because generated SMT goes through G6's typed declarations and term reader
and never through an arbitrary program. Nothing else of that repository's autoformaliser is here: no backend
loader, no success schema, no acceptance helper. A reply is a hypothesis; only the checks and the person decide
anything about it.

Everything a prompt carries from the person (the task, the confirmed examples, retrieved statements) reaches a
model only inside ``g6.core.cloud_consent.request_sources``, through a guarded caller (``g6.harness.formalisation.draft``).
"""
from __future__ import annotations

SYSTEM = "You are a formal mathematics assistant."

INITIAL_SMTLIB = """\
You are a formal mathematics assistant. Translate the following informal mathematical \
statement into a valid SMT-LIB2 formalization, given as the typed reply described below. Output ONLY \
the reply. Do not include explanations or markdown fences.

Informal statement: {statement}
{context_block}
{reply_format}
SMT-LIB2 formalization:"""

REPAIR_SMTLIB = """\
The following SMT-LIB2 formalization failed its checks. Fix it. Output ONLY the corrected \
reply, in the same typed form. Do not include explanations or markdown fences.

Informal statement: {statement}
Previous attempt:
{prev_formalization}
Check diagnostics:
{error}
{context_block}
{reply_format}
Corrected SMT-LIB2:"""

INITIAL_LEAN4 = """\
You are a formal mathematics assistant. Translate the following informal mathematical \
statement into a valid Lean 4 theorem declaration. Output ONLY the Lean 4 code starting \
with `theorem` or `def`. Do not include explanations or markdown fences.

Informal statement: {statement}
{context_block}
Lean 4 formalization:"""

REPAIR_LEAN4 = """\
The following Lean 4 formalization has a type error. Fix it so it elaborates without \
errors. Output ONLY the corrected Lean 4 code. Do not include explanations or markdown fences.

Informal statement: {statement}
Previous attempt:
{prev_formalization}
Lean error:
{error}
{context_block}
Corrected Lean 4 formalization:"""

#: The typed reply an SMT draft is read from. The sorts are the function's own; the relation is one term.
REPLY_SMT = """\
Reply with one JSON object and nothing else:
{{"sorts": {sorts}, "relation": "<one SMT-LIB term of sort Bool over the inputs and result>", \
"raises_when": "<one Bool term over the inputs, or empty>", "error": "<the exception type's name, or empty>", \
"assumptions": []}}
Use only: whole numbers, true, false, the declared symbols, + - *, div and mod by a positive literal, \
= distinct < <= > >=, not and or =>, ite. No quantifier, no let, no function, no other symbol.
The function is {entry_point}. Its inputs are {inputs}; "result" is the value it returns ({result_sort}).
Inputs covered: {domain}"""

PROMPTS = {("smt", "initial"): INITIAL_SMTLIB, ("smt", "repair"): REPAIR_SMTLIB,
           ("lean4", "initial"): INITIAL_LEAN4, ("lean4", "repair"): REPAIR_LEAN4}

__all__ = ["INITIAL_LEAN4", "INITIAL_SMTLIB", "PROMPTS", "REPAIR_LEAN4", "REPAIR_SMTLIB", "REPLY_SMT", "SYSTEM"]