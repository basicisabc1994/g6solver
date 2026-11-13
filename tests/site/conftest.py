# SPDX-License-Identifier: Apache-2.0
"""The website in ``oss/web`` (a Django project built to static files). Skipped where Django is not installed."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

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


@pytest.fixture(scope="session")
def honesty():
    """The copy check (``test_honesty.py``) as a module, loaded once for every review folder's tests."""
    path = Path(__file__).with_name("test_honesty.py")
    spec = importlib.util.spec_from_file_location("site_honesty", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scratch():
    """A folder of the test's own inside the repository (``.build/<uuid>``, made with its parents) and removed after
    the test: for the tests whose documents and templates must sit under the repository's root. A checkout carries
    no such folder, so no test may count on one being there."""
    folder = Path(__file__).resolve().parents[2] / ".build" / uuid4().hex
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def with_planned(monkeypatch):
    """The site with its one switch on (``pages.extensions.SHOW_PLANNED``): every planned row listed, as before the
    owner's ruling of 2026-10-23 that the site shows only what is in the library."""
    from pages import extensions

    monkeypatch.setattr(extensions, "SHOW_PLANNED", True)
    return True
