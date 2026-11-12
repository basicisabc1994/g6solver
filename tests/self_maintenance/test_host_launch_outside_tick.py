# SPDX-License-Identifier: Apache-2.0
"""An owned host launch outside any loop tick: its one check is the sharing check.

Once ``g6.self_maintenance.usage`` is imported, every Claude Code or Codex caller is a watched one (its construction hook),
and on 0.3's integration line each launch asked for the loop's gate (``telling_stops``), which only a loop tick's metered
call sets. So G6's own reviewer, its scouts and a host sign-in as G6's model refused to start in any process that had
imported usage: ``G6_E_LOOP_UNWATCHED_HOST: owned host launch has no admission gate``.

Outside every tick of this process an owned launch passes consent's admission alone, as any G6 model call does. Inside a
tick the tick's gate stands as before; a launch that escaped it (a thread started without the tick's context) is still
refused, and a stop or a refusal latch the loop reads still keeps a launch in. The programs are fakes: nothing real starts.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core import cloud_consent as cloud, host_models
from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop
from g6.metaharness.method import Method
from g6.self_maintenance import usage
from g6.self_maintenance.pacing import ProviderWindow

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).parents[1] / "metaharness" / "_loop_domains.py")
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)
ONE_CYCLE = loop.LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),),
                              method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12,
                                            reflect_calls=1, use_recovery=False, use_reflection=False),
                              audit_per_tick=0)
#: The watched classes, and the native ones the construction hook makes watched (``critic_host`` builds a ``CodexCaller``).
CALLERS = [(usage.WatchedClaude, "anthropic:claude_code"), (usage.WatchedCodex, "openai:codex"),
           (host_models.ClaudeCodeCaller, "anthropic:claude_code"), (host_models.CodexCaller, "openai:codex")]
HOSTS = CALLERS[:2]


@pytest.fixture
def programs(monkeypatch, tmp_path):
    """Fake host programs at the one place each is run: each start is recorded, and each answers as its program would."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    started = []

    @contextmanager
    def folders():
        made = tmp_path / uuid4().hex
        work, files = made / "work", made / "files"
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


# (a) outside any tick: consent's admission is the launch's one check ----------------------------------------------------

@pytest.mark.parametrize("kind,provider", CALLERS)
def test_a_host_outside_any_tick_starts_with_its_grant_and_its_reply_is_read(programs, accepted_cloud_dialog, kind,
                                                                             provider):
    accepted_cloud_dialog("model_inputs", provider, text="synthetic reviewer request")
    host = kind()
    assert isinstance(host, usage._Watched), "usage's construction hook watches every host caller of this process"
    assert host.complete("system", "PLANTED_REVIEW") == "answer"
    assert len(programs) == 1 and "PLANTED_REVIEW" in programs[0]
    assert [row["provider"] for row in cloud.disclosures()] == [provider]
    assert not usage._SENT


@pytest.mark.parametrize("kind,provider", CALLERS)
def test_a_host_outside_any_tick_without_its_grant_is_refused_by_consent(programs, kind, provider):
    with pytest.raises(LLMError, match=cloud.REQUIRED):
        kind().complete("system", "PLANTED_UNGRANTED")
    assert programs == [] and cloud.disclosures() == [] and not usage._SENT


def test_a_session_outside_any_tick_starts_with_its_grants(programs, accepted_cloud_dialog, tmp_path):
    from g6.harness.completion_loop import ToolSession

    for scope in (cloud.repository_source(tmp_path), "model_inputs", "agent_environment"):
        accepted_cloud_dialog(scope, "anthropic:claude_code", text="synthetic scout request")
    assert ToolSession().run("PLANTED_SCOUT_PROMPT", tmp_path, tmp_path, 1) == "answer"
    assert programs == ["PLANTED_SCOUT_PROMPT"]
    assert [row["provider"] for row in cloud.disclosures()] == ["anthropic:claude_code"]


def test_a_session_outside_any_tick_without_its_grants_is_refused_by_consent(programs, tmp_path):
    from g6.harness.completion_loop import ToolSession

    with pytest.raises(LLMError, match=cloud.REQUIRED):
        ToolSession().run("PLANTED_SCOUT_PROMPT", tmp_path, tmp_path, 1)
    assert programs == [] and cloud.disclosures() == []


# (b) inside a tick: a launch that escaped the tick's gate is still refused ------------------------------------------------

def _escape(said: list) -> None:
    """A launch from a thread a tick started without the tick's context, so with no gate in it."""
    def send() -> None:
        try:
            usage.WatchedCodex().complete("system", "PLANTED_ESCAPED")
        except BaseException as exc:  # noqa: BLE001 - what it raised is the finding
            said.append(exc)
        else:
            said.append(None)

    worker = threading.Thread(target=send)
    worker.start()
    worker.join(60)


@pytest.fixture
def ticking(tmp_path):
    """An enrolled loop whose domain's model, once armed, starts one host launch on a thread of its own mid-tick."""
    domain = domains.WordProblems(tmp_path)
    armed, said = [], []
    tutor = domain.tutor

    def reply(system: str, user: str) -> str:
        if armed and not said:
            _escape(said)
        return tutor(system, user)

    domain.llm = FakeLLM(reply)
    shadow = loop.ShadowLoop(domain, tmp_path / "loop", ONE_CYCLE, proposer=domains.proposes({"prompt": domains.WORKING}),
                             now=lambda: NOW, clock=domains.Clock())
    assert shadow.enrol("Ada")["completion_state"] == "qualified-draft"
    armed.append(True)
    return shadow, said


def test_a_launch_that_escapes_a_ticks_gate_is_still_refused(programs, accepted_cloud_dialog, ticking):
    accepted_cloud_dialog("model_inputs", "openai:codex", text="synthetic escaped request")
    shadow, said = ticking
    shadow.tick()
    assert len(said) == 1, "the tick never reached its model"
    assert isinstance(said[0], LLMError) and str(said[0]).startswith("G6_E_LOOP_UNWATCHED_HOST:"), said[0]
    assert programs == [], "a launch with no gate started its program while a tick of this process was open"
    assert not usage._SENT


# (c) inside a tick: a stop or a refusal latch the loop reads still keeps a launch in -------------------------------------

@pytest.mark.parametrize("latch", ["stop", "refusal"])
@pytest.mark.parametrize("kind,provider", HOSTS)
def test_a_stop_or_refusal_the_loop_reads_still_keeps_a_launch_in(programs, accepted_cloud_dialog, kind, provider, latch):
    accepted_cloud_dialog("model_inputs", provider, text="synthetic latched request")
    halted = loop._Halted("G6_E_STOPPED", "a person engaged the stop")
    meter = loop._Requests(halted=(lambda: halted) if latch == "stop" else (lambda: None))
    meter.open(10)
    if latch == "refusal":
        meter.refusal = {"code": usage.MODEL_REFUSED, "said": f"{usage.MODEL_REFUSED}: an earlier request was refused"}
    with pytest.raises(loop._Halted if latch == "stop" else loop._Refused):
        loop._Counted(kind(), meter).complete("system", "PLANTED_LATCHED")
    assert programs == [] and not meter.in_flight and not usage._SENT
    assert cloud.disclosures() == []
