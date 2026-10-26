# SPDX-License-Identifier: Apache-2.0
"""The words G6 returns about an optimisation answer say what a check showed, never that G6 certified it.

The field ``certificate``, the check's ``certificate`` name and the code ``G6_E_OPTIM_CERTIFICATE_REFUTED`` name the
mathematical object (the duals, a cut, a ray) and stay; the sentences around them changed."""
from __future__ import annotations

import json

import pytest

from g6.optimisation import programmes, solve_lp
from g6.optimisation.optimisation_block import OptimisationBlock, OptimisationInput

_BANNED = "certif"


def _words(text: str) -> str:
    """``text`` without the field and code names that keep the word."""
    for kept in ("G6_E_OPTIM_CERTIFICATE_REFUTED", '"certificate"', "certificate"):
        text = text.replace(kept, "")
    return text.lower()


def test_the_help_served_over_mcp_does_not_say_certified() -> None:
    reply = OptimisationBlock().infer(OptimisationInput(op="help")).unwrap()
    assert _BANNED not in json.dumps(reply["help"]).lower()
    assert "duals" in reply["help"][0] and "solve_lp" in reply["help"][0]


def test_the_solve_lp_docstring_does_not_say_certified() -> None:
    assert _BANNED not in (solve_lp.__doc__ or "").lower()


def test_the_reason_for_an_unconfirmed_solver_status_does_not_say_certify(monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.optimisation.schema import LinearProblem

    monkeypatch.setattr(programmes, "_mathopt", lambda *a, **k: {"status": "ABNORMAL"})
    problem = LinearProblem.model_validate({"variables": {"x": {}}, "constraints": [], "objective": {"x": 1}})
    outcome = programmes._classify(problem, "INFEASIBLE", 1.0)
    assert _BANNED not in outcome["reason"].lower() and "GLOP reported INFEASIBLE" in outcome["reason"]


def test_an_infeasible_programme_says_what_its_duals_show() -> None:
    pytest.importorskip("ortools")
    r = solve_lp({"x": {}, "y": {}}, [{"terms": {"x": 1, "y": 1}, "ub": 1}, {"terms": {"x": 1, "y": 1}, "lb": 2}],
                 {"x": 1})
    assert r.completion_state == "blocked-escalated" and r.check["passed"] is True
    said = " ".join([*r.check["checked"], r.reason, r.warning_card["message"]])
    assert _BANNED not in _words(said), said
    assert "duals" in " ".join(r.check["checked"])
