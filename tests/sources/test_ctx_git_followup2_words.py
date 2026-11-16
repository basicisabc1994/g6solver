# SPDX-License-Identifier: Apache-2.0
"""Git's failure streams may classify a refusal but must not become its explanation."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from g6.sources.ctx_git import ops, runner
from g6.sources.ctx_git.config import GitRefused


@pytest.mark.parametrize("stderr", [True, False])
@pytest.mark.parametrize("message,code", [
    (b"fatal: not a git repository: /private-folder/repo", "G6_E_GIT_NOT_A_REPOSITORY"),
    (b"fatal: unknown revision /private-folder/rev", "G6_E_GIT_BAD_REVISION"),
    (b"fatal: cannot open /private-folder/repo", "G6_E_GIT_FAILED"),
])
def test_runner_uses_git_failure_only_to_classify(tmp_path, monkeypatch, stderr, message, code):
    monkeypatch.setattr(runner, "git_program", lambda: "git")
    monkeypatch.setattr(runner.component_process, "window", lambda timeout: nullcontext())
    monkeypatch.setattr(runner.component_process, "run_contained", lambda *a, **k: SimpleNamespace(
        returncode=128, stdout=b"" if stderr else message, stderr=message if stderr else b""))
    with pytest.raises(GitRefused) as caught:
        runner._runner(tmp_path, {}, 1)("status")
    assert caught.value.code == code
    assert "private-folder" not in caught.value.reason
    assert "status" in caught.value.reason


@pytest.mark.parametrize("message,code", [
    (b"CONFLICT: /private-folder/repo", "G6_E_GIT_CONFLICT"),
    (b"cannot open /private-folder/repo", "G6_E_GIT_FAILED"),
])
def test_stash_pop_refusal_does_not_repeat_git_failure(tmp_path, message, code):
    def run(*args, **kwargs):
        if args[0] == "status":
            return runner.Done(0, b"", b"")
        assert args == ("stash", "pop")
        return runner.Done(1, b"", message)

    with pytest.raises(GitRefused) as caught:
        ops.stash_pop(run, tmp_path, ops.Call("stash_pop"))
    assert caught.value.code == code
    assert "private-folder" not in caught.value.reason
    assert "git keeps the stash" in caught.value.reason
