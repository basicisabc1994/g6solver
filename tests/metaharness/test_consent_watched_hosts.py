"""Finding (consent merge into the 0.3 line): the owned host launch passes the sharing check.

The refusals follow-up made ``usage._complete_host`` the one path that starts Claude Code or Codex for a watched host,
and consent put its check in ``consent_callers._Consent.complete``, which that path never passes. A watched host with no
grant must start no program and leave the consent refusal on the loop's record; with a grant it starts once and the
disclosure ledger holds one attempted disclosure. The programs here are fakes: nothing real is started.
"""
import json
from contextlib import contextmanager
from uuid import uuid4

import pytest

from g6.core import cloud_consent as cloud, consent_callers, host_models
from g6.core.consent_callers import CallerWrapper
from g6.metaharness import loop
from g6.self_maintenance import usage

HOSTS = [(usage.WatchedClaude, "anthropic:claude_code"), (usage.WatchedCodex, "openai:codex")]


@pytest.fixture
def programs(monkeypatch, tmp_path):
    """Fake host programs: each start is recorded, and each answers as its real program would."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    started = []

    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    def run(argv, stdin, cwd, files, *rest):
        started.append(stdin)
        if "exec" in argv:
            (files / "last_message.txt").write_text("answer", encoding="utf-8")
            item = {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}}
            return 0, json.dumps(item) + "\n" + json.dumps({"type": "turn.completed"}) + "\n", ""
        return 0, json.dumps({"type": "result", "is_error": False, "result": "answer"}), ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    for kind in (host_models.ClaudeCodeCaller, host_models.CodexCaller):
        monkeypatch.setattr(kind, "locate", lambda self: "fake-host")
    return started


class Wrapper(CallerWrapper):
    """A request wrapper of a domain's own, delegating through G6's published contract."""


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("base,provider", HOSTS)
def test_a_watched_host_with_no_grant_starts_no_program(programs, base, provider, wrapped):
    host = base()
    meter = loop._Requests()
    meter.open(10)
    with pytest.raises(loop._Refused, match=cloud.REQUIRED):
        loop._Counted(Wrapper(host) if wrapped else host, meter).complete("system", "PLANTED_UNGRANTED")
    assert programs == [], "a watched host started its program with no grant"
    assert meter.refusal["code"] == cloud.REQUIRED
    assert cloud.disclosures() == []
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("base,provider", HOSTS)
def test_a_watched_host_with_a_grant_starts_once_with_one_disclosure(programs, accepted_cloud_dialog, base, provider,
                                                                      wrapped):
    accepted_cloud_dialog("model_inputs", provider, text="synthetic host request")
    host = base()
    meter = loop._Requests()
    meter.open(10)
    assert loop._Counted(Wrapper(host) if wrapped else host, meter).complete("system", "request") == "answer"
    assert len(programs) == 1
    assert [row["provider"] for row in cloud.disclosures()] == [provider]
    assert meter.refusal is None and not meter.in_flight and not usage._SENT


class Scout(CallerWrapper):
    """A domain's own caller that sends one scout prompt through a session (``ToolSession.run``), whatever it is asked."""

    def __init__(self, root) -> None:
        from g6.harness.completion_loop import ToolSession

        super().__init__(ToolSession())
        self.root = root

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        return self.inner.run("PLANTED_SCOUT_PROMPT", self.root, self.root, 1)


def test_a_session_with_no_grant_starts_no_program(programs, tmp_path):
    meter = loop._Requests()
    meter.open(10)
    with pytest.raises(loop._Refused, match=cloud.REQUIRED):
        loop._Counted(Scout(tmp_path), meter).complete("system", "user")
    assert programs == [] and meter.refusal["code"] == cloud.REQUIRED and cloud.disclosures() == []


def test_a_session_with_its_grants_starts_once_with_one_disclosure(programs, accepted_cloud_dialog, tmp_path):
    for scope in (cloud.repository_source(tmp_path), "model_inputs", "agent_environment"):
        accepted_cloud_dialog(scope, "anthropic:claude_code", text="synthetic scout request")
    meter = loop._Requests()
    meter.open(10)
    assert loop._Counted(Scout(tmp_path), meter).complete("system", "user") == "answer"
    assert programs == ["PLANTED_SCOUT_PROMPT"]
    assert [row["provider"] for row in cloud.disclosures()] == ["anthropic:claude_code"]   # the step is the run's send


@pytest.mark.parametrize("step", ["watched", "own"])
def test_a_session_launch_step_outside_its_run_sends_nothing(programs, accepted_cloud_dialog, tmp_path, step):
    from g6.harness.completion_loop import ToolSession

    accepted_cloud_dialog("model_inputs", "anthropic:claude_code", text="synthetic step request")
    session, files = ToolSession(), tmp_path / "step" / "files"
    files.mkdir(parents=True)
    launch = session._launch if step == "watched" else ToolSession._launch.__get__(session)
    with pytest.raises(Exception, match=cloud.REQUIRED):
        launch(["fake-host", "-p", "--output-format", "json"], "PLANTED_STEP", tmp_path, files, {}, 1.0)
    assert programs == [] and cloud.disclosures() == []


@pytest.mark.parametrize("base,provider", HOSTS)
def test_a_watched_host_is_its_provider_and_the_guard_leaves_it_as_it_is(base, provider):
    host = base()
    assert consent_callers.destination(host).provider == provider
    assert consent_callers.guard(host) is host and type(host) is base
    built = (host_models.ClaudeCodeCaller if base is usage.WatchedClaude else host_models.CodexCaller)()
    assert consent_callers.destination(built).provider == provider     # what the construction hook made of a native one
    assert consent_callers.guard(built) is built
