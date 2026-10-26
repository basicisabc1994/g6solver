# SPDX-License-Identifier: Apache-2.0
"""The media tests' shared helpers: load ``media_kit.py`` once as ``media_kit`` (the suite imports test files by path,
so a sibling module is not importable by name)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

if "media_kit" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("media_kit", Path(__file__).resolve().parent / "media_kit.py")
    _kit = importlib.util.module_from_spec(_spec)
    sys.modules["media_kit"] = _kit
    _spec.loader.exec_module(_kit)
