"""Finding (consent merge into the 0.3 line): the owned host launch passes the sharing check.

The refusals follow-up made ``usage._complete_host`` the one path that starts Claude Code or Codex for a watched host,
and consent put its check in ``consent_callers._Consent.complete``, which that path never passes. A watched host with no
grant must start no program and leave the consent refusal on the loop's record; with a grant it starts once and the
disclosure ledger holds one attempted disclosure. The programs here are fakes: nothing real is started.
"""
import json
import threading
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


@pytest.fixture
def lookups(monkeypatch):
    """Each lookup of a host program, recorded; the lookup itself is the real one, which under the suite's guard finds no
    program. Nothing is started."""
    looked = []
    for kind in (host_models.ClaudeCodeCaller, host_models.CodexCaller):
        def locate(self, _real=kind.locate):
            looked.append(type(self).__name__)
            return _real(self)
        monkeypatch.setattr(kind, "locate", locate)
    monkeypatch.setattr(host_models, "_run", lambda *args: pytest.fail("a host program was started"))
    return looked


@pytest.mark.parametrize("base,provider", HOSTS)
def test_a_watched_host_with_no_grant_is_refused_for_consent_before_its_program_is_looked_for(lookups, base, provider):
    with pytest.raises(host_models.LLMError, match=cloud.REQUIRED):
        base().complete("system", "PLANTED_UNGRANTED")
    assert lookups == [], "the program was looked for before the sharing check refused"
    assert cloud.disclosures() == [] and not usage._SENT


@pytest.mark.parametrize("base,provider", HOSTS)
def test_a_watched_host_with_a_grant_and_no_program_says_the_program_is_missing(lookups, accepted_cloud_dialog, base,
                                                                                  provider):
    accepted_cloud_dialog("model_inputs", provider, text="synthetic host request")
    with pytest.raises(host_models.LLMError, match="G6_E_OUTSIDE_PROGRAM_MISSING"):
        base().complete("system", "request")
    assert len(lookups) == 1 and cloud.disclosures() == [] and not usage._SENT


def test_a_session_with_no_grant_is_refused_for_consent_before_its_program_is_looked_for(lookups, tmp_path):
    meter = loop._Requests()
    meter.open(10)
    with pytest.raises(loop._Refused, match=cloud.REQUIRED):
        loop._Counted(Scout(tmp_path), meter).complete("system", "user")
    assert lookups == [] and meter.refusal["code"] == cloud.REQUIRED and cloud.disclosures() == []


def test_a_send_with_no_grant_is_refused_while_another_send_is_out(programs, accepted_cloud_dialog, monkeypatch):
    """The check before the program is looked for reads the grants; it does not wait for a send that is out (which holds
    the sharing lock until its program answers)."""
    accepted_cloud_dialog("model_inputs", "anthropic:claude_code", text="synthetic host request")
    out, release, answers = threading.Event(), threading.Event(), {}
    answer = host_models._run

    def held(*args):
        out.set()
        assert release.wait(10)
        return answer(*args)
    monkeypatch.setattr(host_models, "_run", held)

    def call(name, host, user):
        try:
            answers[name] = host.complete("system", user)
        except Exception as exc:                     # noqa: BLE001 - the refusal is the result looked at
            answers[name] = exc
    first = threading.Thread(target=call, args=("first", usage.WatchedClaude(), "request"), daemon=True)
    first.start()
    try:
        assert out.wait(10), "the granted send never started its program"
        second = threading.Thread(target=call, args=("second", usage.WatchedCodex(), "PLANTED_UNGRANTED"), daemon=True)
        second.start()
        second.join(5)
        assert not second.is_alive(), "the send with no grant waited for the send that was out"
    finally:
        release.set()
        first.join(10)
    assert isinstance(answers["second"], host_models.LLMError) and cloud.REQUIRED in str(answers["second"])
    assert answers["first"] == "answer" and programs == ["request"]
    assert [row["provider"] for row in cloud.disclosures()] == ["anthropic:claude_code"]


@pytest.mark.parametrize("base,provider", HOSTS)
def test_a_grant_revoked_after_the_early_check_is_refused_at_admission(programs, accepted_cloud_dialog, monkeypatch,
                                                                         base, provider):
    """Passing the early check admits nothing: a grant revoked while the program is looked for is refused at admission,
    and nothing starts or is disclosed."""
    accepted_cloud_dialog("model_inputs", provider, text="synthetic host request")
    kind = host_models.ClaudeCodeCaller if base is usage.WatchedClaude else host_models.CodexCaller

    def locate(self):
        cloud.revoke("model_inputs", provider)
        return "fake-host"
    monkeypatch.setattr(kind, "locate", locate)
    with pytest.raises(host_models.LLMError, match=cloud.REQUIRED):
        base().complete("system", "request")
    assert programs == [] and cloud.disclosures() == [] and not usage._SENT


@pytest.mark.parametrize("base,provider", HOSTS)
def test_a_watched_host_is_its_provider_and_the_guard_leaves_it_as_it_is(base, provider):
    host = base()
    assert consent_callers.destination(host).provider == provider
    assert consent_callers.guard(host) is host and type(host) is base
    built = (host_models.ClaudeCodeCaller if base is usage.WatchedClaude else host_models.CodexCaller)()
    assert consent_callers.destination(built).provider == provider     # what the construction hook made of a native one
    assert consent_callers.guard(built) is built
