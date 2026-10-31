# SPDX-License-Identifier: Apache-2.0
"""No file of the algorithm-design component, and none of its theory documents, names its kernels "certified" or
"verified" or calls anything "experimental" (P1, PR #3; DECISIONS #36): a kernel checks one answer, it is not a proof."""
from __future__ import annotations

from pathlib import Path

import g6.algorithm_design

PACKAGE = Path(g6.algorithm_design.__file__).parent
OSS = PACKAGE.parents[2]
DOCS = [OSS / "docs" / "theory" / f"{name}.md"
        for name in ("ALGORITHM_KERNELS", "AUTONOMOUS_ALGORITHM_SWE_AGENT", "ALGORITHM_DESIGN_KNOWLEDGE_BUNDLE")]
WORDS = ("certified host registry", "verified_kernels", "CertifiedResult", "(experimental)")


def test_no_file_claims_certified_or_verified_kernels():
    files = [p for p in sorted(PACKAGE.rglob("*")) if p.is_file() and p.suffix in (".py", ".md", ".json")] + DOCS
    assert all(p.is_file() for p in DOCS), [p.name for p in DOCS if not p.is_file()]
    found = [(p.name, w) for p in files for w in WORDS if w in p.read_text(encoding="utf-8")]
    assert found == []
    assert sorted(p.name for p in (OSS / "docs").rglob("VERIFIED_*")) == []
