# SPDX-License-Identifier: Apache-2.0
"""The theory documents render on GitHub (owner, 2026-10-30: "fix the 'operatorname' bug on github"). GitHub's maths
renderer refuses some TeX commands and shows the whole formula as an error instead; these documents use \\mathrm
where they would use \\operatorname, which reads the same."""
from __future__ import annotations

import re
from pathlib import Path

THEORY = Path(__file__).resolve().parents[1] / "docs" / "theory"
REFUSED = re.compile(r"\\(operatorname|DeclareMathOperator|newcommand|renewcommand|def\\|require\{|href\{)")


def test_the_theory_documents_use_no_tex_command_github_refuses():
    found = {p.name: REFUSED.findall(p.read_text(encoding="utf-8")) for p in sorted(THEORY.glob("*.md"))}
    assert found and not {name: hits for name, hits in found.items() if hits}
