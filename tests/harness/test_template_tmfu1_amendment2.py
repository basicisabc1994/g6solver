"""Amendment 2: retain the old assertions with compiler/producer-owned failures.

The recorded executor seams remain synthetic. Only their invented failure messages
are replaced: run the fixed compiler's failure function and the real report hooks.
The original tests still assert classification, custody, reproduction and envelopes.
"""
import ast
import io
import json
import subprocess
import sys
from contextlib import nullcontext, redirect_stdout
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as search
from g6.harness import verification_proposer
from g6.workforce import repo_tests
from tests.harness import test_verification_formal as formal_cases
from tests.harness import test_verification_proposer as proposer_cases
from tests.harness import test_verification_templates as template_cases
from tests.harness.test_template_classification_repair3 import counted_producer
from tests.harness.test_template_runtime_binding import SOURCE, _compiled
from tests.repair_question4 import conftest as repair_fixtures
from tests.repair_question4 import test_finding_2 as control_cases
from tests.repair_question4 import test_followup_roots as later_cases
from tests.repair_question4 import test_resume_inventory as resume_cases

pytestmark = pytest.mark.usefixtures('declared_mcp_grants')


@pytest.fixture
def repair_question_isolation(tmp_path, monkeypatch):
    # Calling a test from another directory does not inherit its conftest fixtures.
    # Retain that directory's original home and ordinary-directory fixture too.
    repair_fixtures.review_isolation.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize(('statement', 'code'), [
    ('raise SystemExit(2)', 'G6_E_CHECK_SETUP: constructor'),
    ('raise GeneratorExit()', 'G6_E_CHECK_SETUP: constructor'),
    ('raise asyncio.CancelledError()', 'G6_E_CHECK_SETUP: constructor'),
    ('raise concurrent.futures.CancelledError()', 'G6_E_CHECK_SETUP: constructor'),
    ('raise ValueError("private setup text")', 'G6_E_OBSERVATION_UNAVAILABLE:'),
], ids=['exit', 'generator', 'async-cancel', 'future-cancel', 'ordinary-exception'])
def test_differential_constructor_keeps_the_setup_code_split(tmp_path, statement, code):
    _, data = _compiled('differential.v1')
    (tmp_path / 'runtime_fixture.py').write_text(
        'import asyncio, concurrent.futures\n' + SOURCE.replace('# REPLACEMENT', statement), encoding='utf-8')
    boot, channel = tmp_path / 'bootstrap.py', tmp_path / 'result.bin'
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_bytes(b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=30)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found['result'] == 'error', (found, ran.stderr)
    if code.endswith(':'):
        assert found['detail'].startswith(code), found
    else:
        assert found['detail'] == code, found
    assert found['reason_code'] == code.partition(':')[0]
    assert 'observations' not in found and 'observed' not in found


class CompilerFailures:
    """Keep executor decisions and observations; replace only failure ownership.

    Compile fixed fixture functions before calling the original test, including
    its exec/eval guards. No supplied replay/model text is executed by this seam.
    Repository execution is still the original recorded boundary, not an OS test.
    """

    def __init__(self, tmp_path, monkeypatch):
        self.patch = monkeypatch
        self.counted_type = type(counted_producer(tmp_path))
        self.scopes = {template: _compiled(template)[0] for template in (
            'relation.v1', 'state_invariant.v1', 'example.v1')}
        self.produced = []

    def __getattr__(self, name):
        return getattr(self.patch, name)

    def setattr(self, target, name, value, *args, **kwargs):
        if target is search and name == '_run_check':
            def execute(change, placed, source, node, **given):
                raw = value(change, placed, source, node, **given)
                return self.record(raw, source)
            self.patch.setattr(target, name, execute, *args, **kwargs)
        else:
            self.patch.setattr(target, name, value, *args, **kwargs)

    def record(self, raw, source):
        messages, errors = dict(raw.get('failure_messages', {})), dict(raw.get('check_errors', {}))
        produced_before = len(self.produced)
        for node in raw.get('failed_ids', []):
            template = search._generated_template(node)
            if template not in self.scopes:
                continue
            bindings = next(ast.literal_eval(item.value) for item in ast.parse(source).body
                            if isinstance(item, ast.Assign) and len(item.targets) == 1
                            and isinstance(item.targets[0], ast.Name) and item.targets[0].id == 'BINDINGS')
            scope = self.scopes[template]
            scope['BINDINGS'] = bindings
            output = io.StringIO()
            if template == 'state_invariant.v1':
                observations = raw['probe_observations']
                assert isinstance(observations, list) and observations
                outcomes = iter(item['output'] for item in observations)
                scope['_replay'] = lambda trace: next(outcomes)

                def invoke():
                    scope['test_replay'](SimpleNamespace(disabled=nullcontext))
            elif template == 'relation.v1':
                # Each bound relation really fails on these operation results.
                scope['_load'] = lambda spec: lambda value: value + '!' if isinstance(
                    value, str) else value + 1
                invoke = scope['test_relation']
            else:
                _, data = runner.bindings(source)
                calls = data['cases']
                frame = runner.frame({'version': 'observation.v1', 'errors': {}, 'outcomes': [
                    {'returned': '-1', 'arguments': [json.dumps(arg) for arg in call['args']]}
                    for call in calls]})
                compared = runner.finish(0, frame, data)
                assert compared['result'] == 'failed', compared

                def check_example():
                    raise AssertionError(compared['detail'])
                scope['_check_example'] = check_example
                invoke = scope['test_example']
            # The compiled function, pytest's crash formatter and the actual
            # producer determine the line; no test writes a failure-line literal.
            self.patch.setattr(sys, '_g6_check_setup_types', [], raising=False)
            with redirect_stdout(output), pytest.raises(pytest.fail.Exception) as caught:
                invoke()
            if template == 'state_invariant.v1':
                assert repo_tests.probe_observations(output.getvalue()) == observations
            report = SimpleNamespace(nodeid=node, when='call', outcome='failed', failed=True,
                                     longrepr=SimpleNamespace(reprcrash=caught.getrepr(style='short').reprcrash))
            counted = self.counted_type()
            counted.pytest_exception_interact(None, SimpleNamespace(excinfo=caught), report)
            counted.pytest_runtest_logreport(report)
            messages[node] = counted.messages[node]
            errors.update(counted.check_errors)
            self.produced.append((node, messages[node]))
        return ({**raw, 'failure_messages': messages, 'check_errors': errors}
                if len(self.produced) != produced_before else raw)


FORMAL_CASES = [
    ('test_a_reproduced_trace_is_an_unconfirmed_counterexample_with_all_three_parts', {}),
    ('test_a_preserve_trace_the_base_commit_breaks_too_accuses_nobody', {}),
    ('test_review_vs4_i1_a_trace_the_base_commit_cannot_replay_is_never_kept_at_the_base', {'held_at_base': ()}),
    ('test_review_vs4_i1_a_trace_the_base_commit_cannot_replay_is_never_kept_at_the_base', {'held_at_base': (0,)}),
    ('test_a_change_instance_is_replayed_on_the_patch_only', {}),
    ('test_a_replay_that_cannot_run_is_not_replayable_and_counted_never_a_finding', {}),
    ('test_no_compiled_or_model_text_runs_in_g6s_process_and_the_replay_holds_caller_text_only_in_its_literal', {}),
    ('test_the_multi_step_access_rule_catches_a_read_after_a_revoke_through_the_state_invariant_path', {}),
    ('test_a_z3_counterexample_is_re_run_confined_before_it_is_reported', {}),
    ('test_review_vs4_i3_each_symbolic_re_run_is_its_own_and_is_read_from_its_own_run', {}),
    ('test_over_mcp_a_state_invariant_keeps_the_envelope_and_codes', {}),
]
FORMAL_IDS = ['reproduced-three-parts', 'fails-on-base-too', 'none-replayable-at-base', 'one-kept-at-base',
              'change-patch-only', 'not-replayable-counted', 'no-code-in-host', 'read-after-revoke',
              'z3-confined-rerun', 'I3-own-rerun', 'state-mcp-envelope']


@pytest.mark.parametrize(('name', 'given'), FORMAL_CASES, ids=FORMAL_IDS)
def test_formal_classification_with_real_failure(tmp_path, monkeypatch, name, given):
    runs = CompilerFailures(tmp_path, monkeypatch)
    getattr(formal_cases, name)(tmp_path, runs, **given)
    assert runs.produced


@pytest.mark.parametrize(('name', 'given'), [
    ('test_a_compiled_counterexample_is_unconfirmed_evidence_with_its_reproduction', {}),
    ('test_a_proposed_or_compiled_check_whose_run_loaded_the_changed_code_elsewhere_is_a_check_error',
     {'side': 'base', 'seen': template_cases.SHADOWED, 'passed': False}),
    ('test_a_run_that_loaded_only_other_modules_from_outside_keeps_its_result', {}),
    ('test_the_mcp_tool_takes_template_instances_and_search_and_keeps_its_envelope_and_codes', {}),
], ids=['compiled-reproduction', 'changed-code-loaded-elsewhere', 'other-modules-outside', 'template-mcp-envelope'])
def test_template_classification_with_real_failure(tmp_path, monkeypatch, name, given):
    runs = CompilerFailures(tmp_path, monkeypatch)
    getattr(template_cases, name)(tmp_path, runs, **given)
    assert runs.produced


def test_proposed_classification_with_real_failure(tmp_path, monkeypatch):
    runs = CompilerFailures(tmp_path, monkeypatch)
    monkeypatch.setattr(verification_proposer, 'boundary', lambda root: '')
    proposer_cases.test_a_proposed_check_that_fails_gives_at_most_insufficient_evidence_with_its_reproduction(
        tmp_path, runs, ready=None)
    assert runs.produced


@pytest.mark.usefixtures('repair_question_isolation')
def test_later_control_with_real_failure(tmp_path, monkeypatch):
    runs = CompilerFailures(tmp_path, monkeypatch)
    control_cases.test_error_in_later_control_keeps_confirmed_patch_failure(tmp_path, runs)
    assert runs.produced


@pytest.mark.parametrize(('first', 'later'), [('failed', 'base'), ('passed', 'again'), ('failed', 'again'),
                                           ('failed', 'reproduction')],
                         ids=['base-failed', 'again-passed', 'again-failed', 'reproduction-failed'])
@pytest.mark.usefixtures('repair_question_isolation')
def test_later_error_with_real_failure(tmp_path, monkeypatch, first, later):
    runs = CompilerFailures(tmp_path, monkeypatch)
    later_cases.test_each_later_error_has_its_own_record(tmp_path, runs, first, later)
    assert runs.produced


@pytest.mark.parametrize('step', ['patch_decision', 'control_budget'],
                         ids=['patch_decision-failed', 'control_budget-failed'])
@pytest.mark.usefixtures('repair_question_isolation')
def test_later_decision_with_real_failure(tmp_path, monkeypatch, step):
    runs = CompilerFailures(tmp_path, monkeypatch)
    later_cases.test_later_decision_and_budget_errors_preserve_each_observation(tmp_path, runs, 'failed', step)
    assert runs.produced


@pytest.mark.parametrize('name', [
    'test_confirmed_reproduction_command_also_excludes_repository_pytest_hooks',
    'test_control_and_replay_receive_the_same_frozen_copy_binding',
], ids=['reproduction-excludes-hooks', 'three-frozen-bindings'])
@pytest.mark.usefixtures('repair_question_isolation')
def test_resumed_custody_with_real_failure(tmp_path, monkeypatch, name):
    runs = CompilerFailures(tmp_path, monkeypatch)
    getattr(resume_cases, name)(tmp_path, runs)
    assert runs.produced
