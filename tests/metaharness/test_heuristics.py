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


# 0.3 design 14.2: candidates built from stored strategy artefacts, lineage from the ledger, one promotion path


def _stored(tmp_path, instruction="Be CAREFUL with every digit."):
    from g6.core.ledger import ChainLedger
    from g6.learning.strategy import StrategyArtefact, StrategyStore

    store, ledger = StrategyStore(tmp_path / "strategies"), ChainLedger(tmp_path / "lineage.jsonl")
    return store, ledger, store.put(StrategyArtefact(instruction=instruction, trigger="always", scope="search"))


def test_candidates_are_built_from_stored_artefacts(tmp_path):
    from g6.learning.operators import lineage
    from g6.learning.paired_eval import apply

    store, ledger, root = _stored(tmp_path)
    strategy = HeuristicStrategy(store=store, ledger=ledger, parents=(root,))
    got = strategy.propose(_view("Answer briefly."), lambda profile, ids: [])
    assert len(got) == 4
    records = lineage(ledger)
    assert len(records) == 4 and {r.parent for r in records.values()} == {root}
    for candidate, operator in zip(got, ("regression", "boundary", "counterexample", "metamorphic")):
        digest = Profile.model_validate(candidate).digest()
        child = store.resolve(strategy.derived[digest]["strategy"])
        assert strategy.derived[digest]["operator"] == operator == records[child.content_digest].operator
        assert child.instruction == "Be CAREFUL with every digit.\n" + OPERATOR_TEXT[operator]
        assert candidate["prompt"] == apply(child, "Answer briefly.").prompt and candidate["prompt"].endswith(
            "\n\nAnswer briefly.")
        assert strategy.lineage[digest] == (root,)
    # a fifth child is past the ledger's cap, and the parent's text has nothing to do with it
    assert HeuristicStrategy(store=store, ledger=ledger, parents=(root,), max_children=5).propose(
        _view("Answer briefly."), lambda profile, ids: []) == got
    assert len(lineage(ledger)) == 4
    with_none = HeuristicStrategy(store=store, ledger=ledger)
    assert with_none.propose(_view("Answer briefly."), lambda profile, ids: []) == []


def test_a_store_and_a_ledger_come_together(tmp_path):
    import pytest

    store, ledger, root = _stored(tmp_path)
    for bad in ({"store": store}, {"ledger": ledger}, {"parents": (root,)}):
        with pytest.raises(ValueError):
            HeuristicStrategy(**bad)


def test_the_proposal_names_its_strategy_and_parent(tmp_path):
    store, ledger, root = _stored(tmp_path)
    wf, _ = rig.build()
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft"
    strategy = HeuristicStrategy(store=store, ledger=ledger, parents=(root,), max_children=2)
    proposals = mh.propose(strategy)["proposals"]
    assert len(proposals) == 2
    for proposal in proposals:
        assert (proposal.strategy, proposal.proposer, proposal.parent_digests) == ("heuristics", "auto:heuristics",
                                                                                   (root,))
        child = store.resolve(strategy.derived[proposal.candidate_digest]["strategy"])
        assert child.instruction in proposal.candidate.prompt          # the bytes the decision digest binds
        assert "prompt" in proposal.diff


def test_changing_the_strategy_after_evaluation_blocks_the_decision(tmp_path):
    store, ledger, root = _stored(tmp_path)
    wf, _ = rig.build()
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    evaluated, other = mh.propose(HeuristicStrategy(store=store, ledger=ledger, parents=(root,),
                                                    max_children=2))["proposals"]
    assert evaluated.candidate.prompt != other.candidate.prompt and evaluated.digest() != other.digest()
    report = mh.evaluate(evaluated)["report"]
    swapped = mh.decide(report, other)                                  # another strategy than the one evaluated
    assert swapped["completion_state"] == "blocked-escalated" and "not for this proposal" in swapped["message"]
    assert "decision" not in swapped or swapped["decision"].verdict != "recommend"


def test_no_local_promotion_exists():
    import g6.cognitive_bridge.strategy as bridge_strategy
    import g6.learning.operators as operators
    import g6.learning.paired_eval as paired_eval
    import g6.learning.worth as worth

    for module in (H, operators, bridge_strategy, paired_eval, worth):
        public = set(getattr(module, "__all__", ())) | {n for n in vars(module) if not n.startswith("_")}
        assert not [n for n in public if any(w in n.lower() for w in ("promote", "assess", "approve"))], module


def test_approval_is_only_the_client_dialog():
    """The heuristics path reaches activation through metaharness_approve alone: no module on it takes an approval
    argument, reads an environment variable or names a terminal command."""
    import inspect

    import g6.cognitive_bridge.strategy as bridge_strategy
    import g6.learning.operators as operators
    import g6.learning.paired_eval as paired_eval
    import g6.learning.strategy as strategy
    import g6.learning.worth as worth

    for module in (H, operators, bridge_strategy, paired_eval, strategy, worth):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        params = {a.arg for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  for a in (*n.args.args, *n.args.kwonlyargs)}
        assert not params & {"approved", "approve", "accepted_by", "confirm", "answer", "activate"}, module
        assert "os.environ" not in source and "getenv" not in source and "activate(" not in source, module
