# SPDX-License-Identifier: Apache-2.0
"""Protected bridge to the unchanged frozen EvidencePolicy; measured receipts only.

The host runs acceptance_gate or metaharness.gate unchanged and attests its result.
This secondary gate checks the pinned policy, conservative intervals and counts;
it does not replace that evaluation with a runner prediction or self-test.
"""
import math

from g6.core.ledger import digest
from dataclasses import asdict


def check_evaluation(evaluation, claim, p):
    if p is None or evaluation.policy_sha256 != digest(asdict(p)):
        return 'evaluation does not bind the frozen EvidencePolicy'
    if not evaluation.independent or not evaluation.receipts or not evaluation.gate_receipt:
        return 'independent measured gate receipts required'
    units = evaluation.units
    if not units or len({row.unit_id for row in units}) != len(units):
        return 'distinct bound bug unit pairs required'
    wins = sum(row.candidate_success and not row.base_success for row in units)
    losses = sum(row.base_success and not row.candidate_success for row in units)
    if (evaluation.n, evaluation.wins, evaluation.losses, evaluation.ties) != (
            len(units), wins, losses, len(units) - wins - losses):
        return 'measured counts differ from bound bug unit pairs'
    if evaluation.n < p.min_cases or evaluation.wins + evaluation.losses + evaluation.ties != evaluation.n:
        return 'insufficient or inconsistent measured unit counts'
    if not 1 <= evaluation.reuse_index <= p.max_evaluations:
        return 'held-out split is spent or its reuse index is missing'
    measures = evaluation.measures
    if set(measures) != {'success', 'false_acceptance', 'false_rejection', 'cost'}:
        return 'all before/after measures with intervals are required'
    before = sum(row.base_success for row in units) / len(units)
    after = sum(row.candidate_success for row in units) / len(units)
    if (abs(measures['success'].before - before) > 1e-9
            or abs(measures['success'].after - after) > 1e-9):
        return 'success rates differ from bound bug unit pairs'
    if measures['false_acceptance'].delta_upper95 > p.false_accept_margin:
        return 'false acceptance regressed beyond frozen margin'
    if measures['false_rejection'].delta_upper95 > p.false_accept_margin:
        return 'false rejection regressed beyond frozen margin'
    if measures['cost'].delta_upper95 > abs(measures['cost'].before) * p.cost_margin:
        return 'cost regressed beyond frozen margin'
    if claim == 'no_behaviour_change':
        if (evaluation.split != 'monitor' or not evaluation.full_suite or evaluation.gate != 'non_inferior'
                or measures['success'].delta_lower95 is None or measures['success'].delta_lower95 < 0):
            return 'full suite and measured monitor non-inferiority required'
        return ''
    if evaluation.split != 'held_out' or evaluation.gate not in ('recommend', 'accepted'):
        return 'unchanged held-out evaluator must accept the improvement'
    discordant = evaluation.wins + evaluation.losses
    if discordant < p.min_discordant or evaluation.wins <= evaluation.losses:
        return 'no measured held-out improvement'
    # Stable binomial sign-test upper tail; counts are capped by the typed receipt.
    log2 = math.log(2)
    tail = math.fsum(math.exp(math.lgamma(discordant + 1) - math.lgamma(k + 1)
        - math.lgamma(discordant - k + 1) - discordant * log2) for k in range(evaluation.wins, discordant + 1))
    if tail > p.threshold or abs(tail - evaluation.p_value) > 1e-9:
        return 'paired held-out sign test failed or p value differs'
    if measures['success'].after <= measures['success'].before:
        return 'no measured success gain'
    return ''
