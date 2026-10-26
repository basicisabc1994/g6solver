# SPDX-License-Identifier: Apache-2.0
"""The website in ``oss/web`` (a Django project built to static files). Skipped where Django is not installed."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

if importlib.util.find_spec("django") is None:
    # Not collected at all: pytest.importorskip here stops the whole run when this folder is named on the command line
    collect_ignore_glob = ["*"]
else:
    WEB = Path(__file__).resolve().parents[2] / "web"
    if str(WEB) not in sys.path:
        sys.path.insert(0, str(WEB))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "g6site.settings")

    import django

    django.setup()


@pytest.fixture
def client():
    from django.test import Client

    return Client()


@pytest.fixture
def with_planned(monkeypatch):
    """The site with its one switch on (``pages.extensions.SHOW_PLANNED``): every planned row listed, as before the
    owner's ruling of 2026-10-23 that the site shows only what is in the library."""
    from pages import extensions

    monkeypatch.setattr(extensions, "SHOW_PLANNED", True)
    return True
