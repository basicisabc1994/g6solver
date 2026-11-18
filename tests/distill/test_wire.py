"""Distill wiring acceptance through local surfaces and actual registered schemas."""
import inspect
import json
from pathlib import Path

import pytest

from .wire_declarations import ARGUMENTS, CLASSES, issued


@pytest.fixture
def surface(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    from g6.distill import surface
    return surface


def test_all_twelve_tools_attach_issued_declarations():
    from g6.mcp import distill

    class Server:
        tools = {}

        def tool(self):
            def add(fn):
                self.tools[fn.__name__] = fn
                return fn
            return add

    server = Server()
    with issued():
        distill.register(server, object, object())
    assert set(server.tools) == set(CLASSES)
    for name, fn in server.tools.items():
        assert fn._g6_declaration.classes == CLASSES[name]
        assert set(inspect.signature(fn).parameters) - {"ctx"} == set(ARGUMENTS[name])
    hunk = Path(".build/issuance.patch").read_text(encoding="utf-8")
    for name, classes in CLASSES.items():
        assert f"+    {name!r}: contract({', '.join(repr(c) for c in classes)})," in hunk
        assert f"+    {name!r}: {ARGUMENTS[name]!r}," in hunk


def test_library_empty_read_does_not_create_state(surface):
    import os
    assert surface.library("list") == {"entries": []}
    assert not Path(os.environ["G6_HOME"]).exists()


@pytest.mark.parametrize("op", ["describe", "history"])
def test_library_validates_ids(surface, op):
    with pytest.raises(ValueError):
        surface.library(op, "../../secret")


def test_failure_intake_is_self_observed_and_refuses_held_out(surface):
    from g6.distill.ledger import Stores
    from g6.distill.records import FailureRecord
    result = surface.failure_record("user_request", "test_weakening", '{"split":"field","example":"x"}', "please adapt")
    [row] = Stores().for_model(FailureRecord).list()
    assert result["record_id"] == row.record_id
    assert row.observed_by == "self" and row.source == "person"
    with pytest.raises(ValueError):
        surface.failure_record("user_request", "test_weakening", '{"split":"held_out"}', "no")


def test_future_backends_are_explicitly_unavailable(surface):
    for name in ("distill", "self_model", "simulate", "extend", "research"):
        result = surface.unavailable(name)
        assert result["completion_state"] == "blocked-escalated"
        assert result["warning_card"]["code"] == "G6_E_DISTILL_UNAVAILABLE"


def test_cli_library_and_future_routes(surface, capsys):
    from g6.cli import main
    assert main(["library", "ls"]) == 0
    assert json.loads(capsys.readouterr().out) == {"entries": []}
    for args in (["self-model"], ["research", "start"], ["research", "stop"], ["research", "status"]):
        assert main(args) == 1
        assert json.loads(capsys.readouterr().out)["warning_card"]["code"] == "G6_E_DISTILL_UNAVAILABLE"


def test_every_new_tool_is_discoverable_and_routed():
    from g6.skills import corpus, routing
    data = corpus.load()
    indexed = {e["id"]: e for e in data["entries"]}
    manifest = routing.load()
    for name in CLASSES:
        assert "tool:" + name in indexed
        assert "mcp:g6/" + name in manifest.entries
    assert "learn" in manifest.routes


def test_library_dialog_cancel_and_stale_never_mutate(monkeypatch):
    import asyncio
    from g6.mcp import distill, consent
    from g6.distill import acceptance_gate
    fns = {}
    class Server:
        def tool(self):
            return lambda fn: fns.setdefault(fn.__name__, fn)
    distill.register(Server(), object, object())
    monkeypatch.setattr(distill.surface, "library", lambda *a: {"entry": {"entry_id": "a" * 64, "authority": "A2"}})
    async def cancel(*args):
        return "cancel", None, ""
    monkeypatch.setattr(consent, "elicit", cancel)
    monkeypatch.setattr(acceptance_gate, "withdraw", lambda *a, **kw: pytest.fail("cancelled decision mutated library"))
    result = asyncio.run(fns["g6_library_decide"]("a" * 64, "withdraw", "disputed", object()))
    assert result["warning_card"]["code"] == "G6_E_DISTILL_DIALOG"
    result = asyncio.run(fns["g6_library_decide"]("b" * 64, "withdraw", "disputed", object()))
    assert result["warning_card"]["code"] == "G6_E_DISTILL_STALE"


def test_hook_records_only_a_reproduction_as_untrusted_field_data(monkeypatch):
    from g6.distill import surface
    from g6.harness import completion_hook
    seen = []
    monkeypatch.setattr(surface, "record_reproduction", lambda event: seen.append(event))
    monkeypatch.setenv("G6_HINTS", "off")
    completion_hook.on_tool({"tool_name": "mcp__g6__g6_review_patch", "session_id": "test",
        "tool_response": {"assessment": {"outcome": "defect_detected"}}})
    assert len(seen) == 1


def test_fresh_client_can_list_library_without_a_grant(monkeypatch):
    from g6.distill.library import Library
    from g6.mcp import build_server
    from tests.workflow.kit import call
    monkeypatch.setattr(Library, "list", lambda self: [])
    with issued():
        server = build_server()
        result = call(server, "g6_library", {"op": "list"})
    assert result == {"entries": []}


def test_consent_census_matches_real_schemas_and_h1():
    from g6.core.consent_declarations import TOOLS, TOOL_ARGUMENTS
    from g6.mcp import build_server, registrations
    with issued():
        tools = dict(registrations._listed(build_server()))
        for name in CLASSES:
            fn = tools[name].fn
            assert fn._g6_declaration == TOOLS[name]
            assert set(tools[name].parameters["properties"]) == set(TOOL_ARGUMENTS[name])
            assert set(TOOL_ARGUMENTS[name].values()) <= {"content", "pointer"}
        assert set(tools["g6_library"].parameters["properties"]["op"]["enum"]) == {"list", "describe", "history"}
        assert set(tools["g6_library_decide"].parameters["properties"]["action"]["enum"]) == {"demote", "withdraw", "restore"}


def test_hook_intake_never_mints_independent_evidence(monkeypatch):
    from g6.distill import surface
    written = []
    class Store:
        def list(self):
            return []
        def put(self, row):
            written.append(row)
    monkeypatch.setattr(surface.Stores, "for_model", lambda *a: Store())
    surface.record_reproduction({"tool_response": {"assessment": {"outcome": "defect_detected", "reasons": []}}})
    assert written == []
    surface.record_reproduction({"tool_response": {"assessment": {"outcome": "defect_detected", "reasons": [
        {"reproduction": "test_app.py::test_example"}]}}})
    assert len(written) == 1
    assert written[0].observed_by == "self" and written[0].split == "field"


def test_hook_accepts_real_completion_reproduction_shape(monkeypatch):
    from g6.distill import surface
    written = []
    class Store:
        def list(self):
            return []
        def put(self, row):
            written.append(row)
    monkeypatch.setattr(surface.Stores, "for_model", lambda *a: Store())
    surface.record_reproduction({"tool_response": {"assessment": {"outcome": "defect_detected", "reasons": [],
        "completion": {"defects_reproduced": {"stated_and_repository_checks": [
            {"reproduction": {"node": "tests/test_app.py::test_app"}}]}}}}})
    assert len(written) == 1


def test_field_fires_are_unlabelled_deduplicated_and_not_gate_custody(surface):
    from g6.core.ledger import ChainLedger
    from g6.core.storage import state_path
    from g6.harness.library_review_gate import record_fire
    found = {"verdict": "concern", "entry_id": "a" * 64, "candidate_id": "b" * 64, "authority": "A1"}
    record_fire(found, {"test_a.py": "old"}, {"test_a.py": "new"})
    record_fire(found, {"test_a.py": "old"}, {"test_a.py": "new"})
    [row] = ChainLedger(state_path("distill", "field_fires_ledger.jsonl")).entries()
    assert row["body"]["label"] == "unlabelled"
    assert not state_path("distill", "held_out").exists()


def test_cli_dispatch_without_persistence(monkeypatch, capsys):
    from g6.distill import surface
    from g6.cli import main
    seen = []
    monkeypatch.setattr(surface, "library", lambda *args: seen.append(args) or {"entries": []})
    assert main(["library", "ls"]) == 0
    capsys.readouterr()
    assert main(["library", "show", "a" * 64]) == 0
    capsys.readouterr()
    assert seen == [("list", ""), ("describe", "a" * 64)]
    for args in (["self-model"], ["research", "start"], ["research", "stop"], ["research", "status"]):
        assert main(args) == 1
        assert json.loads(capsys.readouterr().out)["warning_card"]["code"] == "G6_E_DISTILL_UNAVAILABLE"


def test_sensitive_synthesis_never_runs_without_host_allowance(monkeypatch):
    from g6.mcp import distill
    fns = {}
    class Server:
        def tool(self):
            return lambda fn: fns.setdefault(fn.__name__, fn)
    distill.register(Server(), object, object())
    monkeypatch.delenv("G6_MCP_SENSITIVE", raising=False)
    result = fns["g6_distill_start"]("test_weakening")
    assert result["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    # Synthetic host configuration, not a permission change on the person's server.
    monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.distill_start")
    result = fns["g6_distill_start"]("test_weakening")
    assert result["warning_card"]["code"] == "G6_E_DISTILL_UNAVAILABLE"


def test_library_decision_delegates_only_dialog_name_and_gate_refusal_blocks(monkeypatch):
    import asyncio
    from g6.mcp import distill, consent
    from g6.distill import acceptance_gate as gate
    fns = {}
    class Server:
        def tool(self):
            return lambda fn: fns.setdefault(fn.__name__, fn)
    distill.register(Server(), object, object())
    monkeypatch.setattr(distill.surface, "library", lambda *a: {"entry": {"entry_id": "a" * 64, "authority": "A2"}})
    async def accept(*args):
        return "accept", {"confirm": "change", "name": "Dialog person"}, ""
    monkeypatch.setattr(consent, "elicit", accept)
    seen = []
    def refused(*args, **kwargs):
        seen.append(kwargs)
        return gate.AcceptanceDecision("refuse", ("stale entry",))
    monkeypatch.setattr(gate, "demote", refused)
    result = asyncio.run(fns["g6_library_decide"]("a" * 64, "demote", "disputed", object()))
    assert seen == [{"reason": "disputed", "person": "Dialog person"}]
    assert result["warning_card"]["code"] == "G6_E_DISTILL_DECISION"
