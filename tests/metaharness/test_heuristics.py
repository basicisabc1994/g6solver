# SPDX-License-Identifier: Apache-2.0
"""PR #4's heuristic discovery as a meta-harness proposer (P1): each candidate adds one operator's instruction to the
profile's prompt, the meta-harness's own gate decides it, and the module keeps no gate, assessment or approval flag of
its own."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

from g6.metaharness import Profile, adapt
from g6.metaharness import heuristics as H
from g6.metaharness.heuristics import OPERATOR_TEXT, OPERATORS, HeuristicStrategy
from g6.metaharness.propose import ProposerView

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)


def _view(prompt=""):
    return ProposerView(workflow="w", task="t", checks=(), steps=(), models=(), optional_checks=(),
                        base=Profile(prompt=prompt).model_dump(mode="json"), dev=(), failures=(), dev_success=0)


def test_the_operators_are_pr4s_six_in_its_order():
    assert OPERATORS == ("regression", "boundary", "counterexample", "metamorphic", "decompose", "prompt_revision")
    assert set(OPERATOR_TEXT) == set(OPERATORS) - {"prompt_revision"}


def test_each_candidate_adds_one_operator_text_in_order():
    got = HeuristicStrategy().propose(_view(""), lambda profile, ids: [])
    assert [c["prompt"] for c in got] == [OPERATOR_TEXT[o] for o in ("regression", "boundary", "counterexample",
                                                                       "metamorphic")]
    based = HeuristicStrategy(max_children=2).propose(_view("Be brief."), lambda profile, ids: [])
    assert [c["prompt"] for c in based] == [f"Be brief.\n\n{OPERATOR_TEXT['regression']}",
                                            f"Be brief.\n\n{OPERATOR_TEXT['boundary']}"]


def test_an_operator_already_in_the_prompt_is_not_proposed_again():
    got = HeuristicStrategy().propose(_view(OPERATOR_TEXT["regression"]), lambda profile, ids: [])
    assert OPERATOR_TEXT["regression"] + "\n\n" + OPERATOR_TEXT["regression"] not in [c["prompt"] for c in got]
    assert got[0]["prompt"].endswith(OPERATOR_TEXT["boundary"])


def test_no_candidate_once_the_base_holds_max_generation_texts():
    held = "\n\n".join(OPERATOR_TEXT[o] for o in ("regression", "boundary", "counterexample"))
    assert HeuristicStrategy(max_generation=3).propose(_view(held), lambda profile, ids: []) == []
    assert HeuristicStrategy().propose(_view("x" * 7_990), lambda profile, ids: []) == []   # past 8,000 characters


def test_its_candidates_are_meta_harness_proposals(tmp_path):
    wf, _ = rig.build()
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft"
    proposal = mh.propose(HeuristicStrategy())["proposals"][0]
    assert (proposal.strategy, proposal.proposer) == ("heuristics", "auto:heuristics")


def test_the_module_keeps_no_gate_of_its_own():
    tree = ast.parse(Path(H.__file__).read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not names & {"assess", "promote", "mutate"}
    params = {a.arg for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              for a in (*n.args.args, *n.args.kwonlyargs)}
    assert "approved" not in params
