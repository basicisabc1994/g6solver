"""Every file decoder and same-module wrapper must contain both resource limits."""
from pathlib import Path

from tests.s16r3.json_read_census import census


# Certificate-bound source files with relevant calls. These require an issuance-window repair. Every entry is a file a
# component certificate binds (test_every_bound_entry_is_a_certificate_input): a name is never enough.
BOUND = {
    'evidence/certificate.py',
    'ml/adapt_bayesian/certificate/second_route.py',
    '__init__.py',
    'action_gating/__init__.py',
    'action_gating/action_gating_block.py',
    'action_gating/audit.py',
    'action_gating/audit_store.py',
    'action_gating/capabilities.py',
    'action_gating/classifiers.py',
    'action_gating/decision_engine.py',
    'action_gating/explain.py',
    'action_gating/mitigation.py',
    'action_gating/normalizer.py',
    'action_gating/operation_budget.py',
    'action_gating/provenance.py',
    'action_gating/qa.py',
    'action_gating/safeguards.py',
    'action_gating/scenario_modeler.py',
    'action_gating/schema.py',
    'action_gating/scope.py',
    'action_gating/uncertainty.py',
    'action_gating/user_model.py',
    'action_logic/__init__.py',
    'action_logic/block.py',
    'action_logic/controls.py',
    'action_logic/hypotheses.py',
    'action_logic/schema.py',
    'core/__init__.py',
    'core/ai_block.py',
    'core/approval_authority.py',
    'core/approval_store.py',
    'core/binomial.py',
    'core/block_contract.py',
    'core/choices.py',
    'core/cloud_consent.py',
    'core/component_certificate.py',
    'core/component_process.py',
    'core/confine.py',
    'core/consent_callers.py',
    'core/consent_declarations.py',
    'core/consent_registry.py',
    'core/contract_decorator.py',
    'core/decision_record.py',
    'core/door.py',
    'core/env.py',
    'core/errors.py',
    'core/evidence.py',
    'core/evidence_grade.py',
    'core/extension_points.py',
    'core/failure_modes.py',
    'core/git_env.py',
    'core/host_models.py',
    'core/keys.py',
    'core/ledger.py',
    'core/llm.py',
    'core/maturity.py',
    'core/model_calls.py',
    'core/objective_contract.py',
    'core/outside.py',
    'core/process_tree.py',
    'core/promotion_custody.py',
    'core/promotion_evaluator.py',
    'core/protocols.py',
    'core/registry.py',
    'core/reliability_envelope.py',
    'core/resources.py',
    'core/result.py',
    'core/risk_policy.py',
    'core/sandbox.py',
    'core/special.py',
    'core/state_surface.py',
    'core/stop.py',
    'core/storage.py',
    'core/validation_gates.py',
    'core/verification/__init__.py',
    'core/vetting.py',
    'csf/__init__.py',
    'csf/composition.py',
    'csf/ltl.py',
    'csf/policy.py',
    'csf/rollback.py',
    'csf/schema.py',
    'csf/trace.py',
    'csf/verifier.py',
    'epistemic_integrity/__init__.py',
    'epistemic_integrity/adjudicator.py',
    'epistemic_integrity/epistemic_integrity_block.py',
    'epistemic_integrity/markers.py',
    'evidence/__init__.py',
    'evidence/certificate.py',
    'guards/__init__.py',
    'guards/patterns.py',
    'guards/source_scan.py',
    'invariants/__init__.py',
    'invariants/envelope.py',
    'ml/adapt_sklearn/__init__.py',
    'ml/adapt_sklearn/block.py',
    'ml/adapt_sklearn/estimators.py',
    'workforce/_certificate_formal.py',
    'workforce/_certificate_runner.py',
    'workforce/component_certificate.py',
    # bound by component certificates (duty_of_care, adapt_sklearn): their limit guards wait for the issuance window
    'jobs/_care/crisis.py',
    'jobs/_care/directory.py',
    'jobs/_care/instruments.py',
    'ml/_fit.py',
    'ml/adapt_automl/flaml_child.py',
}
# Not bound by a certificate: its helpers are compiled into confined example children whose bytes are pinned
# (repair_question4); a decoder failure there ends the child, which the parent reads as a failed check.
PINNED_CHILD = {'harness/behaviour_runner.py'}


def test_every_bound_entry_is_a_certificate_input():
    """An exemption names only a file a certificate binds (review 4 of the reply change, finding 3: the knowledge
    manifest's store reader was exempted by its name)."""
    from tests.s16r3.certificate_inputs import certificate_inputs

    assert not BOUND - certificate_inputs(), sorted(BOUND - certificate_inputs())
    assert not PINNED_CHILD & certificate_inputs()


def test_all_file_json_and_ledger_helpers_hold_decoder_limits():
    root = Path(__file__).resolve().parents[2] / 'src' / 'g6'
    missing = []
    for path in sorted(root.rglob('*.py')):
        rel = path.relative_to(root).as_posix()
        if rel in BOUND | PINNED_CHILD:
            continue
        for function, line, call, protected in census(path.read_text(encoding='utf-8')):
            if not protected:
                missing.append(f'{rel}:{line} {function}: {call}')
    assert not missing, '\n' + '\n'.join(missing)


def test_census_follows_two_helpers_and_import_aliases():
    source = '''
import json as decoder
from g6.core.ledger import ChainLedger as Chain
def parse(raw):
    return decoder.loads(raw)
def middle(raw):
    return parse(raw)
def read(path):
    return middle(path.read_bytes())
def ledger(path):
    return Chain(path)
'''
    assert {(fn, call) for fn, _line, call, protected in census(source) if not protected} == {
        ('parse', 'decoder.loads'), ('middle', 'parse'), ('read', 'middle'), ('ledger', 'Chain')}


def test_census_ignores_handlers_on_else_finally_and_sibling_calls():
    source = '''
import json
def read(path):
    try:
        pass
    except (RecursionError, MemoryError):
        pass
    else:
        json.loads(path.read_bytes())
    finally:
        json.loads(path.read_bytes())
    json.loads(path.read_bytes())
'''
    assert len(census(source)) == 3
    assert not any(row[3] for row in census(source))


def test_census_never_classifies_a_decoder_helper_out_of_scope():
    source = '''
from json import loads as decode
def parse(raw):
    return decode(raw)
def helper(raw):
    return parse(raw)
'''
    assert {(fn, call) for fn, _line, call, protected in census(source) if not protected} == {
        ('parse', 'decode'), ('helper', 'parse')}


def test_census_tracks_renamed_ledger_handles():
    source = '''
from g6.core.ledger import ChainLedger
def reader(path):
    try:
        x = ChainLedger(path)
    except Exception:
        raise ValueError('unreadable') from None
    return x.entries()
'''
    assert [(fn, call) for fn, _line, call, protected in census(source) if not protected] == [('reader', 'x.entries')]


def test_census_does_not_accept_a_handler_that_rethrows_the_resource_error():
    source = '''
import json
def read(path):
    try:
        return json.loads(path.read_bytes())
    except Exception:
        raise
'''
    assert not census(source)[0][3]


def test_census_lists_contained_helpers_too():
    source = '''
import json
def parse(raw):
    try:
        return json.loads(raw)
    except (RuntimeError, MemoryError):
        raise ValueError('unreadable') from None
def helper(raw):
    return parse(raw)
'''
    assert {(fn, call, protected) for fn, _line, call, protected in census(source)} == {
        ('parse', 'json.loads', True), ('helper', 'parse', True)}


def test_census_respects_first_matching_exception_handler():
    source = '''
import json
def read(path):
    try:
        return json.loads(path.read_bytes())
    except Exception:
        raise
    except (RecursionError, MemoryError):
        raise ValueError('unreadable') from None
'''
    assert not census(source)[0][3]
