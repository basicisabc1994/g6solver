"""Fresh MCP refusals retain their producer's code through every reply boundary."""
import ast
from contextlib import nullcontext
import importlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.mcp import consent, improve, knowledge, mission, review_loop, review_reply, workflow
from g6.mcp import cloud_consent as egress
from g6.mcp.composition import compose_reply
from g6.metaharness.jobs import Jobs


def _meta_refusal():
    from g6.core.door import blocked_reply
    from g6.mcp import _metaharness_tools

    functions = {}

    class Server:
        def tool(self):
            def keep(fn):
                functions[fn.__name__] = fn
                return fn
            return keep

    surface = SimpleNamespace(inspect=lambda name: blocked_reply("[G6_E_META_NOT_CONFIGURED] no workflows"))
    _metaharness_tools(Server(), surface, object)
    return functions['metaharness_inspect']('fixture')


def _bound_review_refusal():
    from g6.core.door import blocked_reply
    from g6.mcp import behaviour

    return behaviour.bound({**blocked_reply("[G6_E_RESOURCE_LIMIT] result not read"), "assessment": {}}, "fixture")


def _selector_refusal():
    from g6.mcp import selector

    functions = {}

    class Server:
        def tool(self):
            def keep(fn):
                functions[fn.__name__] = fn
                return fn
            return keep

    selector.register(Server())
    return functions['g6_select']('')


def test_no_mcp_refusal_uses_unbound_door_factory():
    root = Path(__file__).parents[2]
    files = subprocess.run(["git", "ls-files", "src/g6/mcp/*.py"], cwd=root,
                           check=True, capture_output=True, text=True).stdout.splitlines()
    bad = []
    for rel in files:
        if rel.endswith("/consent_controls.py"):
            continue  # The adapter alone owns the raw factory.
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update({a.asname or a.name.split('.')[0]: a.name if a.asname else a.name.split('.')[0]
                                for a in node.names})
            if isinstance(node, ast.ImportFrom):
                aliases.update({a.asname or a.name: f'{node.module}.{a.name}' for a in node.names})
            if isinstance(node, ast.ImportFrom) and node.module == "g6.core.door":
                if any(alias.name == "blocked_reply" for alias in node.names):
                    bad.append((rel, node.lineno))
        def qualified(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return qualified(node.value) + '.' + node.attr
            return ''

        assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
        for _ in range(len(assignments) + 1):
            for node in assignments:
                if isinstance(node.value, (ast.Name, ast.Attribute)):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = qualified(node.value)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            symbol = qualified(call.func)
            if symbol not in {'g6.core.door.blocked_reply', 'g6.metaharness.api._blocked'}:
                continue
            owner = parents.get(call)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            if (Path(rel).name, getattr(owner, 'name', '')) != ('improve.py', '_blocked'):
                bad.append((rel, call.lineno, symbol))
    assert bad == [], bad


def test_mcp_job_handle_refusals_use_producer_adapter():
    root = Path(__file__).parents[2]
    files = subprocess.run(["git", "ls-files", "src/g6/mcp/*.py"], cwd=root,
                           check=True, capture_output=True, text=True).stdout.splitlines()
    bad = []
    for rel in files:
        tree = ast.parse((root / rel).read_text(encoding='utf-8'))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'jobs'
                    and node.func.attr in {'start', 'status'}):
                parent = parents[node]
                if not isinstance(parent, ast.Call) or getattr(parent.func, 'id', '') != 'producer_reply':
                    bad.append((rel, node.lineno))
    assert bad == [], bad


FACTORIES = [
    ("invoke", lambda: importlib.import_module("g6.mcp").invoke_reply("unused", "unused", "[")),
    ("invoke-door", lambda: importlib.import_module("g6.mcp").invoke_reply("no_such_fixture_component", "run")),
    ("composition", lambda: compose_reply({})),
    ("review-admission", lambda: review_loop.step(r"\\review-host.invalid\share", "task", "session")),
    ("review-step", lambda: review_loop.step(str(Path.cwd()), "task", "session")),
    ("workflow", lambda: workflow._refused("Name a local folder.")),
    ("knowledge", lambda: knowledge._refused("G6_E_KNOWLEDGE_FAILED", "not available")),
    ("mission", lambda: mission._refusal("not available")),
    ("mission-envelope", lambda: mission.reply({"completion_state": "blocked-escalated", "result": "not available"})),
    ("improve", lambda: improve._blocked(improve.START_FAILED, "not available", "try later")),
    ("dialog", lambda: consent.refusal(consent.Consent("declined"))),
    ("input-schema", lambda: egress.input_refusal("g6_review_until_clean", {}, arguments={})),
    ("withheld", lambda: egress.withheld_reply(metadata={"error_code": "G6_E_KNOWLEDGE_FAILED"})),
    ("meta-surface", _meta_refusal),
    ("bound-review", _bound_review_refusal),
    ("selector", _selector_refusal),
]


@pytest.mark.parametrize("name,factory", FACTORIES, ids=[name for name, _ in FACTORIES])
@pytest.mark.parametrize("detail", ["compact", "full"])
def test_refusal_factory_code_survives_direct_and_job_egress(monkeypatch, name, factory, detail):
    ledger = SimpleNamespace(entries=lambda: [])
    lock = SimpleNamespace(guard=nullcontext(), ledger=ledger)
    monkeypatch.setattr(cloud, "_take", lambda: lock)
    monkeypatch.setattr(cloud, "_leave", lambda *args: None)
    from g6.core.door import blocked_reply

    monkeypatch.setattr(review_loop, "_step", lambda *args:
                        blocked_reply("[G6_E_INPUT_VALIDATION] this session belongs to another task"))
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=SimpleNamespace(name="codex"))), request_id="fixture")
    with cloud.collect_sources():
        direct = factory()
        code = direct["warning_card"]["code"]
        _, refs, metadata = cloud.freeze_output(direct)
        assert refs is not None, name
        assert metadata["error_code"] == code
        sent = egress.protect_reply(ctx, direct, refs, record=False)
        assert sent["warning_card"]["code"] == code
        if name.startswith("review-") or name in {"invoke", "composition"}:
            assert "cloud_consent_code" not in sent
    jobs = Jobs()
    try:
        started = jobs.start(factory)
    finally:
        jobs.close()
    with cloud.collect_sources() as capture:
        polled = jobs.status(started["job_id"], detail=detail)
        assert polled["status"] == "completed", polled
        sent = egress.protect_reply(ctx, polled, cloud.output_sources(polled), job=capture.job, record=False)
    assert sent["result"]["warning_card"]["code"] == code
    if name.startswith("review-") or name in {"invoke", "composition"}:
        assert "cloud_consent_code" not in sent["result"]


def test_host_refusal_metadata_does_not_make_diagnostics_public(monkeypatch):
    from g6.mcp.consent_controls import blocked_reply

    private = cloud.Source.text("repository:private", "file", "private diagnostic")
    with cloud.collect_sources(), cloud.request_sources((private,)):
        reply = blocked_reply("[G6_E_INPUT_VALIDATION] private diagnostic")
        _, refs, metadata = cloud.freeze_output(reply)
        assert refs and private in refs
        assert metadata["error_code"] == "G6_E_INPUT_VALIDATION"


def test_review_compaction_keeps_refusal_metadata(monkeypatch):
    from g6.core.door import blocked_reply
    from g6.mcp.consent_controls import refused_send

    monkeypatch.setattr(review_reply, "save", lambda *args: None)
    with cloud.collect_sources():
        reply = {**blocked_reply("[G6_E_INPUT_VALIDATION] refused"), "assessment": {"report": "refused"}}
        reply = refused_send(reply, code="G6_E_INPUT_VALIDATION")
        compact = review_reply.finish(reply, "fixture")
        _, refs, metadata = cloud.freeze_output(compact)
        assert refs is not None
        assert metadata["error_code"] == "G6_E_INPUT_VALIDATION"


def test_composition_keeps_its_door_refusal_code(monkeypatch):
    from g6.core.door import blocked_reply
    from g6.mcp import composition

    result = {**blocked_reply("[G6_E_RESOURCE_LIMIT] result not read"), "evidence": []}
    monkeypatch.setattr(composition, "compose", lambda *args, **kwargs: SimpleNamespace(
        infer=lambda data: SimpleNamespace(value=result)))
    with cloud.collect_sources():
        reply = composition.compose_reply({"stages": [{"component": "fixture", "op": "run"}], "inputs": {}})
        _, refs, metadata = cloud.freeze_output(reply)
        assert refs is not None
        assert metadata["error_code"] == "G6_E_RESOURCE_LIMIT"


def test_mission_compaction_keeps_refusal_binding():
    from g6.core.door import blocked_reply
    from g6.mcp.consent_controls import refused_send

    with cloud.collect_sources():
        found = refused_send({**blocked_reply("[G6_E_RESOURCE_LIMIT] result not read"),
                              "mission": {"completion_state": "blocked-escalated"}}, code="G6_E_RESOURCE_LIMIT")
        compact = mission.finish(found, "fixture")
        _, refs, metadata = cloud.freeze_output(compact)
        assert refs is not None
        assert metadata['error_code'] == 'G6_E_RESOURCE_LIMIT'


def test_host_producer_adapter_retains_task_custody():
    from g6.core.door import blocked_reply
    from g6.mcp.consent_controls import producer_reply
    from g6.mcp.review_binding import BoundReply

    with cloud.collect_sources():
        reply = BoundReply(blocked_reply("[G6_E_RESOURCE_LIMIT] result not read"), "fixture-task")
        held = producer_reply(reply)
        assert held.task_identity == "fixture-task"
        assert callable(held.custody)
        assert cloud.freeze_output(held)[2]['error_code'] == 'G6_E_RESOURCE_LIMIT'


@pytest.mark.parametrize("as_job", [False, True], ids=["direct", "job"])
def test_withheld_input_refusal_does_not_ask_for_source_grants(monkeypatch, as_job):
    from g6.mcp.consent_controls import blocked_reply

    ledger = SimpleNamespace(entries=lambda: [])
    monkeypatch.setattr(cloud, "_take", lambda: SimpleNamespace(guard=nullcontext(), ledger=ledger))
    monkeypatch.setattr(cloud, "_leave", lambda *args: None)
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=SimpleNamespace(name="codex"))), request_id="fixture")
    private = cloud.Source.text("repository:private", "file", "private diagnostic")

    def refuse():
        return blocked_reply("[G6_E_INPUT_VALIDATION] private diagnostic")

    with cloud.collect_sources(), cloud.request_sources((private,)):
        if as_job:
            jobs = Jobs()
            try:
                started = jobs.start(refuse)
            finally:
                jobs.close()
            with cloud.collect_sources() as capture:
                result = jobs.status(started["job_id"])
                sent = egress.protect_reply(ctx, result, cloud.output_sources(result), job=capture.job, record=False)
            sent = sent["result"]
        else:
            result = refuse()
            sent = egress.protect_reply(ctx, result, cloud.output_sources(result), record=False)
        assert sent["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
        assert "private diagnostic" not in str(sent)
        assert "cloud_consent_code" not in sent
        assert "g6_cloud_grant" not in str(sent)
