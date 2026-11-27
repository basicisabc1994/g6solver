"""Copy contracts for the new card and record templates."""
from __future__ import annotations

import re
from pathlib import Path

from g6.harness import patch_report
from g6.harness.workflow import evidence, record_md
from tests.harness.test_evidence_card import assessment, recorded
from tests.workflow.kit import event

BANNED = ("experi" + "mental", "in" + "finite", "pro" + "of", "pro" + "ve", "veri" + "fied", "prevented", "saved you",
          "would have shipped", "caught a bug", "guaranteed", "safe", "confidence")


def _obeys(text: str) -> None:
    for word in BANNED:
        assert not re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE), (word, text)
    assert not re.search(r"\d\s*%|\brate\b|\btrend\b", text, re.IGNORECASE), text


def _card_variants() -> list[str]:
    """Every card template: recorded, unrecorded in each record status, nothing checked, and a loop's card."""
    value, _ = recorded(assessment())
    cards = [patch_report.card(value, None), patch_report.card({"unchecked_reason": "No Python changed."}, None)]
    cards += [patch_report.card({"workflow_evidence": evidence.bundle([], status=status)}, None)
              for status in ("kept", "off", "withheld", "write_failed", "unavailable")]
    loop = evidence.bundle([event("a" * 32, "loop_ended", data={"reviews": 0, "probes": 3, "reproduced": 1, "open": 1,
                                                                "ending": "open", "hook_present": 1})])
    cards.append(patch_report.card({}, {"workflow_evidence": loop, "left": "out of scope"}))
    return [line for card in cards for line in patch_report.card_lines(card)]


def test_new_templates_obey_the_claims_rule():
    templates = [*_card_variants(), record_md.render([]), record_md.render([], recording=False),
                 *record_md.self_changes(None, {})]
    for text in templates:
        _obeys(text)


def test_findings_are_reproduced_until_the_person_labels_them():
    for line in _card_variants():
        if line.startswith("- **Caught:**") and "not recorded" not in line:
            assert "reproduced findings" in line
        if line.startswith("- **Repaired:**") and re.search(r"Open: [1-9]", line):
            assert "Not labelled by you." in line


def test_plugin_readme_card_sentence_obeys_the_claims_rule():
    root = Path(__file__).resolve().parents[2]
    text = (root / "plugins/g6/README.md").read_text(encoding="utf-8")
    paragraph = next(part for part in text.split("\n\n") if "card" in part.lower())
    _obeys(paragraph)
