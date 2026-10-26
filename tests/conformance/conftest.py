# SPDX-License-Identifier: Apache-2.0
"""The conformance kit's tests: load ``kit.py`` once as ``conformance_kit`` (the suite imports test files by path, so
a sibling module is not importable by name), and print, after the run, the hits of the checks that are reported and
not required for the core components."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

if "conformance_kit" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("conformance_kit", Path(__file__).resolve().parent / "kit.py")
    _kit = importlib.util.module_from_spec(_spec)
    sys.modules["conformance_kit"] = _kit
    _spec.loader.exec_module(_kit)


def pytest_terminal_summary(terminalreporter) -> None:
    kit = sys.modules.get("conformance_kit")
    if kit is None or not kit.REPORTED:
        return
    terminalreporter.write_sep("-", "conformance: reported for the core, required of extensions")
    for check, by_name in sorted(kit.REPORTED.items()):
        hits = {name: found for name, found in by_name.items() if found}
        total = sum(len(found) for found in hits.values())
        detail = ", ".join(f"{name} {len(found)}" for name, found in sorted(hits.items()))
        terminalreporter.write_line(f"{check}: {total} hit(s){' (' + detail + ')' if detail else ''}")
