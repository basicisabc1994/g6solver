"""Run the retained outside regressions with synthetic scratch folders, never a real boundary.

The assertions are the original tests' assertions. This directory's existing fixture supplies ordinary temporary
folders so Windows owner-only scratch-folder refusals cannot mask a changed reason. Every execution stays faked.
"""
import pytest

from tests.harness import test_confirmed_enforcement as enforcement
from tests.harness import test_confirmed_evidence_bound as evidence
from tests.harness import test_inert_names as names
from tests.harness import test_patch_report as report
from tests.harness.test_confirmed_enforcement import repo as enforcement_repo  # noqa: F401
from tests.harness.test_inert_names import repo as names_repo  # noqa: F401
from tests.harness.test_patch_report import repo as report_repo  # noqa: F401


@pytest.mark.parametrize('name', [
    'test_the_confirmed_case_is_its_one_example_v1_check_run_on_the_patch_and_counted_by_the_issuer',
    'test_a_confirmed_case_that_fails_on_the_patch_is_a_reproduced_defect',
    'test_a_confirmed_case_the_base_commit_already_has_is_enforced_and_not_counted',
])
def test_retained_confirmed_enforcement(enforcement_repo, monkeypatch, name):  # noqa: F811
    getattr(enforcement, name)(enforcement_repo, monkeypatch)


def test_retained_substituted_copy(tmp_path, monkeypatch):
    evidence.test_finding_8_a_run_on_a_copy_that_is_not_the_artefact_is_refused_and_counts_for_nothing(
        tmp_path, monkeypatch)


@pytest.mark.parametrize('statuses', [['passed', 'failed', 'passed'], ['passed', 'passed']])
def test_retained_supported_replays(tmp_path, monkeypatch, statuses):
    evidence.test_every_run_on_the_patch_reaches_the_outcome(
        tmp_path, monkeypatch, statuses, 'supported_within_scope')


def test_retained_ignored_name(names_repo, monkeypatch):  # noqa: F811
    names.test_an_ignored_files_name_is_one_inert_line_in_the_next_actions(names_repo, monkeypatch)


def test_retained_hostile_report(report_repo, monkeypatch):  # noqa: F811
    report.test_text_the_repository_or_the_caller_wrote_is_shown_inert(report_repo, monkeypatch)
