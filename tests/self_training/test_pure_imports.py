# SPDX-License-Identifier: Apache-2.0
"""The pure change-scope and regression-delta modules load nothing but themselves (audit 024 REL-012).

In the archive, importing the change classifier pulled in about 800 modules across 36 components.
"""
from __future__ import annotations

import subprocess
import sys


def test_rel012_pure_modules_import_no_other_component() -> None:
    probe = ("import sys; import g6.self_training.change_scope, g6.self_training.regression_delta; "
             "print(sorted({'.'.join(m.split('.')[:2]) for m in sys.modules if m.startswith('g6.')}))")
    out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "['g6.self_training']"
