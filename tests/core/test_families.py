# SPDX-License-Identifier: Apache-2.0
"""Extensions live in family packages (``g6.<family>.<name>``, DECISIONS #37): the registry walks one level into each
family without importing anything, registers each member under its own name, and an agent reaches a member through
``g6_find``, ``g6_describe`` and ``g6_invoke`` like any other component."""
from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

import pytest

import g6
from g6.core import discovery, registry
from g6.mcp import describe_reply, invoke_reply

SRC = Path(g6.__file__).parent
HANDOFF_FAMILIES = {"media", "ml", "agents", "knowledge", "memory", "sources", "deploy", "workspace", "code",
                    "embodied", "cogarch", "provers", "domains", "jobs", "integrations"}

MEMBER = '''"""Counts zebra stripes on a widget, for the family layout tests."""
from typing import Literal

from pydantic import BaseModel

from g6.core.ai_block import AIBlock
from g6.core.result import Result


class StripeInput(BaseModel):
    op: Literal["count", "ops", "help"] = "help"
    text: str = ""


class StripeBlock(AIBlock[StripeInput, dict, dict]):
    def __init__(self, name: str = "stripe_counter", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: StripeInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"ops": ["count"]})
        if data.op == "help":
            return Result.ok({"help": ["count: how often the word stripe appears in a text"]})
        return Result.ok({"count": data.text.count("stripe"), "completion_state": "qualified-draft"})


__g6_component__ = True
__g6_block__ = "StripeBlock"
__g6_requires__ = ()
'''


@pytest.fixture
def family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_reset) -> Path:
    """A family package ``g6.testfamily`` on a second portion of the ``g6`` path, holding ``stripe_counter`` and the
    shared code a family may keep (``_types.py``, ``_shared/``), which never registers."""
    fam = tmp_path / "g6" / "testfamily"
    (fam / "stripe_counter").mkdir(parents=True)
    (fam / "__init__.py").write_text('"""A family for tests."""\n__g6_family__ = "testfamily"\n', encoding="utf-8")
    (fam / "stripe_counter" / "__init__.py").write_text(MEMBER, encoding="utf-8")
    (fam / "_types.py").write_text("X = 1\n", encoding="utf-8")
    (fam / "_shared").mkdir()
    (fam / "_shared" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(tmp_path / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {"testfamily"})
    importlib.invalidate_caches()
    yield fam
    for name in [m for m in sys.modules if m == "g6.testfamily" or m.startswith("g6.testfamily.")]:
        del sys.modules[name]


def _member(fam: Path, name: str, source: str) -> None:
    (fam / name).mkdir()
    (fam / name / "__init__.py").write_text(source, encoding="utf-8")
    importlib.invalidate_caches()


def test_the_families_are_the_handoffs_fifteen() -> None:
    assert registry.FAMILIES == HANDOFF_FAMILIES


def test_family_packages_on_disk_are_exactly_the_families() -> None:
    def declared(init: Path) -> str | None:
        for node in ast.parse(init.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__g6_family__" for t in node.targets):
                return ast.literal_eval(node.value)
        return None

    on_disk = {p.name for p in SRC.iterdir() if (p / "__init__.py").is_file() and declared(p / "__init__.py") == p.name}
    assert on_disk == registry.FAMILIES


def test_a_family_package_is_not_a_component() -> None:
    names = set(registry.get_registry().names())
    for name in sorted(registry.FAMILIES):
        assert importlib.import_module(f"g6.{name}").__g6_family__ == name
        assert name not in names


def test_a_member_registers_under_its_own_name_without_importing_anything(family: Path) -> None:
    before = set(sys.modules)
    reg = registry.ComponentRegistry()
    names = reg.names()
    assert "stripe_counter" in names and "testfamily" not in names
    assert not {"_types", "_shared"} & set(names)
    assert reg.refs["stripe_counter"] == registry.ComponentRef(
        "stripe_counter", "g6.testfamily.stripe_counter", "builtin", "testfamily")
    newly = {m for m in set(sys.modules) - before if m.startswith("g6.")}
    assert newly == set()


def test_an_agent_reaches_a_member_through_find_describe_and_invoke(family: Path) -> None:
    assert discovery.find("count the zebra stripes on my widget")[0]["component"] == "stripe_counter"
    assert "g6.testfamily.stripe_counter" not in sys.modules  # ranking read the docstring, it did not import

    info = describe_reply("stripe_counter")
    assert info["available"] and info["family"] == "testfamily" and info["package"] == "g6.testfamily.stripe_counter"
    assert info["ops"] == ["count"]

    reply = invoke_reply("stripe_counter", "count", json.dumps({"text": "stripe, stripe"}))
    assert reply["ok"] and reply["result"]["count"] == 2 and reply["completion_state"] == "qualified-draft"


def test_a_member_that_fails_to_import_cannot_break_find(family: Path) -> None:
    _member(family, "broken_member", '"""Paints zebra stripes, then fails on import."""\nraise ImportError("boom")\n')
    found = [r["component"] for r in discovery.find("zebra stripes", limit=10)]
    assert {"stripe_counter", "broken_member"} <= set(found)


def test_a_leaf_name_collision_is_recorded_and_never_raises(family: Path) -> None:
    _member(family, "lean", '"""A second component called lean."""\n')
    reg = registry.get_registry()
    assert reg.names().count("lean") == 1 and reg.refs["lean"].module == "g6.lean"  # the core package keeps its name
    assert reg.collisions() == [("lean", "g6.lean", "g6.testfamily.lean")]

    from g6.cli.doctor import run_checks

    names = [c for c in run_checks({}) if c.name == "component names"]
    assert names and names[0].status == "fail" and "g6.testfamily.lean" in names[0].detail


def test_doctor_passes_when_no_two_components_share_a_name(registry_reset) -> None:
    from g6.cli.doctor import run_checks

    assert [c.status for c in run_checks({}) if c.name == "component names"] == ["ok"]


def test_describe_names_the_family_and_the_package() -> None:
    lean, router = registry.get_registry().describe("lean"), registry.get_registry().describe("llm_router")
    assert lean["family"] is None and lean["package"] == "g6.lean"
    assert router["family"] == "integrations" and router["package"] == "g6.llm_router"
