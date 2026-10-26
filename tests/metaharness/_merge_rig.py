# SPDX-License-Identifier: Apache-2.0
"""Offline measured-cost workflow for the merge regression tests."""
from dataclasses import replace

from g6.core.llm import FakeLLM
from g6.harness import Check
from g6.metaharness import Model, Profile, Suite, SuiteCase, Workflow, adapt


def harness(tmp_path, *, cost=4.0, missing=False, objectives=None):
    receipt = {}
    def respond(system, user):
        improved = "CAREFUL" in system
        receipt.update(cost_usd=cost if improved else 1.0, input_tokens=7, output_tokens=2)
        return "right" if improved or int(user.split()[-1]) % 2 == 0 else "wrong"
    def cases(prefix):
        return tuple(SuiteCase(f"{prefix}{i}", f"{prefix} {i}", "right") for i in range(40))
    wf = Workflow("measures", "Question {input}", (Check("nonempty", bool, check_id="text"),),
                  lambda out, expected: out == expected, Suite(cases("d"), cases("h")),
                  {"local": Model(FakeLLM(respond), usage=None if missing else lambda: dict(receipt))})
    if objectives is not None:
        wf = replace(wf, objectives=objectives)
    mh = adapt(wf, workspace=tmp_path, clock=lambda: 0.0)
    mh.confirm("Ada")
    proposal = mh.candidate(Profile(routing={"answer": "local"}, prompt="CAREFUL"), proposer="Bo")["proposal"]
    return mh, proposal
