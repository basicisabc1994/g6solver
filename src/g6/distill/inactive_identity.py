# SPDX-License-Identifier: Apache-2.0
"""Read existing inactive identities and recorded bodies, without judging authority.

No registration, oracle, evaluator or threshold writer is reachable here. A caller
supplies its ordinary executor for comparisons; this module grants no exemption.
"""
from __future__ import annotations

from g6.core.decoder_limits import json_value as _decode_json

import ast
import re

from g6.core.ledger import digest
from g6.core.storage import state_path

from .detectors import SEED_TEMPLATES
from .ledger import Stores
from .records import FailureRecord, LibraryEntry, VerifierCandidate


def execution_contract(candidate):
    contract = candidate.contract.model_dump(mode='json', exclude={
        'assumptions', 'escape_family', 'withdrawal_conditions'})
    for name in ('languages', 'frameworks', 'inputs', 'verdict_kinds'):
        contract[name] = sorted(set(contract[name]))
    return contract


def predicate_identity(source):
    return ast.dump(ast.parse(source), include_attributes=False)


def method_identity(candidate, stack=(), *, resolve=None):
    if resolve is None:
        def resolve(ref):
            entry = Stores().for_model(LibraryEntry).get(ref)
            return Stores().for_model(VerifierCandidate).get(entry.candidate_id)
    if candidate.candidate_id in stack or len(stack) >= 16:
        raise ValueError('inactive method identity has a cyclic or over-deep dependency')
    stack = (*stack, candidate.candidate_id)
    contract = execution_contract(candidate)
    if candidate.form == 'predicate':
        body = {'ast': predicate_identity(candidate.body)}
    elif candidate.form == 'template_instance':
        seed = SEED_TEMPLATES[candidate.body.template]
        body = {'oracle': 'weakening.detect_test_weakening', 'supported': seed.supported,
                'reason_fragments': seed.reason_fragments, 'bindings': candidate.body.bindings}
    else:
        refs = [candidate.body] if candidate.form == 'reuse' else candidate.body.entries
        children = []
        for ref in refs:
            child = resolve(ref)
            child_identity = method_identity(child, stack, resolve=resolve)
            if len(refs) == 1 and contract == execution_contract(child):
                return child_identity
            children.append(child_identity)
        body = {'children': children}
        if candidate.form == 'composition':
            body.update(op=candidate.body.op, k=candidate.body.k)
    return digest({'form': candidate.form, 'body': body, 'execution_contract': contract})


def _case(raw, ident):
    if (not isinstance(raw, dict) or digest(raw) != ident
            or any(not isinstance(raw.get(key), str) or not raw[key]
                   for key in ('before_tests', 'after_tests'))):
        raise ValueError('recorded case digest or body changed')
    return raw


def _recorded_case(ident):
    if not re.fullmatch(r'[0-9a-f]{64}', ident):
        raise ValueError('invalid recorded case reference')
    custody = state_path('distill', 'held_out')
    archived = custody / 'regression_cases' / f'{ident}.json'
    path = archived if archived.exists() else custody / 'cases' / f'{ident}.json'
    # Same custody limits as the evaluator reader, with no import of its code.
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('linked recorded input')
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('oversized recorded input')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('non-finite JSON constant')

    raw = _decode_json(path.read_bytes().decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    return _case(raw, ident)


def recorded_cases(entry):
    """Only bodies already named by this inactive entry; no discovery or labels."""
    if (entry.status == 'withdrawn' and entry.reason == 'own regression suite no longer reproduces'
            and not entry.inactive_cases):
        raise ValueError('legacy withdrawal counterexample verdict unavailable')
    expected = {row.case_ref: row.verdict for row in entry.regression_suite}
    expected.update((row.case_ref, row.verdict) for row in entry.inactive_cases)
    cases = {ident: _recorded_case(ident) for ident in expected}
    for ident in entry.held_recurrences:
        failure = Stores().for_model(FailureRecord).get(ident)
        if failure.family != entry.family:
            raise ValueError('recorded failure family changed')
        if not any(row.case_ref == failure.case_ref for row in entry.inactive_cases):
            expected.pop(failure.case_ref, None)
        if failure.case_ref in cases:
            continue
        raw = failure.provenance.get('case')
        cases[failure.case_ref] = (_case(raw, failure.case_ref)
                                  if isinstance(raw, dict) and 'before_tests' in raw
                                  else _recorded_case(failure.case_ref))
    if not cases:
        raise ValueError('inactive recorded cases unavailable')
    return cases, expected
