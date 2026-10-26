# SPDX-License-Identifier: Apache-2.0
"""Every shipped bundle has a smoke sample for the release build (``packaging/smoke_bundle.py``), and the
agent-change-check sample, a real git repository the script makes, gives a result with rows. Offline."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from g6.bundles import agent_change, catalog

_spec = importlib.util.spec_from_file_location(
    "smoke_bundle", Path(__file__).resolve().parents[2] / "packaging" / "smoke_bundle.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def test_every_shipped_bundle_has_a_smoke_sample():
    assert set(catalog()) == set(smoke.SAMPLES) | {"agent-change-check"}


def test_the_smoke_script_reads_the_token_from_the_link_serve_prints(monkeypatch, capsys):
    # the page no longer carries the token, so the release smoke test must take it from the printed link
    # (fails while smoke_bundle.py reads 'const TOKEN=' from the page instead of read_link)
    import pytest

    from g6.bundles import server

    class WindowClosed(Exception):
        pass

    opened = []

    def browser(link):
        opened.append(link)
        raise WindowClosed   # ends serve's wait

    monkeypatch.setattr(server.webbrowser, "open", browser)
    with pytest.raises(WindowClosed):
        server.serve(catalog()["safe-actions"])
    base, _, token = opened[0].partition("#token=")
    assert smoke.read_link(capsys.readouterr().out) == (base, token) and token
    assert smoke.read_link("no address here\n") is None


def test_the_agent_change_sample_is_a_real_repository_with_a_change(monkeypatch):
    monkeypatch.setattr(agent_change, "MUTATION_BUDGET_S", 30)
    result = agent_change.run(smoke.agent_change_sample())
    assert result.rows and result.completion_state == "qualified-draft", result.rows
