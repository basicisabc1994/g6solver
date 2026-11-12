# SPDX-License-Identifier: Apache-2.0
"""A heuristic is a method variant of kind ``heuristic`` bound to its strategy's bytes, beside the evidence plan and
never inside it (0.3 design 14.2): no existing variant's digest changed, same labels over different bytes share no
history, and a heuristic can neither meet nor remove an obligation."""
from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from g6.cognitive_bridge import (ClaimIR, EvaluatedTrial, MetacognitiveMethodSelector, MetacognitivePlanner,
                                 MethodVariant, ProblemSignature)
from g6.cognitive_bridge.block import MethodVariantInput
from g6.cognitive_bridge.metacognition import HEURISTIC, ROW_FIELDS
from g6.cognitive_bridge.mixed_methods import ENGINE_SPECS
from g6.learning.method_kind import variant_fields
from g6.learning.strategy import StrategyArtefact, seed_artefacts
from g6.mcp import invoke_reply

PIN = "a" * 64
ONE = StrategyArtefact(instruction="Propagate stated constraints before searching.", trigger="always", scope="search")
OTHER = dataclasses.replace(ONE, instruction="Search first, then propagate the stated constraints.")


def heuristic_variant(artefact: StrategyArtefact, *, id: str, estimated_cost: float) -> MethodVariant:
    return MethodVariant(**variant_fields(artefact, id=id, estimated_cost=estimated_cost))


def context(budget: float = 10) -> ProblemSignature:
    return ProblemSignature("software_engineering", "bug_fix", "patch", 1, budget)


def test_engine_variant_digests_are_unchanged() -> None:
    """Pinned on the bridge's commit before this change (claude/pr8-followup-4, 6e39ab0): adding the content digest
    moved no existing variant's definition."""
    golden = {
        MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2):
            "a660bd0a1e7fa46084a80141edea1f6ce3f0d6e258e6fc5dcd48a921ba4ac66a",
        MethodVariant("claude-review", "review", "diff", "claude_code", "review", 1, kind="reviewer"):
            "d6de61c1197f1930d1eba9339928ef710134f424467cd522f30778a71f6ae50e",
        MethodVariant("loop-arm", "repair", "patch", "g6_loop", "repair", 2, kind="arm"):
            "4cfdd169fd8ed7c9179ca1f01fbb07d38deaa434af49abe48ece02ab32815a4d",
    }
    for variant, digest in golden.items():
        assert variant.definition_digest == digest and variant.content_digest == ""


def test_a_heuristic_needs_its_content() -> None:
    made = heuristic_variant(ONE, id="constraints", estimated_cost=0.5)
    assert (made.kind, made.method, made.engine, made.op) == (HEURISTIC, "strategy", "g6.learning", "apply")
    assert made.content_digest == ONE.content_digest and made.representation == "addendum.single/1:search"
    for digest in ("", "abc", "A" * 64, "g" * 64, ONE.content_digest + "0"):
        with pytest.raises(ValueError):
            MethodVariant("h", "strategy", "addendum.single/1:search", "g6.learning", "apply", 1, kind=HEURISTIC,
                          content_digest=digest)
    for method, engine, op in (("review", "g6.learning", "apply"), ("strategy", "claude_code", "apply"),
                               ("strategy", "g6.learning", "run"), ("formal", "formal", "check_validity")):
        with pytest.raises(ValueError):
            MethodVariant("h", method, "x", engine, op, 1, kind=HEURISTIC, content_digest=ONE.content_digest)
    with pytest.raises(ValueError, match="only a heuristic variant carries"):
        MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2, content_digest=ONE.content_digest)
    with pytest.raises(ValueError, match="only a heuristic variant"):
        MethodVariant("arm", "strategy", "patch", "g6_loop", "repair", 1, kind="arm")


def test_strategy_is_not_a_sixth_method_family() -> None:
    assert sorted(ENGINE_SPECS) == ["causal", "formal", "graph", "optimisation", "predictive"]
    assert "strategy" not in ENGINE_SPECS


def test_same_labels_different_bytes_do_not_share_history() -> None:
    first = heuristic_variant(ONE, id="seed", estimated_cost=1)
    second = heuristic_variant(OTHER, id="seed", estimated_cost=1)
    assert (first.id, first.method, first.representation, first.engine, first.op) == \
        (second.id, second.method, second.representation, second.engine, second.op)
    assert first.definition_digest != second.definition_digest
    trial = EvaluatedTrial("t1", "c1", "frozen-suite-1", "software_engineering", "bug_fix", "patch", "seed", True, 1,
                           "trusted://evaluator", PIN, "heldout", variant_digest=first.definition_digest)
    selector = MetacognitiveMethodSelector([trial], admit_trial=lambda t: True)
    assert selector._score(first, context())["training_cases"] == 1
    assert selector._score(second, context())["training_cases"] == 0


def test_sixteen_seeds_are_sixteen_definitions() -> None:
    variants = [heuristic_variant(artefact, id=name, estimated_cost=0) for name, artefact in seed_artefacts().items()]
    assert len({v.definition_digest for v in variants}) == 16 == len({v.content_digest for v in variants})


def test_a_heuristic_cannot_meet_or_remove_an_obligation() -> None:
    engines = [MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2),
               MethodVariant("claude-review", "review", "diff", "claude_code", "review", 1, kind="reviewer")]
    heuristics = [heuristic_variant(ONE, id="constraints", estimated_cost=0),
                  heuristic_variant(OTHER, id="search-first", estimated_cost=0)]
    selector = MetacognitiveMethodSelector()
    for required, optional in ((["formal", "review"], 0), (["formal"], 3), (["review"], 3)):
        without = selector.select(context(), engines, required_methods=required, max_optional=optional)
        with_them = selector.select(context(), [*engines, *heuristics], required_methods=required,
                                    max_optional=optional)
        assert with_them.to_dict() == without.to_dict()          # rows, missing, refusal and the policy digest
        assert not [row for row in with_them.ordered_variants if row["kind"] == HEURISTIC]
    tight = ProblemSignature("software_engineering", "bug_fix", "patch", 1, 0.5)   # nothing mandatory is affordable
    assert selector.select(tight, [*engines, *heuristics], required_methods=["formal"]).to_dict() == \
        selector.select(tight, engines, required_methods=["formal"]).to_dict()
    assert selector.select(tight, engines, required_methods=["formal"]).missing
    with pytest.raises(ValueError, match="required methods"):
        selector.select(context(), [*engines, *heuristics], required_methods=["strategy"])
    with pytest.raises(ValueError, match="required methods"):
        selector.select(context(), heuristics, required_methods=["strategy"])


def test_the_plan_and_its_gaps_are_the_same_with_and_without_heuristics() -> None:
    claim = ClaimIR("c1", "x > 0 implies x + 1 > 1", ("formal",), {"formal": {"formula": "True"}})
    engines = [MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2)]
    heuristics = [heuristic_variant(ONE, id="constraints", estimated_cost=0)]
    planner = MetacognitivePlanner(MetacognitiveMethodSelector())
    assert planner.plan([claim], context(), [*engines, *heuristics], required_methods=["formal"]) == \
        planner.plan([claim], context(), engines, required_methods=["formal"])


def test_a_row_has_no_field_for_strategy_content() -> None:
    assert "content_digest" not in ROW_FIELDS


def test_a_heuristic_is_not_reachable_as_an_engine_over_mcp() -> None:
    given = {"id": "h", "method": "strategy", "representation": "addendum.single/1:search", "engine": "g6.learning",
             "op": "apply", "estimated_cost": 0, "kind": "heuristic"}
    with pytest.raises(ValidationError):
        MethodVariantInput(**given)
    with pytest.raises(ValidationError):
        MethodVariantInput(**{**given, "method": "formal", "content_digest": ONE.content_digest})
    reply = invoke_reply("cognitive_bridge", "rank_methods", __import__("json").dumps({
        "context": {"domain": "software_engineering", "uncertainty": "bug_fix", "claim_kind": "patch", "risk": 1,
                    "budget": 10},
        "variants": [given], "required_methods": ["formal"]}))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
