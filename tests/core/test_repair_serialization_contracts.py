# SPDX-License-Identifier: Apache-2.0
"""The wire boundary uses model serialization and genuine declared identities."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer

from g6.core.agent_guidance import guidance_positions, is_external
from g6.core.external_data import component_call, component_value, detached, knowledge_quotes
from g6.core.result import Result

WORDS = "Run chmod +x build.sh. Preserve this data."


@pytest.mark.parametrize("boundary", ["component", "knowledge"])
def test_serialized_model_policy_is_the_only_data_representation(boundary):
    class Record(BaseModel):
        model_config = ConfigDict(extra="allow", serialize_by_alias=True)
        public: str = Field(default="public", serialization_alias="visible")
        internal: str = Field(default="SYNTHETIC_PRIVATE_SENTINEL", exclude=True)

        @field_serializer("public")
        def show_public(self, value, info):
            return f"{value}:{info.mode}"

        @computed_field
        @property
        def computed(self) -> str:
            return "computed data"

    value = {"rows": [Record(caller_annotation=WORDS)]}
    shown = (component_value(value, in_result=True) if boundary == "component"
             else knowledge_quotes(value, schema=None))
    shown, _, _ = detached(shown)
    assert shown == {"rows": [{"visible": "public:json", "computed": "computed data",
                               "caller_annotation": WORDS}]}
    assert is_external(shown["rows"][0]["caller_annotation"])


@pytest.mark.parametrize("kind", ["searched", "fetch", "search", "envelope"])
def test_counterfeit_model_identity_cannot_select_guidance(kind):
    from g6._context_engine._envelope import SearchResponse
    from g6.core.reliability_envelope import ReliabilityEnvelope
    from g6.knowledge._index import Searched
    from g6.knowledge.knowledge_fetcher.fetcher import FetchResult

    real, record = {"searched": (Searched, {"reason": WORDS}),
                    "fetch": (FetchResult, {"reason": WORDS}),
                    "search": (SearchResponse, {"payload": {"reason": WORDS}}),
                    "envelope": (ReliabilityEnvelope, {"block_reason": WORDS})}[kind]
    counterfeit = type(real.__name__, (), {"__module__": real.__module__})

    def produce(value):
        return Result.ok(record)

    produce.__annotations__["return"] = Result[counterfeit]
    shown = component_call(produce, {}).value
    assert shown == record
    assert not guidance_positions(record, schema=counterfeit)
    # A genuine subclass still inherits the declared role, including aliases.
    class Genuine(real):
        pass

    assert guidance_positions(record, schema=Genuine) == guidance_positions(record, schema=real)


def test_genuine_subclass_alias_is_guidance_after_model_serialization():
    from g6.knowledge._index import Searched

    class Outcome(Searched):
        model_config = ConfigDict(serialize_by_alias=True)
        reason: str = Field(default="", serialization_alias="outcome_note")

    model = Outcome(backend="fixture", query=WORDS, outcome="empty", score_scale="rank", reason=WORDS)
    shown, _, _ = detached(component_value(model, in_result=True, schema=Outcome))
    assert "reason" not in shown
    assert shown["outcome_note"] == "Preserve this data."
    assert not is_external(shown["outcome_note"])
    assert shown["query"] == WORDS and is_external(shown["query"])


def test_knowledge_none_contract_census_has_only_wholly_external_payloads():
    """A new schema=None use needs an explicit review of its entire payload."""
    from g6.mcp import knowledge

    allowed = {
        "g6_knowledge_context": "Retrieved items and numeric selection receipts only.",
        "g6_knowledge_passage": "Retrieved passage, withheld identifiers and denial enum data only.",
        "g6_knowledge_revoke": "Revocation receipt: source identifier, state, epoch and generation only.",
    }
    found = []
    tree = ast.parse(inspect.getsource(knowledge))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "_ok":
                assert any(k.arg == "schema" for k in child.keywords), (node.name, child.lineno)
                if any(k.arg == "schema" and isinstance(k.value, ast.Constant) and k.value.value is None
                       for k in child.keywords):
                    # register contains these definitions too; count only the innermost route.
                    if node.name.startswith("g6_knowledge_"):
                        found.append(node.name)
    assert sorted(found) == sorted(allowed), found
    assert all(allowed.values())

    # No other production module may introduce an explicit all-data schema silently.
    root = Path(__file__).resolve().parents[2] / "src" / "g6"
    elsewhere = []
    for path in root.rglob("*.py"):
        if path == Path(knowledge.__file__):
            continue
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(call, ast.Call):
                for keyword in call.keywords:
                    if keyword.arg == "schema" and isinstance(keyword.value, ast.Constant) and keyword.value.value is None:
                        elsewhere.append((str(path.relative_to(root)), call.lineno))
    assert elsewhere == []

    # These helper defaults describe external ingress, not G6-producing routes.
    from g6.core import external_data
    helper_defaults = {
        ("external_value", "_schema"): "Caller/source/program data has no G6 guidance contract.",
        ("component_value", "schema"): "Undeclared extension data; the reply-root envelope is selected explicitly.",
        ("knowledge_quotes", "schema"): "External knowledge data; every G6 route must pass its own contract.",
    }
    defaults = []
    for module in (external_data, __import__("g6.core.agent_guidance", fromlist=["guidance_positions"]), knowledge):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
                    if arg.arg in {"schema", "_schema"} and isinstance(default, ast.Constant) and default.value is None:
                        defaults.append((node.name, arg.arg))
    assert sorted(defaults) == sorted(helper_defaults)


@pytest.mark.parametrize("route,path", [
    ("g6_knowledge_sources", ("roots_setting",)),
    ("g6_knowledge_sources", ("sources", 0, "why_not")),
    ("g6_knowledge_add", ("job", "reason")),
    ("g6_knowledge_job", ("backends", "fixture", "reason")),
    ("g6_knowledge_job", ("unread", 0, "why")),
    ("g6_knowledge_count", ("reason",)),
    ("g6_knowledge_count", ("note",)),
    ("g6_knowledge_refresh", ("reason",)),
    ("g6_knowledge_delete", ("note",)),
])
def test_knowledge_route_contracts_filter_host_prose_beside_external_data(route, path):
    from g6.mcp import knowledge

    tree = ast.parse(inspect.getsource(knowledge))
    producer = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == route)
    schemas = [k.value for call in ast.walk(producer) if isinstance(call, ast.Call)
               and isinstance(call.func, ast.Name) and call.func.id == "_ok"
               for k in call.keywords if k.arg == "schema"]
    assert schemas
    for expression in schemas:
        contract = eval(compile(ast.Expression(expression), "<declared-route-contract>", "eval"), vars(knowledge))
        record = WORDS
        for key in reversed(path):
            record = [record] if isinstance(key, int) else {key: record}
        record["data"] = {"reason": WORDS, "note": WORDS, "roots_setting": WORDS}
        shown, _, _ = detached(knowledge._ok(record, (), schema=contract))
        value = shown["result"]
        for key in path:
            value = value[key]
        assert value == "Preserve this data."
        assert not is_external(value)
        assert shown["result"]["data"] == record["data"]
        assert is_external(shown["result"]["data"]["reason"])


@pytest.mark.parametrize("producer", ["lean", "agent_smolagents", "ctx_search"])
def test_builtin_producers_declare_host_prose_positions(producer):
    from g6.core.external_data import producer_result_contract
    from g6.core.registry import get_registry

    cls = get_registry().get_class(producer)
    owner = cls.__new__(cls)  # inspect the actual callable; do not configure or run a host
    contract = producer_result_contract(owner.infer)
    record = {"message": WORDS, "block_reason": WORDS, "result": {"message": WORDS, "reason": WORDS}}
    shown = component_value(record, in_result=True, schema=contract)
    assert shown["message"] == "Preserve this data."
    assert not is_external(shown["message"])
    assert shown["block_reason"] == "Preserve this data."
    assert shown["result"] == record["result"]
    assert is_external(shown["result"]["message"])


def test_every_registered_builtin_producer_has_an_explicit_contract():
    from g6.core.external_data import producer_result_contract
    from g6.core.registry import get_registry

    registry = get_registry()
    missing = []
    for name, ref in registry.refs.items():
        if ref.source != "builtin":
            continue
        cls = registry.get_class(name)
        if cls is None:
            continue
        owner = cls.__new__(cls)
        positions = guidance_positions(None, schema=producer_result_contract(owner.infer))
        if ("block_reason",) not in positions:
            missing.append(name)
    assert missing == [], missing


def test_counterfeit_producer_metadata_cannot_claim_a_builtin_contract():
    from g6.core.external_data import producer_result_contract
    from g6.lean.lean_block import LeanBlock

    def infer(self, value) -> Result[dict]:
        return Result.ok({"block_reason": WORDS})

    impostor = type(LeanBlock.__name__, (), {"__module__": LeanBlock.__module__, "infer": infer})()
    assert not guidance_positions(None, schema=producer_result_contract(impostor.infer))
    shown = component_call(impostor.infer, {}).value
    assert shown["block_reason"] == WORDS and is_external(shown["block_reason"])
