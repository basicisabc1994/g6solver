# SPDX-License-Identifier: Apache-2.0
"""Review 1, class 1 (findings 9 to 16 and 21): every refusal the bridge returns carries G6's envelope, a registered
``G6_E_*`` code and a ``warning_card``, on each path the review found returning a bare ``blocked-escalated``."""
from __future__ import annotations

import pytest
from returns.result import Failure, Success

from g6.cognitive_bridge import (
    AdaptationPromotionGate, ClaimIR, CognitiveAgent, CognitiveBridgeBlock, CrossMethodConflictGate, GuardChange,
    Hypothesis, MetacognitiveMethodSelector, MetacognitivePlanner, MethodRouter, MethodVariant, MixedMethodExecutor,
    ProblemSignature, ScopedAssertion, StaticProposer,
)


def _coded(out: dict, what: str) -> None:
    from g6.cognitive_bridge import codes

    assert out['completion_state'] == 'blocked-escalated'
    assert out.get('warning_card'), f'{what} refusal has no warning_card'
    assert out['warning_card']['code'].startswith('G6_E_') and out['code'] == out['warning_card']['code']
    assert out['code'] in codes.CODES, f'{what} refusal code {out["code"]} is not registered'


@pytest.mark.parametrize('surface', ['python', 'block'])
def test_finding_9_selector_missing_mandatory_method_has_a_coded_refusal(surface):
    if surface == 'python':
        out = MetacognitiveMethodSelector().select(
            ProblemSignature('d', 'u', 'k', 1, 0), [], required_methods=['formal']).to_dict()
    else:
        out = CognitiveBridgeBlock().infer({
            'op': 'rank_methods', 'context': {'domain': 'd', 'uncertainty': 'u', 'claim_kind': 'k', 'risk': 1,
                                             'budget': 0},
            'variants': [], 'required_methods': ['formal']}).value
    _coded(out, 'selector')


@pytest.mark.parametrize('need', ['graph', 'formal'])
def test_finding_10_planner_missing_professional_obligation_has_a_coded_refusal(need):
    out = MetacognitivePlanner(MetacognitiveMethodSelector()).plan(
        [ClaimIR('c', 'claim', (need,), {})], ProblemSignature('d', 'u', 'k', 1, 1),
        [MethodVariant('v', 'formal', 'SMT', 'formal', 'check_validity', 1)], required_methods=['formal'])
    _coded(out, 'planner')


def test_finding_11_plan_methods_missing_model_has_a_coded_refusal():
    out = CognitiveBridgeBlock().infer({
        'op': 'plan_methods', 'claims': [{'id': 'c', 'statement': 'claim', 'evidence_needs': ['formal']}]}).value
    assert out['missing'][0]['method'] == 'formal'
    _coded(out, 'plan_methods')


@pytest.mark.parametrize('mode', ['failure', 'exception', 'prerequisite'])
def test_finding_12_executor_failed_run_has_a_coded_refusal(mode):
    class Registry:
        def invoke(self, *args, **kwargs):
            if mode == 'exception':
                raise RuntimeError('backend could not execute')
            if mode == 'prerequisite':
                return Success({'completion_state': 'qualified-draft', 'answer': {'identifiable': False}})
            return Failure('[G6_E_INVOKE_FAILED] backend refused')
    if mode == 'prerequisite':
        claim = ClaimIR('c', 'claim', ('causal',), {'causal': {
            'graph': 'digraph { T -> Y }', 'treatment': 'T', 'outcome': 'Y', 'data': [{'T': 0, 'Y': 1}]}})
    else:
        claim = ClaimIR('c', 'claim', ('formal',), {'formal': {'formula': 'P'}})
    out = MixedMethodExecutor(Registry()).execute(MethodRouter().plan([claim]), approved=True)
    _coded(out, 'executor')
    blocked = [step for step in out['steps'] if step['completion_state'] == 'blocked-escalated']
    assert blocked
    for step in blocked:  # every blocked step carries a bridge code (review 2: a supplied card is a registered code's)
        assert step.get('warning_card') and step['code'] == step['warning_card']['code'], step['id']
        assert step['code'].startswith('G6_E_'), step['id']


def test_finding_13_assurance_failed_criterion_has_a_coded_refusal(assurance_case):
    c = assurance_case
    out = c['evaluator'].evaluate(standard=c['standard'], artifacts={}, verifiers=c['verifiers'],
                                  documentation=c['documentation'], candidate_actor=c['candidate_actor']).to_dict()
    assert out['blockers']
    _coded(out, 'assurance dossier')


def test_finding_14_adaptation_blocked_promotion_has_a_coded_refusal():
    change = GuardChange('g', 'a' * 64, 'b' * 64, 'c' * 64, ('src/g6/cognitive_bridge/guard.py',), 'cognitive_bridge',
                         'candidate', 'evaluator', ('false_confidence',))
    _coded(AdaptationPromotionGate().assess(change, [], [], detected_failures=['false_confidence']), 'adaptation')


def test_finding_15_unadmitted_assertion_has_a_coded_refusal():
    assertion = ScopedAssertion('c', 'p', 'a' * 64, 'supports', 'formal', 'evidence:unadmitted', 'receipt')
    _coded(CrossMethodConflictGate().reconcile([assertion]), 'epistemic')


def test_finding_16_agent_without_an_executable_probe_has_a_coded_refusal():
    out = CognitiveAgent(StaticProposer([]), {}).run(
        'research', [Hypothesis('A', 'a', .5), Hypothesis('B', 'b', .5)], budget=0, max_steps=1)
    _coded(out, 'agent')


def test_finding_21_blocked_compilation_has_a_coded_refusal(assurance_case):
    c = assurance_case
    compiled = c['orchestrator'].compile(
        claim=c['compiled'].original_claim, standard=c['standard'],
        context=ProblemSignature('review', 'logic', 'validity', 1, 0), variants=[], variant_models={})
    _coded(compiled.to_dict(), 'compiled-plan')
