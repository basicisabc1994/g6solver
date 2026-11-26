# SPDX-License-Identifier: Apache-2.0
"""Pure gate acceptance, separate from filesystem persistence/permissions tests."""
import pytest

from tests.self_training import test_research_merge_gate as checks


@pytest.mark.parametrize('mutation', ['red', 'skipped', 'missing', 'wrong_commit', 'same_family',
    'unresolved', 'missing_ruleset', 'missing_codeowners', 'bot_workflows', 'untrusted_status',
    'stale', 'predicted', 'no_gain', 'cost_regression', 'false_accept_regression', 'wrong_policy', 'spent'])
def test_measured_evidence_is_required_in_memory(mutation):
    checks.test_incomplete_evidence_never_merges(None, mutation)


@pytest.mark.parametrize('path', ['src/g6/self_training/merge_gate.py', 'src/g6/core/llm.py',
    '.github/workflows/ci.yml', 'CODEOWNERS', 'validation/distill/held_out.json',
    'src/g6/self_model/risk_gate.py', 'tests/test_existing.py', '../outside.py', 'C:/outside.py'])
def test_protected_paths_never_merge_in_memory(path):
    checks.test_protected_bound_and_ambiguous_diff_stays_draft(None, path)


def test_signed_generation_and_external_effect_in_memory():
    checks.test_complete_host_evidence_merges_and_issues_scoped_generation(None)


def test_only_new_tests_are_exempt_in_memory():
    checks.test_brand_new_test_is_allowed_but_not_conftest_or_bound_file(None)


def test_signed_evidence_cannot_be_edited_in_memory():
    checks.test_evidence_signature_binds_every_measured_field(None)


def test_measured_regression_is_idempotent_in_memory():
    checks.test_regression_opens_one_revert_and_cooldown_survives_restart(None)


def test_stop_is_checked_before_external_merge_in_memory():
    checks.test_stop_before_merge_side_effect(None)
