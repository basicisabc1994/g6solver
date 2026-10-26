# SPDX-License-Identifier: Apache-2.0
"""The workforce tests' shared settings: ``data/`` holds repositories the tests copy (never collected), and
``confined_bench``."""
import pytest

collect_ignore_glob = ["data/*"]


@pytest.fixture
def confined_bench(confined_python, monkeypatch):
    """The bench's test runners started with ``confined_python`` (``tests/conftest.py``), so they run confined by the
    operating system (``g6.core.confine``), or a skip that says why: the workforce verifies only a confined run."""
    from g6.core import confine
    from g6.workforce import repo_tests, truth

    name, why = confine.backend(confined_python)
    if name is None:
        pytest.skip(f"the Windows backend cannot run here: {why}")
    monkeypatch.setattr(truth, "_python", lambda: confined_python)
    monkeypatch.setattr(repo_tests, "interpreter", lambda repo: confined_python)
    return confined_python