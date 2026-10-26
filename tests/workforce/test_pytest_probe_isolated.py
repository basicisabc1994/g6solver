# SPDX-License-Identifier: Apache-2.0
"""The pytest declaration's probe (``g6.workforce.__g6_outside__``) imports pytest in the Python running G6.

Review B2-M2: it ran ``python -c "import pytest"`` without ``-I``, and ``-c`` puts the working folder first on the
import path, so a ``pytest.py`` planted in the folder G6 was started from ran on the host. Offline.
"""
from __future__ import annotations

import subprocess
import sys

import g6.workforce
from g6.core.outside import detect


def test_the_probe_never_imports_a_pytest_planted_in_the_working_folder(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "marker"
    (tmp_path / "pytest.py").write_text(f"import pathlib\npathlib.Path(r'{marker}').write_text('ran')\n"
                                        "__version__ = 'planted'\n", encoding="utf-8")
    subprocess.run([sys.executable, "-c", "import pytest"], cwd=tmp_path, capture_output=True, timeout=60, check=False)
    assert marker.exists()   # survival control: the plant is live for a plain -c
    marker.unlink()
    monkeypatch.chdir(tmp_path)
    found, = [p for p in g6.workforce.__g6_outside__ if p.name == "pytest"]
    detected = detect(found)
    assert not marker.exists()
    assert detected.found and "planted" not in detected.version
