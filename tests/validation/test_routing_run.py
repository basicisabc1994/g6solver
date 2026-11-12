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


#: Measurement runners under ``validation/`` that reach a model provider outside the consent check: (file, what it
#: reaches) -> the number of sites in that file, as ``provider_sites`` reads them: a provider class or factory of
#: ``g6.core.llm``, ``runner-class:<Class>`` (a subclass of one that the runner defines and builds), ``sdk-send:<where>``
#: (a provider SDK's send) and ``credential-handed-on:<NAME>`` (a provider credential put in the environment of a
#: process the runner starts, or in a file for one: how ``v7_swebench/run_arm.py`` and ``worked_examples/run.py``
#: start Claude Code). Each is repaired in its own change, and its row is then removed here. The list may shrink and
#: never grow: a new file, a new way of reaching a provider in a listed file and one more site in a listed file each
#: fail the test below.
EARLIER_RUNNERS_OUTSIDE_THE_CHECK = {
    ("validation/frontier/round.py", "g6.core.llm.OllamaCaller"): 2,
    ("validation/v1_grounding/run.py", "g6.core.llm.provider_from_env"): 1,
    ("validation/v2_action_gating/run.py", "g6.core.llm.provider_from_env"): 1,
    ("validation/v6_metaharness/post_run/pilot.py", "g6.core.llm.OllamaCaller"): 1,
    ("validation/v6_metaharness/run.py", "g6.core.llm.OllamaCaller"): 2,
    ("validation/v6b_metaharness/run.py", "g6.core.llm.OllamaCaller"): 1,
    ("validation/v6b_metaharness/run.py", "g6.core.llm._post_json"): 1,
    ("validation/v6b_metaharness/run.py", "runner-class:CountingOllama"): 1,
    ("validation/v7_swebench/run_arm.py", "credential-handed-on:ANTHROPIC_API_KEY"): 2,
    ("validation/v7_swebench/shadow.py", "g6.core.llm.AnthropicCaller"): 1,
    ("validation/v7_swebench/shadow.py", "sdk-send:Recorder.create"): 1,
    ("validation/worked_examples/evolve.py", "g6.core.llm.AnthropicCaller"): 1,
    ("validation/worked_examples/loop.py", "g6.core.llm.AnthropicCaller"): 1,
    ("validation/worked_examples/run.py", "credential-handed-on:ANTHROPIC_API_KEY"): 4,
}

#: Calls that read a credential's name and hand it to nothing: the environment's own readers and G6's key file.
_KEY_READERS = frozenset({"os.environ.get", "os.environ.pop", "os.getenv", "g6.core.keys.Key"})
_CHECKS = frozenset({"g6.core.cloud_consent.admission", "g6.agents._runtime.consent.process_admission"})


def credential_names(tree_root: Path) -> frozenset[str]:
    """The environment names G6's provider modules declare as a provider's credential or address: the values of
    ``g6.core.llm.PROVIDER_KEYS`` and the names in ``g6.core.host_models.NEVER_PASSED``, read from the tree's source.
    A tree whose provider modules declare neither is an error, never an empty list."""
    names: set[str] = set()
    for module, constant in (("llm", "PROVIDER_KEYS"), ("host_models", "NEVER_PASSED")):
        tree = ast.parse((tree_root / "src" / "g6" / "core" / f"{module}.py").read_text(encoding="utf-8"))
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) \
                else []
            if any(isinstance(target, ast.Name) and target.id == constant for target in targets):
                value = ast.literal_eval(node.value)
                names |= set(value.values() if isinstance(value, dict) else value)
    if not names:
        raise AssertionError("the provider modules declare no credential names: the walk has nothing to look for")
    return frozenset(names)


def handed_on(tree: ast.Module, names: frozenset[str]) -> dict[str, list[int]]:
    """Credential name -> the places in one file that hand it on: the name (or ``NAME=value``) in a list, tuple, set
    or dict, as the target of an item assignment, as a returned or assigned value, or as an argument of any call that
    is not one of the readers above. A comparison and a read of the environment hand nothing on. A place inside a
    ``with`` of the consent check is covered and not counted."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update({alias.asname or alias.name: alias.name for alias in node.names})
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            aliases.update({alias.asname or alias.name: f"{node.module}.{alias.name}" for alias in node.names})

    def qualified(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        return qualified(node.value) + "." + node.attr if isinstance(node, ast.Attribute) else ""

    def named(node: ast.AST) -> str:
        text = node.value if isinstance(node, ast.Constant) else (
            node.values[0].value if isinstance(node, ast.JoinedStr) and node.values
            and isinstance(node.values[0], ast.Constant) else None)
        if not isinstance(text, str):
            return ""
        return text if text in names else text.split("=", 1)[0] if "=" in text and text.split("=", 1)[0] in names else ""

    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        name = named(node)
        parent = parents.get(node)
        if not name or isinstance(parent, (ast.JoinedStr, ast.Compare, ast.Expr)):
            continue                                   # a part of a counted f-string, a comparison, a docstring
        if isinstance(parent, ast.Subscript) and isinstance(parent.ctx, ast.Load):
            continue                                   # a read: environ[NAME]
        if isinstance(parent, ast.Call) and qualified(parent.func) in _KEY_READERS:
            continue
        ancestor, covered = node, False
        while ancestor in parents:
            ancestor = parents[ancestor]
            covered |= isinstance(ancestor, ast.With) and any(
                isinstance(item.context_expr, ast.Call) and qualified(item.context_expr.func) in _CHECKS
                for item in ancestor.items)
        if not covered:
            found.setdefault(name, []).append(node.lineno)
    return found


def _module_names(tree: ast.Module) -> dict[str, str]:
    """Local name -> the dotted name it is bound to, from one file's imports and its plain assignments."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update({alias.asname or alias.name: alias.name for alias in node.names})
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.update({alias.asname or alias.name: f"{node.module}.{alias.name}" for alias in node.names})
    for _ in range(4):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                bound = _dotted(node.value, names)
                names.update({target.id: bound for target in node.targets if isinstance(target, ast.Name) and bound})
    return names


def _dotted(node: ast.AST, names: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    return _dotted(node.value, names) + "." + node.attr if isinstance(node, ast.Attribute) else ""


def provider_sites(tree_root: Path) -> dict[tuple[str, str], int]:
    """Every place under ``validation/`` that reaches a model provider outside the consent check, read by the
    consent inventory's own three walks (``tests/core/test_consent_inventory.py``), run over ``validation/`` as they
    run over ``src/g6``: a provider class or factory called or subclassed, each name resolved through the file's
    imports and assignments (``inventory``); a client process started (the same call, its ``host-process:`` rows);
    a provider SDK's send (``sdk_resources``). Two readings the three walks do not make for ``src/g6`` are added,
    because the runners here need them: a provider reached through another runner's module (``distill.llm.X`` is
    followed into ``distill.py``'s own imports), and a provider credential handed on to a process or a file
    (``handed_on``). A site inside the consent check is not counted. Only the routing trials' synthetic fixtures
    are left out."""
    import tempfile

    inventory = _load("g6_tests_consent_inventory", ROOT / "tests" / "core" / "test_consent_inventory.py")
    files = [path for path in sorted((tree_root / "validation").rglob("*.py"))
             if not path.relative_to(tree_root).as_posix().startswith("validation/routing/fixtures/")]
    found: set[tuple[str, int, str]] = set()
    rows = inventory.inventory(tree_root, extra=tuple(files))
    # the walk gives every file outside src the module name ``new``: a file with a row that rests on such a name is
    # walked again alone, so a class of one runner is never taken for a class of another
    for path in files:
        rel = path.relative_to(tree_root).as_posix()
        if any(row[0] == rel and "new." in row[2] for row in rows):
            rows = [row for row in rows if row[0] != rel] + [
                row for row in inventory.inventory(tree_root, extra=(path,)) if row[0] == rel]
    # a class the runner defines itself is named by the walk as ``new.<Class>``: shown here as ``runner-class:<Class>``
    found |= {(rel, line, "runner-class:" + symbol[4:] if symbol.startswith("new.") else symbol)
              for rel, line, symbol, covered, _debt in rows if rel.startswith("validation/") and not covered}
    providers = inventory.provider_symbols(tree_root)
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in files}
    names = {path: _module_names(tree) for path, tree in trees.items()}

    def beside(path: Path, head: str) -> Path | None:
        """The runner module a bare module name means in ``path``: the file of that name beside it, or the only
        one of that name under ``validation/``."""
        if path.with_name(head + ".py") in trees:
            return path.with_name(head + ".py")
        same = [other for other in trees if other.stem == head]
        return same[0] if len(same) == 1 else None

    def through(path: Path, dotted: str, depth: int = 0) -> str:
        head, _, rest = dotted.partition(".")
        target = beside(path, head)
        if target is None or not rest or depth > 6:
            return ""
        first, _, more = rest.partition(".")
        bound = names[target].get(first)
        if bound is None:
            return ""
        resolved = bound + ("." + more if more else "")
        return resolved if resolved in providers else through(target, resolved, depth + 1)

    credentials = credential_names(tree_root)
    with tempfile.TemporaryDirectory() as nothing:       # a root with no src: the SDK walk reads one runner at a time
        for path, tree in trees.items():
            rel = path.relative_to(tree_root).as_posix()
            for node in ast.walk(tree):
                reached = [node.func] if isinstance(node, ast.Call) else (
                    node.bases if isinstance(node, ast.ClassDef) else [])
                for callee in reached:
                    dotted = _dotted(callee, names[path])
                    symbol = "" if dotted in providers else through(path, dotted)
                    if symbol and not symbol.startswith(inventory.SAFE + "."):
                        found.add((rel, node.lineno, symbol))
            for number, (identity, _declared, guarded) in enumerate(inventory.sdk_resources(Path(nothing),
                                                                                           extra=(path,))):
                if not guarded:
                    found.add((rel, -number - 1, "sdk-send:" + identity.removeprefix("new.")))
            for name, lines in handed_on(tree, credentials).items():
                found |= {(rel, line, "credential-handed-on:" + name) for line in lines}
    sites: dict[tuple[str, str], int] = {}
    for rel, _line, symbol in found:
        sites[(rel, symbol)] = sites.get((rel, symbol), 0) + 1
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
        assert entry["env"] == {run.WITHHOLD_ENV: "g6_find,g6_describe", run.BOUND_ENV: run.package_digest()}
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
