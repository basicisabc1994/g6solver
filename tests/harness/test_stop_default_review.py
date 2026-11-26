# SPDX-License-Identifier: Apache-2.0
"""The default Stop review reports locally without constructing a model provider."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud, host_models, keys, llm
from g6.core.ledger import digest
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.harness import verification_proposer as proposer
from g6.workforce import repo_tests


@pytest.fixture
def default_stop(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    change = repo_tests.Change(repo, "a" * 40, "a" * 40, "a" * 40, ("mod.py",), {}, {}, ("mod.py",), ())
    world = SimpleNamespace(repo=repo, base=change.base_commit, session=home / "completion_loop" / "default",
                            change=change, patch="the first patch", calls=[], approval={})
    monkeypatch.setattr(keys, "read", lambda: world.approval)
    monkeypatch.setattr(repo_tests, "checked_repo", lambda _: repo)
    monkeypatch.setattr(repo_tests, "change", lambda *a, **k: world.change)
    monkeypatch.setattr(repo_tests, "diff_text", lambda _: world.patch)
    monkeypatch.setattr(proposer, "boundary", lambda _: "")

    def review(root, **kwargs):
        world.calls.append((root, kwargs))
        return {"assessment": {"report": "Local checks: changed-module tests ran; weakened-test scan ran."}}

    def forbidden(*args, **kwargs):
        raise AssertionError("the default Stop path constructed or called a model provider")

    monkeypatch.setattr(loop, "review_patch", review)
    for module, names in ((loop, ("provider_from_env", "ToolSession", "_start_model_call")),
                          (llm, ("provider_from_env", "_post_json")),
                          (host_models, ("CodexCaller", "ClaudeCodeCaller", "_run")),
                          (cloud, ("admission",))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    return world


def test_default_stop_reviews_task_without_any_model_and_reports_seconds(default_stop, monkeypatch):
    world = default_stop
    times = iter((10.0, 12.5))
    monkeypatch.setattr(loop, "_clock", lambda: next(times))
    step = loop.advance(world.repo, "Keep value equal to one", world.base, world.session)
    assert len(world.calls) == 1
    assert world.calls[0][1] == {"task": "Keep value equal to one", "base": world.base,
                               "requirements": [{"id": "task", "text": "Keep value equal to one", "checks": []}],
                               "timeout_s": loop.REVIEW_S}
    assert step.action == loop.STOP and step.ending == "untested" and not step.code and not step.text
    assert "changed-module tests ran" in step.note and "2.5 seconds" in step.note
    assert "reviewer loop" in step.note and "G6_LOOP_CRITIC=codex" in step.note
    assert "Nothing was tested" not in step.note
    state = loop._load(world.session)
    assert state["reviewed"] == digest(world.patch)
    assert loop.read_report(world.session, state) == step.note
    loop.advance(world.repo, "Keep value equal to one", world.base, world.session)
    assert len(world.calls) == 1


@pytest.mark.parametrize("guard", ["no_python", "already_read", "empty_task", "no_boundary"])
def test_default_stop_preserves_guards_before_review(default_stop, monkeypatch, guard):
    world, task = default_stop, "Keep value equal to one"
    if guard == "no_python":
        world.change = replace(world.change, sources=())
    elif guard == "already_read":
        loop._save(world.session, {"reviewed": digest(world.patch)})
    elif guard == "empty_task":
        task = " "
    else:
        monkeypatch.setattr(proposer, "boundary", lambda _: "no boundary")
    step = loop.advance(world.repo, task, world.base, world.session)
    assert not world.calls and step.action == loop.STOP
    assert step.code == {"no_python": loop.NO_PYTHON, "already_read": "", "empty_task": "G6_E_INPUT_VALIDATION",
                         "no_boundary": "G6_E_ISOLATION_UNAVAILABLE"}[guard]


def test_default_stop_rearmed_digest_skips_unchanged_but_reviews_new_patch(default_stop):
    world = default_stop
    loop.advance(world.repo, "task", world.base, world.session)
    state = loop._load(world.session)
    loop._save(world.session, {"reviewed": state.get("reviewed")})
    assert loop.advance(world.repo, "task", world.base, world.session).note == loop.ALREADY_READ
    world.patch = "the second patch"
    loop.advance(world.repo, "task", world.base, world.session)
    assert len(world.calls) == 2


def test_hook_shows_default_report_without_blocking_or_repair_feedback(default_stop, monkeypatch):
    world = default_stop
    monkeypatch.setattr(hook, "folder", lambda _: world.session)
    monkeypatch.setattr(hook, "current", lambda *a: world.session)
    monkeypatch.setattr(hook, "_task", lambda _: {"repo": str(world.repo), "base": world.base, "task": "task"})
    world.session.mkdir(parents=True)
    (world.session / "task.json").write_text("{}")
    from g6.harness.workflow import policy
    monkeypatch.setattr(policy, "enabled", lambda: (False, "off"))
    result = hook.on_stop({"session_id": "default", "cwd": str(world.repo)})
    assert set(result) == {"systemMessage"}
    assert "changed-module tests ran" in result["systemMessage"]
    assert (world.session / hook.TURN_ENDED).is_file()


@pytest.mark.parametrize("approval", [
    {"G6_LOOP_MODE": "scouts", "G6_LOOP_CRITIC": "codex"},
    {"G6_LLM_PROVIDER": "openai"}, {}])
def test_no_approved_scout_constructs_no_provider_even_with_other_configuration(default_stop, monkeypatch, approval):
    world = default_stop
    world.approval = approval
    monkeypatch.setenv("G6_LLM_PROVIDER", "openai")
    step = loop.advance(world.repo, "task", world.base, world.session)
    assert step.action == loop.STOP and step.ending == "untested" and len(world.calls) == 1
