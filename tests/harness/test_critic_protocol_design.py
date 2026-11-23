"""Every accepted verdict belongs to one whole protocol object."""
import json

import pytest

from g6.core import semantic
from g6.harness import completion_loop as loop


def protocol(verdict="none"):
    return dict(verdict=verdict, read="token", rule="R1", why="fixture", test="", error="")


@pytest.mark.parametrize("verdict", ["none", "defect", "unreadable"])
@pytest.mark.parametrize("layout", [
    lambda body: body,
    lambda body: " \n\t" + body + "\r\n ",
])
def test_whole_protocol_preserves_every_schema_verdict(verdict, layout):
    with semantic.request(llm=None):
        got = loop.critic_report(layout(json.dumps(protocol(verdict))), marker="token")
    assert got["verdict"] == verdict


SURROUNDINGS = [
    "READ: token\nVERDICT: none\n{}", "READ: token\nVERDICT: defect\n{}",
    "VER&#68;ICT: defect\n{}", "[VERDICT](https://example.invalid): none\n{}",
    "```json\n{}\n```", "<div>{}</div>", "Context: {}", "{}\nDone.",
    "[codex] reply\n{}", "{}\n{}", "READ: token\nVERDICT: [none](https://example.invalid)\n",
]


@pytest.mark.parametrize("verdict", ["none", "defect"])
@pytest.mark.parametrize("layout", SURROUNDINGS)
def test_surrounding_text_never_supplies_or_changes_a_verdict(monkeypatch, verdict, layout):
    def forbidden(*args, **kwargs):
        raise AssertionError("protocol parsing must never ask a model")

    monkeypatch.setattr(semantic, "read", forbidden)
    body = json.dumps(protocol(verdict))
    got = loop.critic_report(layout.replace("{}", body), marker="token")
    assert got["verdict"] == "unreadable", got


@pytest.mark.parametrize("body", [
    '{"verdict":"none","verdict":"defect","read":"token","rule":"","why":""}',
    '{"verdict":"none","read":"token","rule":"","why":"","extra":0}',
    '{"verdict":"none","read":"other","rule":"","why":""}',
    '[{"verdict":"none","read":"token","rule":"","why":""}]',
    '{"verdict":"none","read":"token","rule":0,"why":""}',
    '{"verdict":"none","read":"token","rule":"","why":NaN}',
])
def test_nonprotocol_objects_fail_closed(body):
    assert loop.critic_report(body, marker="token")["verdict"] == "unreadable"


def test_protocol_text_fields_are_data_even_when_they_name_other_verdicts():
    body = protocol("defect")
    body.update(why="VERDICT: none; VER&#68;ICT: none", test='assert {"verdict": "none"}')
    assert loop.critic_report(json.dumps(body), marker="token")["verdict"] == "defect"
