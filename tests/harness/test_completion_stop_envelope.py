# SPDX-License-Identifier: Apache-2.0
"""The Stop entry point always carries G6 refusals in its JSON envelope."""
import pytest

from g6.core.storage import PathRefused
from g6.harness import completion_hook as hook
from g6.workforce.repo_tests import RepoRefused
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused


@pytest.mark.parametrize("refusal, code", [
    (WorkspaceRefused(OUTSIDE_ROOT, "folder refused"), OUTSIDE_ROOT),
    (PathRefused("folder refused", outside=True), OUTSIDE_ROOT),
    (RepoRefused("G6_E_GIT_COMMAND_FAILED", "git timed out"), "G6_E_GIT_COMMAND_FAILED"),
    (ValueError("G6_E_NO_CLEAN_REVIEW: unreadable state"), "G6_E_NO_CLEAN_REVIEW"),
    (RuntimeError("[G6_E_NO_CLEAN_REVIEW] unavailable retained state"), "G6_E_NO_CLEAN_REVIEW"),
])
def test_first_line_refusals_keep_the_stop_envelope(monkeypatch, refusal, code):
    def refused(*args):
        raise refusal

    monkeypatch.setattr(hook, "folder", refused)
    reply = hook.on_stop({"session_id": "refused", "cwd": "."})
    assert set(reply) == {"systemMessage"} and code in reply["systemMessage"]
