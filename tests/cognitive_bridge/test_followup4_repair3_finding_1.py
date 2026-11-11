"""Check-then-act: the walker counts the caller's list, then copies it by a later read of the same list.

The count caps (20 claims, 100 variants) are read from the caller's own list (``bound(value)``, ``_admit_held(value)``)
and the copy is then made by iterating that list again. A list that grows in between is copied past its cap, and no
rule reads the copy's length afterwards: the router plans, and publishes, more than 20 claims.
"""
from g6.cognitive_bridge import (
    ClaimIR, MetacognitiveMethodSelector, MethodRouter, MethodVariant, ProblemSignature,
)
from tests.cognitive_bridge.followup4_repair3_support import second_writer


def _claim(name):
    return ClaimIR(name, 's', ('formal',), {'formal': {'formula': 'A'}})


def _variant(name):
    return MethodVariant(name, 'formal', 'SMT', 'formal', 'check_validity', 1)


def test_router_does_not_plan_more_than_twenty_claims_when_the_list_grows_during_the_copy():
    claims = [_claim(f'c{i}') for i in range(20)]
    extra = [_claim(f'x{i}') for i in range(5)]
    refused = ''
    plan = None
    with second_writer(claims, claims[0], extra) as writer:
        try:
            plan = MethodRouter().plan(claims)
        except ValueError as exc:
            refused = str(exc)
    assert writer['done'], 'the stand-in writer never ran: the fixture did not reach the copy'
    assert plan is None or len(plan.claims) <= 20, (
        f'the router planned {len(plan.claims)} claims ({len(plan.steps)} steps) past its 20-claim cap')
    assert plan is not None or '20 typed claims' in refused


def test_selector_does_not_rank_more_than_one_hundred_variants_when_the_list_grows_during_the_copy():
    variants = [_variant(f'v{i}') for i in range(100)]
    extra = [_variant(f'x{i}') for i in range(5)]
    context = ProblemSignature('review', 'logic', 'validity', 1, 1000)
    refused = ''
    decision = None
    seen = []
    selector = MetacognitiveMethodSelector()
    scored = selector._score

    def counted(variant, context, **kwargs):
        seen.append(variant.id)
        return scored(variant, context, **kwargs)

    selector._score = counted
    with second_writer(variants, variants[0], extra) as writer:
        try:
            decision = selector.select(context, variants, required_methods=['formal'])
        except ValueError as exc:
            refused = str(exc)
    assert writer['done'], 'the stand-in writer never ran: the fixture did not reach the copy'
    assert len(seen) <= 100, f'the selector scored {len(seen)} variants past its 100-variant cap'
    assert decision is not None or 'at most 100' in refused
