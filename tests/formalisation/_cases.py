# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Shared builders for the formalisation tests: a confirmed example and a statement of the SMT fragment."""
from __future__ import annotations

import hashlib

from g6.harness.formalisation import smt_adapter
from g6.harness.formalisation.adapters import ExampleObservation, from_expectation
from g6.harness.confirmed_expectation import Question, confirm

BASE = hashlib.sha256(b"the base commit's tree").hexdigest()
DOMAIN = ("(>= x 0)", "(<= x 10)")


def question(args, outcomes, *, function="shop.f", requirement_id="R1", **over) -> Question:
    return Question(requirement_id=requirement_id, function=function, args=list(args), outcomes=outcomes,
                    scope="this one case", cited="the task: f adds one", baseline_sha256=BASE, **over)


def confirmed(args, outcomes, chosen="A", **over):
    return confirm(question(args, outcomes, **over), chosen, answered_by="person:owner")


def example(args, accepted, *rejected, function="shop.f") -> ExampleObservation:
    """A person-confirmed example: option A is what they confirmed, the other options what they rejected."""
    outcomes = {"A": accepted, **{chr(ord("B") + i): one for i, one in enumerate(rejected)}}
    return from_expectation(confirmed(args, outcomes, function=function))


def relation(text, *, domain=DOMAIN, inputs=(("x", "Int"),), result_sort="Int", **over):
    return smt_adapter.statement(entry_point="shop.f", inputs=inputs, result_sort=result_sort, relation=text,
                                 domain=domain, **over)