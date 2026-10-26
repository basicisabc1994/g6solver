# SPDX-License-Identifier: Apache-2.0
"""What every template may read: the site's address, G6's version and the year. Nothing about a visitor."""
from __future__ import annotations

import re
from datetime import date
from functools import cache
from pathlib import Path

from django.conf import settings

_VERSION_FILE = Path(__file__).resolve().parents[2] / "src" / "g6" / "__init__.py"


@cache
def g6_version() -> str:
    """Read from the library's source, not imported: the site does not depend on the library being installed."""
    found = re.search(r'^__version__\s*=\s*"([^"]+)"', _VERSION_FILE.read_text(encoding="utf-8"), re.MULTILINE)
    return found.group(1) if found else ""


def site(request) -> dict[str, str | int]:
    return {"SITE_URL": settings.SITE_URL, "G6_VERSION": g6_version(), "current_year": date.today().year}
