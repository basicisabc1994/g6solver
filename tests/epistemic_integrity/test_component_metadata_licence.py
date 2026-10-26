# SPDX-License-Identifier: Apache-2.0
"""EPISTEMIC_INTEGRITY-01 (audit 2026-10-23): the component metadata shipped ``license_status: proprietary`` and the
archive's ``mvp.*`` wiring notes in the Apache-2.0 library. The pattern of ``tests/action_gating``'s AG-013 test."""
from __future__ import annotations

import re
from pathlib import Path

import g6.epistemic_integrity


def test_component_metadata_licence_matches_the_file_headers() -> None:
    # fails if component_metadata.yaml's "license_status" is anything but Apache-2.0, or it names an archive module
    root = Path(g6.epistemic_integrity.__file__).parent
    spdx = {re.search(r"SPDX-License-Identifier:\s*(\S+)", f.read_text(encoding="utf-8")).group(1)
            for f in root.glob("*.py")}
    meta = (root / "component_metadata.yaml").read_text(encoding="utf-8")
    assert spdx == {"Apache-2.0"}
    assert re.search(r"^license_status:\s*(\S+)", meta, re.M).group(1) == "Apache-2.0"
    assert "mvp." not in meta and "proprietary" not in meta.lower()
