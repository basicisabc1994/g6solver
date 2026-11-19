# SPDX-License-Identifier: Apache-2.0
"""Deterministic interventions derived from an assessment; no execution or authority."""
from __future__ import annotations

import copy


def build(assessment: dict, *, repo: str, task: str) -> list[dict]:
    requirements = assessment.get('requirements', [])
    stated = [{key: r[key] for key in ('id', 'text', 'kind', 'provenance') if key in r} |
              {'checks': [c['id'] for c in r.get('checks', []) if c.get('provenance', 'stated') == 'stated']}
              for r in requirements]
    out = []

    def entry(requirement, behaviour, call, *, person=None, model=False, check=''):
        needs = {'isolation': True, 'sensitive_allow': 'patch_review.review',
                 'consent': ['client_request', 'client_repository'], 'model': model}
        out.append({'requirement': requirement, **({'check': check} if check else {}),
                    'untested': ' '.join(behaviour.split()), 'cheapest_check': call,
                    # Static needs are not observed grants or proof of available isolation.
                    'agent_can_run_now': not any(needs.values()), 'needs': needs,
                    'needs_person': person or ('allow patch_review.review; consent and isolation must be admitted'
                                              + ('; configure a model to propose a check' if model else ''))})

    for req in requirements:
        if req.get('status') != 'not_established':
            continue
        current = next(r for r in stated if r['id'] == req['id'])
        args = {'repo': repo, 'task': task, 'requirements': [current]}
        # With no test binding, G6 can ask its configured proposer for one; no call is made here.
        model = not bool(current['checks'])
        if model:
            args['search'] = {'propose': True}
        entry(req['id'], req['text'], {'tool': 'g6_review_patch', 'arguments': args}, model=model)
    completion = assessment.get('completion', {})
    for failure in completion.get('defects_reproduced', {}).get('proposed_checks', []):
        if failure.get('result') not in ('counterexample', 'unconfirmed_failure'):
            continue
        reproduction = failure.get('reproduction', {})
        ident = failure.get('requirement', '')
        requirement = next((r for r in stated if r['id'] == ident),
                           {'id': ident or 'retained', 'text': 'The previously reproduced expectation holds', 'checks': []})
        args = {'repo': repo, 'task': task, 'requirements': [requirement]}
        if reproduction.get('text'):
            args['proposed_checks'] = [{'id': 'settle-reproduction', 'requirement_id': requirement['id'],
                'intent': 'preserve' if failure['result'] == 'counterexample' else 'change',
                'file': 'test_settle_reproduction.py', 'source': str(reproduction['text'])}]
        entry(ident, f"The expectation reproduced by {failure.get('check', 'the proposed check')} is unconfirmed.",
              {'tool': 'g6_review_patch', 'arguments': args}, check=failure.get('check', ''),
              person=f"confirm whether the reproduced expectation for {ident or 'the retained check'} is intended "
                     'through g6_confirm_behaviour')
    return copy.deepcopy(out)
