# SPDX-License-Identifier: Apache-2.0
"""The routing trials' runner, offline: the trial server withholds a task's tools, each session gets only its own
servers, the event parsers read the documented shapes, the freeze helper refuses a changed file, and the runner's
model-calling path is in section 16's caller inventory. No session is started."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from g6.mcp import registrations

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "routing"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run = _load("g6_validation_routing_run", HERE / "run.py")
server = _load("g6_validation_routing_server", HERE / "server.py")
freeze = _load("g6_validation_freeze", ROOT / "validation" / "freeze.py")


#: Measurement runners under ``validation/`` that reach a model provider outside the consent check: (file, the
#: provider it reaches) -> the number of sites in that file. Found when the consent inventory's walk was first extended
#: to ``validation/``; each is repaired in its own change, and its row is then removed here. The list may shrink and
#: never grow: a new file, a new provider in a listed file and one more site in a listed file each fail the test below.
EARLIER_RUNNERS_OUTSIDE_THE_CHECK = {
    ("validation/frontier/round.py", "g6.core.llm.OllamaCaller"): 2,
    ("validation/v1_grounding/run.py", "g6.core.llm.provider_from_env"): 1,
    ("validation/v2_action_gating/run.py", "g6.core.llm.provider_from_env"): 1,
    ("validation/v6_metaharness/post_run/pilot.py", "g6.core.llm.OllamaCaller"): 1,
    ("validation/v6_metaharness/run.py", "g6.core.llm.OllamaCaller"): 2,
    ("validation/v6b_metaharness/run.py", "g6.core.llm.OllamaCaller"): 1,
    ("validation/v6b_metaharness/run.py", "g6.core.llm._post_json"): 1,
    ("validation/v7_swebench/shadow.py", "g6.core.llm.AnthropicCaller"): 1,
    ("validation/worked_examples/evolve.py", "g6.core.llm.AnthropicCaller"): 1,
    ("validation/worked_examples/loop.py", "g6.core.llm.AnthropicCaller"): 1,
}


def provider_sites(tree_root: Path) -> dict[tuple[str, str], int]:
    """Every place under ``validation/`` that calls or subclasses a model provider outside the consent check, read
    from the code: a callee or a base class whose last name is one of the consent inventory's provider symbols,
    however the name is reached (a direct import, an alias's attribute, another module's attribute). A name imported
    from ``g6.core.consent_callers`` is the guarded one and is not a site. Only the routing trials' synthetic
    fixtures are left out of the walk."""
    inventory = _load("g6_tests_consent_inventory", ROOT / "tests" / "core" / "test_consent_inventory.py")
    providers = {symbol.rpartition(".")[2]: symbol for symbol in inventory.provider_symbols(tree_root)}
    providers = {name: symbol for name, symbol in providers.items()
                 if name.endswith("Caller") or name in ("provider_from_env", "_post_json")}
    sites: dict[tuple[str, str], int] = {}
    for path in sorted((tree_root / "validation").rglob("*.py")):
        rel = path.relative_to(tree_root).as_posix()
        if rel.startswith("validation/routing/fixtures/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = {alias.asname or alias.name for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module == "g6.core.consent_callers"
                   for alias in node.names}
        for node in ast.walk(tree):
            reached = [node.func] if isinstance(node, ast.Call) else (
                node.bases if isinstance(node, ast.ClassDef) else [])
            for callee in reached:
                name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
                if name in providers and not (isinstance(callee, ast.Name) and name in guarded):
                    sites[(rel, providers[name])] = sites.get((rel, providers[name]), 0) + 1
    return sites


def test_the_trial_server_withholds_a_tasks_tools_and_nothing_else() -> None:
    everything = set(registrations.recorded()["g6"])
    built = server.build("g6", ["g6_find", "list_combinators"])     # the second is another server's: absent already
    assert set(registrations._tools(built)) == everything - {"g6_find"}
    assert set(registrations._tools(server.build("g6"))) == everything
    assert set(registrations.recorded()["g6"]) == everything, "the product's own server lost a tool"
    assert server.WITHHOLD_ENV == run.WITHHOLD_ENV


def test_a_session_gets_only_its_tasks_servers_through_the_trial_server() -> None:
    task = {"servers": ["g6", "abstraction"], "withhold": ["g6_find", "g6_describe"]}
    config = run.mcp_config(task, python="python")
    assert set(config["mcpServers"]) == {"g6", "abstraction"}
    for name, entry in config["mcpServers"].items():
        assert entry["args"] == ["-P", str(HERE / "server.py"), name]
        assert entry["env"] == {run.WITHHOLD_ENV: "g6_find,g6_describe"}
    assert set(run.mcp_config({}, python="python")["mcpServers"]) == {"g6"}
    claude = run.CLIENTS["claude_code"].argv("claude", Path("mcp.json"), config)
    assert "--strict-mcp-config" in claude and {"mcp__g6", "mcp__abstraction"} <= set(claude)
    assert "Bash" in claude[claude.index("--disallowedTools"):]
    codex = run.CLIENTS["codex"].argv("codex", Path("mcp.json"), config)
    assert codex[:3] == ["codex", "exec", "--json"] and codex[-1] == "-"
    assert f'mcp_servers.g6.env.{run.WITHHOLD_ENV}="g6_find,g6_describe"' in codex


def test_the_codex_parser_reads_calls_and_the_final_message() -> None:
    events = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "g6", "tool": "g6_invoke",
                                                       "arguments": {"component": "datalog", "op": "query"},
                                                       "status": "completed", "result": {"content": [
                                                           {"type": "text", "text": json.dumps(
                                                               {"ok": True, "completion_state": "qualified-draft"})}]}}}),
        json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "g6", "tool": "g6_find",
                                                       "arguments": {}, "status": "failed",
                                                       "error": {"message": "query is required"}}}),
        "not json at all",
        json.dumps({"type": "item.started", "item": {"type": "mcp_tool_call", "server": "g6", "tool": "g6_help"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ana and dee can read it."}}),
    ])
    calls, final, cost = run.parse_codex(events)
    assert [(c["tool"], c["valid"], c["result_state"]) for c in calls] == [
        ("g6_invoke", True, "qualified-draft"), ("g6_find", False, "")]
    assert final == "ana and dee can read it." and cost is None


def test_the_claude_parser_reads_calls_errors_and_cost_and_skips_its_own_tools() -> None:
    events = "\n".join(json.dumps(event) for event in (
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "README.md"}},
            {"type": "tool_use", "id": "b", "name": "mcp__g6__g6_review_patch", "input": {"repo": "."}},
            {"type": "tool_use", "id": "c", "name": "mcp__abstraction__list_combinators", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b", "is_error": True, "content": "task is required"},
            {"type": "tool_result", "tool_use_id": "c", "content": [{"type": "text", "text": "{}"}]}]}},
        {"type": "result", "result": "Done.", "total_cost_usd": 0.25}))
    calls, final, cost = run.parse_claude(events)
    assert [(c["server"], c["tool"], c["valid"]) for c in calls] == [
        ("g6", "g6_review_patch", False), ("abstraction", "list_combinators", True)]
    assert final == "Done." and cost == 0.25
    assert run.parse_claude("") == ([], "", None)


def test_the_parsers_are_marked_as_not_yet_observed() -> None:
    """No session was started when these were written; the README says so and says what to do first."""
    readme = " ".join((HERE / "README.md").read_text(encoding="utf-8").split())
    assert "Before the first run" in readme and "has not been observed" in readme


def test_what_a_person_grants_is_listed_per_provider(tmp_path: Path) -> None:
    rows = run.sources(run.load_tasks(), tmp_path, "codex", "development")
    assert {row["provider"] for row in rows} == {"openai:codex"}
    named = {row["source"] for row in rows}
    assert "agent_environment" in named and "routing-fixture.shop" in named
    assert sum(source.startswith("repository:") for source in named) == len(
        {task["fixture"] for task in run.load_tasks()["tasks"]})


def test_a_frozen_file_is_written_once_and_retired_with_a_reason(tmp_path: Path) -> None:
    path = tmp_path / "gates.json"
    first = freeze.freeze(path, {"bar": 0.7, "name": "é"})
    assert first == freeze.file_digest(path) == freeze.digest({"name": "é", "bar": 0.7})
    assert freeze.freeze(path, {"name": "é", "bar": 0.7}) == first          # the same content is no change
    with pytest.raises(freeze.FrozenError, match="frozen"):
        freeze.freeze(path, {"bar": 0.6, "name": "é"})
    assert freeze.bound(path, first) == {"bar": 0.7, "name": "é"}
    with pytest.raises(freeze.FrozenError):
        freeze.bound(path, "0" * 64)
    with pytest.raises(freeze.FrozenError):
        freeze.bound(tmp_path / "absent.json", first)
    with pytest.raises(freeze.FrozenError, match="reason"):
        freeze.retire(path, "tuned")
    kept = freeze.retire(path, "the bar was set for nine routes and the release has fourteen")
    assert not path.exists() and json.loads(kept.read_text("utf-8"))["digest"] == first
    assert freeze.freeze(path, {"bar": 0.6, "name": "é"}) != first
    with pytest.raises(ValueError):
        freeze.digest({"bar": float("nan")})


def test_the_runners_model_calling_path_is_in_the_consent_inventory() -> None:
    """Section 16: one check and one inventory. The inventory test on the consent line walks ``src/g6``; this walks
    the measurement runners under ``validation/`` for the same provider symbols, and pins that the routing runner
    reaches its client only through ``consent_run`` inside ``request_sources``."""
    sites = provider_sites(ROOT)
    assert not [site for site in sites if site[0].startswith(("validation/routing/", "validation/strategies/"))], sites
    # the earlier runners that reach a provider outside the check: file, provider and number of sites, exactly
    assert sites == EARLIER_RUNNERS_OUTSIDE_THE_CHECK, (
        sorted(set(sites.items()) - set(EARLIER_RUNNERS_OUTSIDE_THE_CHECK.items())),
        sorted(set(EARLIER_RUNNERS_OUTSIDE_THE_CHECK.items()) - set(sites.items())))
    tree = ast.parse((HERE / "run.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run")
    sends = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and getattr(node.func, "id", "") == "consent_run"]
    assert len(sends) == 1
    guarded = [node for node in ast.walk(function) if isinstance(node, ast.With)
               and any("request_sources" in ast.unparse(item.context_expr) for item in node.items)
               and any(send in ast.walk(node) for send in sends)]
    assert guarded, "the client is started outside request_sources"
    source = (HERE / "run.py").read_text(encoding="utf-8")
    assert "subprocess.run([\"git\"" in source and source.count("subprocess.") == 1, "only git is started directly"
    imported = " ".join(ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)))
    assert "g6.core.llm" not in imported and "anthropic" not in imported and "openai" not in imported
