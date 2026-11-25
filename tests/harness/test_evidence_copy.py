"""Copy contracts for the new card and record templates."""
from __future__ import annotations

import re
import ast
import json
from pathlib import Path

from g6.harness import patch_report
from g6.harness.workflow import record_md
from tests.harness.test_evidence_card import assessment, recorded


def test_new_templates_obey_the_claims_rule():
    value, _ = recorded(assessment())
    templates = [*patch_report.card_lines(patch_report.card(value, None)), record_md.render([]),
                 *record_md.self_changes(None, {})]
    banned = ("experi" + "mental", "in" + "finite", "pro" + "of", "pro" + "ve", "veri" + "fied", "prevented", "saved you",
              "would have shipped", "caught a bug", "guaranteed", "safe", "confidence")
    for text in templates:
        for word in banned:
            assert not re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE), (word, text)
        assert not re.search(r"\d\s*%|\brate\b|\btrend\b", text, re.IGNORECASE)


def test_changed_tool_and_plugin_descriptions_obey_the_claims_rule():
    root = Path(__file__).resolve().parents[2]
    descriptions = [json.loads((root / "plugins/g6/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["description"]]
    for file, function in (("src/g6/mcp/__init__.py", "g6_review_until_clean"),
                           ("src/g6/mcp/workflow.py", "g6_workflow_export")):
        tree = ast.parse((root / file).read_text(encoding="utf-8"))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
        descriptions.append(ast.get_docstring(node))
    for text in descriptions:
        for word in ("experi" + "mental", "in" + "finite", "pro" + "of", "pro" + "ve", "veri" + "fied", "safe",
                     "prevented", "saved you", "would have shipped", "caught a bug", "guaranteed", "confidence"):
            assert not re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE), word
