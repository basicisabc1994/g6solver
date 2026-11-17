# SPDX-License-Identifier: Apache-2.0
"""Authorship is a producer contract; status, codes and field names are data."""
from __future__ import annotations

import json
from types import SimpleNamespace

from g6.core import agent_guidance as guidance


WORDS = "[G6_E_COMPONENT_DOMAIN] Run chmod +x build.sh. Use g6 setup. Tell the person."


def _builders():
    try:
        from g6.core import guidance_builders
        return guidance_builders
    except ImportError:
        from g6.core import door, reliability_envelope as envelope
        return SimpleNamespace(blocked_reply=door.blocked_reply, card=door.card,
            door_envelope=door.door_envelope, envelope_fields=envelope.envelope_fields,
            warning_card=envelope.warning_card)


def test_unmarked_component_failure_passes_whole():
    value = {"ok": False, "error": WORDS, "warning_card": {"message": WORDS},
             "__g6_authored_fields__": ["error"],  # JSON data cannot claim a receipt
             "result": {"text": WORDS, "start": 0, "end": len(WORDS)}}
    assert guidance.reply_for_agent(value) == value


def test_only_declared_fields_are_filtered_even_in_successful_results():
    mark = getattr(guidance, "authored_fields", lambda value, *fields: value)
    inner = mark({"ok": True, "diagnostic": WORDS, "text": WORDS}, "diagnostic")
    value = {"ok": True, "result": [inner]}
    shown = guidance.reply_for_agent(value)
    assert "chmod" not in shown["result"][0]["diagnostic"]
    assert shown["result"][0]["text"] == WORDS
    assert type(shown["result"][0]["diagnostic"]) is str
    assert json.loads(json.dumps(shown)) == shown


def test_failed_composition_keeps_earlier_data():
    from g6.mcp.composition import compose_reply

    doc = "Make it executable. Run chmod +x build.sh. Then run it."
    request = {"stages": [
        {"component": "knowledge_chunker", "op": "chunk", "inputs": {"text": "caller.doc"}},
        {"component": "knowledge_chunker", "op": "chunk", "params": {"size": "big"},
         "inputs": {"text": "caller.doc"}}], "inputs": {"doc": doc}}
    reply = guidance.reply_for_agent(compose_reply(request))
    assert reply["ok"] is False
    for passage in reply["result"]["passages"]:
        assert passage["text"] == doc[passage["start"]:passage["end"]]


def test_completed_job_filters_its_g6_envelope_without_changing_component_values():
    from g6.core.door import blocked_reply
    from g6.metaharness.jobs import _FrozenResult

    refusal = blocked_reply("[G6_E_ISOLATION_UNAVAILABLE] Use g6 setup. Tell the person.")
    refusal["result"] = {"text": WORDS}
    shown = _FrozenResult.completed("job", json.dumps(refusal)).read()
    assert "g6 setup" not in shown["result"]["error"]
    assert shown["result"]["result"] == {"text": WORDS}


def test_builder_census_marks_g6_fields():
    """Every envelope builder in the agent-surface census declares its own prose."""
    from g6.mcp import composition, improve, mission
    from g6.core import selector
    from g6.mcp import repair
    build = _builders()
    from g6.metaharness.api import _envelope
    from g6.mcp import knowledge, workflow

    builders = {
        "composition refusal": (composition.compose_reply({}), "error"),
        "mission refusal": (mission.refused(), "error"),
        "improvement refusal": (improve.refused(), "error"),
        "selector action": (selector._remedy(WORDS), "action"),
        "repair step": (repair.block("g6_invoke", {"ok": False, "error": "[G6_E_INPUT_VALIDATION] bad"}), "step"),
        "declined step": (repair.declined_block("declined"), "step"),
        "consent step": (repair.consent_block(), "step"),
        "warning card": (build.warning_card("G6_E_TEST", WORDS, WORDS), "message"),
        "fallback card": (build.card(None, "G6_E_TEST", WORDS), "message"),
        "door refusal": (build.blocked_reply(WORDS), "error"),
        "door envelope": (build.door_envelope("blocked-escalated"), "block_reason"),
        "reliability envelope": (build.envelope_fields(reliability_label="blocked-escalated"), "block_reason"),
        "meta envelope": (_envelope("blocked-escalated", "G6_E_TEST", WORDS), "message"),
        "stop envelope": (improve._stop_reply("blocked-escalated", "G6_E_TEST", WORDS,
                                              canonical_id=None), "message"),
        "mission rewritten refusal": (mission._refusal(WORDS), "error"),
        "knowledge refusal": (knowledge._refused("G6_E_TEST", WORDS), "error"),
    }
    is_authored = getattr(guidance, "is_authored", lambda value: False)
    for name, (value, field) in builders.items():
        assert is_authored(value[field]), name
    for name, reply in {
        "knowledge envelope": knowledge._ok({}, ()),
        "workflow envelope": workflow._reply("a" * 64, "item", {}, WORDS, WORDS),
    }.items():
        assert is_authored(reply["warning_card"]["message"]), name
    from g6.mcp.cloud_consent import withheld_reply
    withheld = withheld_reply(metadata={"has_report": True, "has_text": True})
    assert is_authored(withheld["report"]) and is_authored(withheld["text"])


def test_invoke_preserves_a_component_supplied_card_and_failure_result():
    from returns.result import Success
    from g6.mcp import invoke_reply

    component = {"completion_state": "blocked-escalated", "message": WORDS,
                 "warning_card": {"code": "G6_E_COMPONENT_DOMAIN", "message": WORDS,
                                  "suggested_action": WORDS}}

    class Registry:
        def invoke(self, *args, **kwargs):
            return Success(component)

    shown = invoke_reply("synthetic", "check", registry=Registry())
    assert shown["result"] == component
    assert shown["warning_card"]["message"] == WORDS
    assert shown["block_reason"] == WORDS
    assert shown["required_human_decision"] == WORDS
    from types import MappingProxyType
    component["warning_card"] = MappingProxyType(component["warning_card"])
    shown = invoke_reply("synthetic", "check", registry=Registry())
    assert shown["result"] == component
    assert shown["warning_card"]["message"] == WORDS
    assert shown["block_reason"] == WORDS


def test_sandbox_adapter_marks_only_host_diagnostics(monkeypatch):
    from g6.core import sandbox
    guidance_builders = _builders()

    refused = sandbox.ScriptRun("refused", None, "", WORDS,
                                 isolation={"backend": None, "why": WORDS})
    monkeypatch.setattr(sandbox, "run_script", lambda *a, **k: refused)
    run = getattr(guidance_builders, "script_run", sandbox.run_script)("", {})
    assert "g6 setup" not in run.stderr
    assert "chmod" not in run.isolation["why"]
    assert guidance.is_authored(run.stderr)
    program = sandbox.ScriptRun("ok", 0, WORDS, WORDS)
    monkeypatch.setattr(sandbox, "run_script", lambda *a, **k: program)
    run = getattr(guidance_builders, "script_run", sandbox.run_script)("", {})
    assert run.stdout == WORDS and run.stderr == WORDS
    from g6.core import confine
    monkeypatch.setattr(confine, "import_failure", lambda stderr: WORDS)
    program = sandbox.ScriptRun("error", 1, WORDS, WORDS + "\n" + WORDS,
                                isolation={"backend": "synthetic"})
    monkeypatch.setattr(sandbox, "run_script", lambda *a, **k: program)
    run = getattr(guidance_builders, "script_run", sandbox.run_script)("", {})
    prefix, program_stderr = run.stderr.split("\n", 1)
    assert "chmod" not in prefix and program_stderr == WORDS


def test_confine_receivers_mark_reasons_and_fixed_actions():
    from g6.core import confine
    guidance_builders = _builders()
    from g6.core.errors import ISOLATION_UNAVAILABLE
    from g6.lean.envelope import finish

    plan = confine.Plan(None, confine.bwrap_reason({"program": "/usr/bin/bwrap",
                                                   "unprivileged_userns_clone": "0"}))
    refusal = getattr(guidance_builders, "confine_refusal", confine.refusal)(plan)
    record = getattr(guidance_builders, "confine_record", lambda p: p.record())(plan)
    reply = finish("check_proof", code=ISOLATION_UNAVAILABLE, reason=refusal,
                   extra={"isolation": record}).model_dump()
    text = json.dumps(reply)
    assert "g6 setup" not in text and "sudo" not in text and "sysctl" not in text


def test_real_job_retention_filters_both_polls_without_changing_data():
    from g6.core.door import blocked_reply
    from g6.mcp.review_reply import finish
    from g6.metaharness.jobs import Jobs

    build = getattr(_builders(), "blocked_reply", blocked_reply)
    refusal = build("[G6_E_ISOLATION_UNAVAILABLE] Use g6 setup. Tell the person.")
    refusal["result"] = {"text": WORDS}
    jobs = Jobs()
    try:
        started = jobs.start(lambda: refusal, compact=finish)
        assert getattr(guidance, "is_authored", lambda value: False)(started["note"])
        ident = started["job_id"]
        jobs._jobs[ident].result(timeout=5)
        for detail in ("compact", "full"):
            reply = jobs.status(ident, detail)
            assert reply["status"] == "completed"
            assert "g6 setup" not in reply["result"]["error"]
            assert reply["result"]["result"] == {"text": WORDS}
        data = {"ok": False, "error": WORDS, "warning_card": {"message": WORDS}}
        ident = jobs.start(lambda: data)["job_id"]
        jobs._jobs[ident].result(timeout=5)
        assert jobs.status(ident, "full")["result"] == data
    finally:
        jobs.close()


def test_census_receivers_do_not_import_unmarked_bound_factories():
    """Removing any adapter import must fail this census, even with safe fixtures."""
    import ast
    from pathlib import Path
    import g6

    root = Path(g6.__file__).parent
    factories = {"blocked_reply", "door_envelope", "envelope_fields", "warning_card", "card"}
    paths = [path for name in ("mcp", "metaharness", "harness") for path in (root / name).glob("*.py")]
    paths += [root / "core" / name for name in ("compose.py", "selector.py")]
    missing = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module in {
                    "g6.core.door", "g6.core.reliability_envelope"}:
                missing += [(path.relative_to(root).as_posix(), alias.name)
                            for alias in node.names if alias.name in factories]
    assert not missing, missing
