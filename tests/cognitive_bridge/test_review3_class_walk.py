# SPDX-License-Identifier: Apache-2.0
"""Review 3's walk of the class list over the bridge, which passed and must keep passing: every listed judge and core
file is protected, registration and its surfaces agree, an unknown adjudicator status fails closed, and model text stays
data."""
from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_all_producer_listed_judges_and_core_files_are_protected():
    from g6.cognitive_bridge import adaptation

    files = adaptation.judged_files() | frozenset((ROOT / "src/g6/core").rglob("*.py"))
    for file in files:
        path = file.relative_to(ROOT).as_posix()
        assert adaptation.protected_paths([path]) == [path]
        assert adaptation.protected_paths([str(file)]) == [str(file)]
    for path in adaptation.ADAPTABLE:
        assert adaptation.protected_paths([path]) == []


def test_registration_catalogue_help_skill_failure_map_and_counts():
    from g6.cognitive_bridge import CognitiveBridgeBlock, codes
    from g6.core import discovery, skills
    from g6.core.registry import get_registry
    from g6.mcp import describe_reply

    name = "cognitive_bridge"
    registry = get_registry()
    assert name in registry.names()
    ops = set(discovery.ops(name))
    assert describe_reply(name)["name"] == name
    assert CognitiveBridgeBlock().infer({"op": "help"}).value["help"]
    skill = skills.skill_text(name)
    assert skill and all(code in skill for code in codes.CODES)
    rows = json.loads((ROOT / "catalog/components.json").read_text(encoding="utf-8"))
    row = next(r for r in rows if r["name"] == name)
    assert set(row["ops"]) == ops
    failures = json.loads((ROOT / "catalog/failure_modes.json").read_text(encoding="utf-8"))
    assert any(name in row.get("guarded_by", []) for row in failures)
    for file in ("README.md", "site/docs/components.md", "site/docs/faq.md", "web/content/faq.md",
                 "web/pages/catalogue.py", "pyproject.toml"):
        assert name in (ROOT / file).read_text(encoding="utf-8")
    # Count the site producer's actual family partition, without invoking Django or HTTP.
    import sys
    monkey_path = str(ROOT / "web")
    sys.path.insert(0, monkey_path)
    try:
        from pages import catalogue
        assert len(catalogue.components()) == 35
        assert name in {r["name"] for r in catalogue.components()}
    finally:
        sys.path.remove(monkey_path)


def test_unknown_adjudicator_status_fails_closed(monkeypatch):
    from g6.cognitive_bridge import CrossMethodConflictGate, ScopedAssertion
    from g6.epistemic_integrity import adjudicator

    real = adjudicator.adjudicate_claim

    def unknown(*args, **kwargs):
        result = real(*args, **kwargs)
        return replace(result, claim=replace(result.claim, epistemic_status="NEVER_SEEN"))

    monkeypatch.setattr(adjudicator, "adjudicate_claim", unknown)
    assertion = ScopedAssertion("c", "switch-on", "a" * 64, "supports", "formal", "source:one", "receipt")
    result = CrossMethodConflictGate(admit_assertion=lambda a: True).reconcile([assertion])
    assert result["completion_state"] == "blocked-escalated"


def test_model_text_stays_data_and_cannot_supply_execution_fields():
    from g6.cognitive_bridge import LLMClaimCompiler

    class FakeModel:
        def complete(self, *args, **kwargs):
            return json.dumps({"claims": [{"id": "c", "statement": "check",
                "evidence_needs": ["formal"], "models": {"formal": {"formula": "A"}},
                "execute_now": "__import__('os').system('forbidden')"}]})

    with pytest.raises(ValueError, match="unexpected fields"):
        LLMClaimCompiler(FakeModel()).draft("check")
    for file in (ROOT / "src/g6/cognitive_bridge").glob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id in {"exec", "eval"}], file.name
