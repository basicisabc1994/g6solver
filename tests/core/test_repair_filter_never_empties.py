# SPDX-License-Identifier: Apache-2.0
"""The reply filter works at phrase level: a field it filters is never left empty or as codes alone.

Lead ruling 2026-11-21 (rc-wrp): the wire-repair filter (0b400900) still removes every setting and command that lifts
a block the person set, from G6's own text too; a sentence that states a fact keeps the fact with neutral words.
"""
from __future__ import annotations

import re

import pytest

from g6.core import agent_guidance as guidance

#: Only instructions, nothing else: the case that once emptied a field.
INSTRUCTION = "[G6_E_TEST] Run chmod +x build.sh. Use g6 setup. Set G6_TEST_SETTING=on."
CODE = re.compile(r"\[G6_E_[A-Z0-9_]+\]")


def _said(text: str) -> bool:
    return bool(CODE.sub("", text).strip())


def _leaves(value, path=()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, (*path, key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _leaves(child, (*path, index))


def _at(value, path):
    for key in path:
        value = value[key]
    return value


def _census():
    from g6.core import guidance_builders as build, selector, confine
    from g6.mcp import composition, improve, mission, repair, knowledge, workflow
    from g6.metaharness.api import _envelope

    plan = confine.Plan(None, "unavailable: " + INSTRUCTION)
    return {
        "composition refusal": composition.compose_reply({}),
        "mission refusal": mission.refused(),
        "improvement refusal": improve.refused(),
        "selector action": selector._remedy(INSTRUCTION),
        "repair step": repair.block("g6_invoke", {"ok": False, "error": "[G6_E_INPUT_VALIDATION] bad"}),
        "declined step": repair.declined_block("declined"),
        "consent step": repair.consent_block(),
        "warning card": build.warning_card("G6_E_TEST", INSTRUCTION, INSTRUCTION),
        "fallback card": build.card(None, "G6_E_TEST", INSTRUCTION),
        "door refusal": build.blocked_reply(INSTRUCTION),
        "door envelope": build.door_envelope("blocked-escalated"),
        "reliability envelope": build.envelope_fields(reliability_label="blocked-escalated"),
        "meta envelope": _envelope("blocked-escalated", "G6_E_TEST", INSTRUCTION),
        "stop envelope": improve._stop_reply("blocked-escalated", "G6_E_TEST", INSTRUCTION, canonical_id=None),
        "mission rewritten refusal": mission._refusal(INSTRUCTION),
        "knowledge refusal": knowledge._refused("G6_E_TEST", INSTRUCTION),
        "knowledge envelope": knowledge._ok({}, ()),
        "workflow envelope": workflow._reply("a" * 64, "item", {}, INSTRUCTION, INSTRUCTION),
        "isolation record": {"isolation": confine.not_started(plan.record(), RuntimeError(INSTRUCTION))},
        "isolation refusal": build.blocked_reply(confine.refusal(plan)),
    }


@pytest.mark.parametrize("name", sorted(_census()))
def test_no_field_the_filter_reads_is_left_empty_or_codes_only(name):
    value = _census()[name]
    shown = guidance.reply_for_agent(value)
    for path, text in _leaves(value):
        if not _said(text) or not guidance._instruction(text):
            continue
        after = _at(shown, path)
        assert isinstance(after, str) and _said(after), (name, path, after)
        assert "chmod" not in after and "g6 setup" not in after and "G6_TEST_SETTING" not in after, (name, path)


def test_a_field_of_instructions_alone_says_that_g6_left_something_out():
    assert guidance.text_for_agent("Use g6 setup.") == guidance.WITHHELD_STEP
    assert guidance.text_for_agent(INSTRUCTION) == "[G6_E_TEST] " + guidance.WITHHELD_STEP


def test_a_fact_keeps_its_sentence_and_names_the_setting_or_command_neutrally():
    from g6.core import confine

    warning = guidance.reply_for_agent({"warning": confine.UNCONFINED_WARNING})["warning"]
    assert "g6 setup" not in warning and ".env" not in warning
    assert "G6 refuses to run untrusted code" in warning and "your own rights" in warning
    assert "You are responsible for what you run." in warning
    plan = confine.Plan(None, "unavailable: switched off")
    reason = guidance.text_for_agent(confine.refusal(plan))
    assert reason.startswith("[G6_E_ISOLATION_UNAVAILABLE]") and "(unavailable: switched off)" in reason
    assert "g6 setup" not in reason and confine.ALLOW not in reason


def test_an_instruction_sentence_beside_a_fact_is_still_dropped_whole():
    assert guidance.text_for_agent("Run chmod +x build.sh. Keep the diagnosis.") == "Keep the diagnosis."
