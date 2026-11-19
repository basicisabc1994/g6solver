# SPDX-License-Identifier: Apache-2.0
"""The planned gate consumers share one vocabulary; unrelated run verdicts do not."""
from pathlib import Path

from g6.coding_agent_gate import surface_state
from g6.coding_agent_gate.coding_agent_gate_block import _OPS
from g6.coding_agent_gate.gate import VERDICT_RANK

ROOT = Path(__file__).resolve().parents[2]
WORDS = {"pass", "report_consistent", "revise", "needs_review", "block"}
CONSUMERS = (
    "src/g6/coding_agent_gate/gate.py", "src/g6/coding_agent_gate/coding_agent_gate_block.py",
    "src/g6/coding_agent_gate/evidence.py", "src/g6/coding_agent_gate/review.py",
    "src/g6/coding_agent_gate/__init__.py", "src/g6/coding_agent_gate/skill/SKILL.md",
    "src/g6/skills/corpus.json", "src/g6/skills/using-g6/SKILL.md",
    "plugins/g6/skills/using-g6/SKILL.md", "src/g6/skills/using-g6/references/lifecycle.md",
    "plugins/g6/skills/using-g6/references/lifecycle.md", "src/g6/bundles/agent_change.py",
    "src/g6/harness/patch_review.py", "tests/coding_agent_gate/test_audit_004_regressions.py",
    "tests/coding_agent_gate/test_audit_019_claim_evidence.py", "tests/coding_agent_gate/test_receipt_evidence.py",
    "tests/coding_agent_gate/test_merge_protected_paths.py", "tests/harness/test_patch_review.py",
    "tests/core/data/worked_example_review_reply.json", "docs/releases/v0.3.0.md",
    "src/g6/algorithm_design/autonomous.py", "tests/algorithm_design/test_autonomous.py",
    "tests/workforce/test_receipt_store.py",
    "docs/theory/AUTONOMOUS_ALGORITHM_SWE_AGENT.md",
)


def test_every_planned_consumer_is_named_and_vocabulary_is_shared():
    assert set(VERDICT_RANK["verify_answer"]) == WORDS
    assert surface_state("report_consistent") == "qualified-draft"
    assert "report_consistent" in _OPS["verify_answer"]
    assert all((ROOT / path).is_file() for path in CONSUMERS)
    for path in ("src/g6/coding_agent_gate/__init__.py", "src/g6/coding_agent_gate/skill/SKILL.md",
                 "src/g6/skills/using-g6/SKILL.md", "plugins/g6/skills/using-g6/SKILL.md",
                 "docs/releases/v0.3.0.md"):
        assert "report_consistent" in (ROOT / path).read_text(encoding="utf-8"), path


def test_autonomous_consumer_handles_the_new_word_without_claiming_observation():
    source = (ROOT / "src/g6/algorithm_design/autonomous.py").read_text(encoding="utf-8")
    assert 'decision.verdict == "report_consistent"' in source
    assert '"report_consistent_pending_observation"' in source


def test_bundle_does_not_render_its_unreceipted_gate_evidence_as_observed():
    source = (ROOT / "src/g6/bundles/agent_change.py").read_text(encoding="utf-8")
    assert "The tests ran and passed on the change" not in source
    assert "without artifact-bound test receipts" in source
