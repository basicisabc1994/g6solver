# SPDX-License-Identifier: Apache-2.0
"""The published component catalogue and failure-mode map must match the code (docs follow the finalised components)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_catalog = _tool("gen_catalog")


def test_catalog_is_current():
    assert gen_catalog.main(["--check"]) == 0, "run: python tools/gen_catalog.py"


def test_failure_map_is_current():
    assert _tool("gen_failure_map").main(["--check"]) == 0, "run: python tools/gen_failure_map.py"
