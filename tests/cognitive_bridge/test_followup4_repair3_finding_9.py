"""Thirty-three hypotheses in a mapping are copied whole, and the call then fails outside the bridge's refusals.

Review 2's rule 7: the 32-hypothesis cap precedes hypothesis copying at every caller. Repair 2 counts declared records
in a list or a tuple; the commit says a mapping of them is not counted, and that ``LLMProposer.propose`` hands the
copier no bound of its own. Together: a mapping of 33 Hypothesis records is detached and every record made again, and
the proposer then fails with AttributeError where a list of the same records is refused by the cap.
"""
from g6.cognitive_bridge import Hypothesis, LLMProposer, Observation, ToolSpec
from tests.cognitive_bridge.followup4_repair3_support import NoModel, observe_walk


def _propose(hypotheses):
    proposer = LLMProposer(NoModel({'probes': []}), {'read': ToolSpec('read', lambda q, a: Observation(True, 'r'))})
    raised = None
    with observe_walk(lambda raw: raw is hypotheses) as copied:
        try:
            proposer.propose('objective', hypotheses, [])
        except Exception as exc:
            raised = exc
    return copied, raised


def _thirty_three():
    return {f'h{i}': Hypothesis(f'h{i}', 's', 1 / 33) for i in range(33)}


def test_a_mapping_of_thirty_three_hypotheses_is_not_copied_before_a_count_is_read():
    copied, _ = _propose(_thirty_three())
    assert copied == [], '33 hypotheses were detached before any count was read'


def test_a_mapping_of_thirty_three_hypotheses_is_refused_by_the_bridge():
    _, raised = _propose(_thirty_three())
    assert isinstance(raised, ValueError), f'not a refusal of the bridge: {raised!r}'


def _routed(claims, nested):
    from g6.cognitive_bridge import MethodRouter
    raised = None
    with observe_walk(lambda raw: raw is nested) as copied:
        try:
            MethodRouter().plan(claims)
        except Exception as exc:
            raised = exc
    return copied, raised


def _thirty_nine():
    from g6.cognitive_bridge import ClaimIR
    nested = [ClaimIR(f'n{i}', 's', ('formal',), {}) for i in range(20)]
    return [ClaimIR(f'c{i}', 's', ('formal',), {}) for i in range(19)] + [nested], nested


def test_thirty_nine_claims_across_a_nested_list_are_not_copied_by_the_router():
    # each list is counted on its own: 19 claims and a list of 20 more pass both counts
    claims, nested = _thirty_nine()
    copied, _ = _routed(claims, nested)
    assert copied == [], 'a nested list of 20 more claims was detached by the router'


def test_thirty_nine_claims_across_a_nested_list_are_refused_by_the_bridge():
    claims, nested = _thirty_nine()
    _, raised = _routed(claims, nested)
    assert isinstance(raised, ValueError), f'not a refusal of the bridge: {raised!r}'
