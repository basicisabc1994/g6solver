# SPDX-License-Identifier: Apache-2.0
"""``g6.invoke``, ``g6_invoke`` and ``g6 invoke`` give one outcome (plan Step 1): the same door, the same label
correction, and the same policy on sensitive ops, which only the person running G6 can grant. Offline."""
from __future__ import annotations

import json

import pytest
from returns.result import Failure, Success

import g6
from g6.cli import main as cli
from g6.core import registry as registry_module
from g6.mcp import SENSITIVE_ENV, invoke_reply

DENIED = "G6_E_SENSITIVE_COMPONENT_DENIED"


class _Registry:
    """One component, ``demo``, whose op answers ``value``; a sensitive op is refused when the caller blocks it, the
    way ``Registry.invoke`` refuses an op its module lists in ``__g6_sensitive_ops__``."""

    refs: dict = {}

    def __init__(self, value, *, sensitive: bool = False) -> None:
        self.value, self.sensitive, self.calls, self.models = value, sensitive, [], []

    def invoke(self, component, op, params, *, block_sensitive=False, **kwargs):  # noqa: ANN001, ANN201
        self.calls.append(params)
        self.models.append(kwargs.get("llm"))
        if self.sensitive and block_sensitive:
            return Failure(f"[{DENIED}] {component}.{op} is not available on this surface")
        return Success(self.value)


@pytest.fixture
def surfaces(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """Each surface's answer for one registry: the Python reply, the MCP reply, and what the command printed and
    returned."""
    monkeypatch.delenv(SENSITIVE_ENV, raising=False)

    def run(registry: _Registry, params: dict | None = None, **allow) -> dict:
        monkeypatch.setattr(registry_module, "get_registry", lambda: registry)
        python = g6.invoke("demo", "op", params or {}, **allow)
        mcp = invoke_reply("demo", "op", json.dumps(params or {}))
        capsys.readouterr()
        code = cli(["invoke", "demo", "op", json.dumps(params or {})])
        out, err = capsys.readouterr()
        return {"python": python, "mcp": mcp, "code": code, "out": out, "err": err}
    return run


def _outcome(reply: dict) -> dict:
    """What a reply decides: everything but its request id and, for a refusal, the words that say how this surface
    allows it."""
    out = {k: v for k, v in reply.items() if k != "request_id"}
    if "result" not in out:
        out = {"ok": out["ok"], "completion_state": out["completion_state"], "code": out["warning_card"]["code"]}
    return out


def _same(found: dict) -> dict:
    mcp = found["mcp"]
    assert _outcome(found["python"]) == _outcome(mcp)
    assert found["code"] == (0 if mcp["ok"] else 1)
    if "result" in mcp:
        assert json.loads(found["out"]) == json.loads(json.dumps(mcp["result"], default=str))
    else:
        assert found["out"] == "" and mcp["warning_card"]["code"] in found["err"]
    return mcp


@pytest.mark.parametrize(("value", "state", "ok"), [
    ({"answer": 42}, "qualified-draft", True),
    ({"completion_state": "qualified-draft", "answer": 42}, "qualified-draft", True),
    ({"completion_state": "verified", "evidence": [{"kind": "test", "ref": "t1"}]}, "qualified-draft", True),
    ({"completion_state": "blocked-escalated", "answer": 42}, "blocked-escalated", False),
])
def test_one_outcome_on_every_surface(surfaces, value: dict, state: str, ok: bool) -> None:
    reply = _same(surfaces(_Registry(value)))
    assert reply["completion_state"] == state and reply["ok"] is ok


def test_a_components_own_verified_is_held_on_every_surface(surfaces) -> None:
    reply = _same(surfaces(_Registry({"completion_state": "verified", "evidence": [{"kind": "test"}]})))
    assert reply["warning_card"]["code"] == "G6_E_PROMOTION_LAW" and reply["claimed_completion_state"] == "verified"


def test_a_sensitive_op_is_refused_on_every_surface_unless_the_person_allows_it(surfaces) -> None:
    registry = _Registry({"answer": 42}, sensitive=True)
    reply = _same(surfaces(registry))
    assert reply["ok"] is False and reply["warning_card"]["code"] == DENIED and registry.calls == [{}, {}, {}]


def test_the_persons_setting_allows_it_on_every_surface(surfaces, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SENSITIVE_ENV, "demo.op")
    reply = _same(surfaces(_Registry({"answer": 42}, sensitive=True)))
    assert reply["ok"] is True and reply["result"]["answer"] == 42


def test_every_surface_hands_the_component_the_persons_model(surfaces, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import llm

    model = llm.FakeLLM()
    monkeypatch.setattr(llm, "provider_from_env", lambda *args, **kwargs: model)
    registry = _Registry({"answer": 42})
    _same(surfaces(registry))
    assert registry.models == [model, model, model]   # g6.invoke, g6_invoke, g6 invoke
    # Dropping the llm hand-over in door.invoke_checked, or a surface calling the registry itself, makes this fail.


def test_the_mcp_tool_takes_nothing_an_agent_could_use_to_allow_a_sensitive_op() -> None:
    """Over MCP the agent supplies only component, op and params_json; whether a sensitive op may run is the server's
    own setting."""
    fastmcp = pytest.importorskip("fastmcp")
    import asyncio

    from g6.mcp import build_server
    from g6.metaharness.surface import Surface

    async def schema() -> dict:
        async with fastmcp.Client(build_server(Surface({}))) as agent:
            return {tool.name: tool.inputSchema for tool in await agent.list_tools()}["g6_invoke"]

    assert set(asyncio.run(schema())["properties"]) == {"component", "op", "params_json"}
    # Adding an allow (or allow_sensitive) parameter to the g6_invoke tool makes this fail.


def test_only_the_callers_own_allow_grants_more_in_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """``allow`` is what the person's own code passes: it grants that call, and nothing after it."""
    monkeypatch.delenv(SENSITIVE_ENV, raising=False)
    registry = _Registry({"answer": 42}, sensitive=True)
    monkeypatch.setattr(registry_module, "get_registry", lambda: registry)
    assert g6.invoke("demo", "op", {}, allow=("demo.op",))["ok"] is True
    assert g6.invoke("demo", "op", {}, allow=("demo",))["ok"] is True
    assert g6.invoke("demo", "op", {}, allow=("other", "demo.other_op"))["warning_card"]["code"] == DENIED
    assert g6.invoke("demo", "op", {}, allow="demo")["ok"] is True   # one name, not its letters
    assert g6.invoke("demo", "op", {})["warning_card"]["code"] == DENIED
    assert invoke_reply("demo", "op", "{}")["warning_card"]["code"] == DENIED


def test_the_python_refusal_says_how_its_caller_allows_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SENSITIVE_ENV, raising=False)
    monkeypatch.setattr(registry_module, "get_registry", lambda: _Registry({}, sensitive=True))
    error = g6.invoke("demo", "op", {})["error"]
    assert "allow=" in error and "demo.op" in error and SENSITIVE_ENV in error


@pytest.mark.parametrize("params", [[1, 2], "not a mapping", None])
def test_parameters_that_are_not_a_mapping_are_blocked_as_on_mcp(monkeypatch: pytest.MonkeyPatch, params) -> None:
    monkeypatch.setattr(registry_module, "get_registry", lambda: _Registry({"answer": 42}))
    reply = g6.invoke("demo", "op", params) if params is not None else g6.invoke("demo", "op")
    if params is None:   # no parameters is an empty mapping, as on MCP
        assert reply["ok"] is True
    else:
        assert reply["ok"] is False and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_the_docs_name_g6_invoke_as_a_door_and_say_what_ok_means() -> None:
    """README, AGENT_GUIDE and g6_help name ``g6.invoke`` beside the other doors and say when ``ok`` is false; g6_help
    gives g6_invoke's parameter by its MCP name."""
    from pathlib import Path

    from g6.core import discovery

    root = Path(g6.__file__).resolve().parents[2]
    for name, text in (("README.md", (root / "README.md").read_text(encoding="utf-8")),
                       ("AGENT_GUIDE.md", (root / "AGENT_GUIDE.md").read_text(encoding="utf-8")),
                       ("g6_help", discovery.HELP)):
        plain = " ".join(text.split()).replace("`", "")
        assert "g6.invoke" in plain, name
        assert "ok is false whenever the state is blocked-escalated" in plain, name
    assert "params_json" in discovery.HELP
    # Dropping g6.invoke or the ok rule from any of the three, or HELP's params_json, makes this fail.
