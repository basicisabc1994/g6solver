# SPDX-License-Identifier: Apache-2.0
"""Study code lives in oss/validation (not an installed package): put it on the path for these tests."""
from __future__ import annotations

import sys
from pathlib import Path

VALIDATION = Path(__file__).resolve().parents[2] / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))
