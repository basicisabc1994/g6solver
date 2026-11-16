# SPDX-License-Identifier: Apache-2.0
"""G6's own catalogue is exempt from the sharing grant (the owner's decision 1, 2026-11-16; plan revision 8, A+E):
``g6_find``, ``g6_describe`` and ``g6_components`` answer a fresh client that has granted nothing for built-in
components, while an installed entry-point extension's text stays ``component_catalogue``: left out of a listing or a
ranking, and a description of it withheld, until the person grants that source. No test here uses the conftest's
``declared_mcp_grants``."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from mcp.types import Implementation  # noqa: E402

from g6.mcp import build_server  # noqa: E402

MARKER = "zqxprivateextension"


def _calls(calls: list[tuple[str, dict]], client: str = "codex") -> list:
    async def run() -> list:
        async with Client(build_server(), client_info=Implementation(name=client, version="test")) as session:
            return [str(await session.call_tool(name, arguments)) for name, arguments in calls]
    return asyncio.run(run())


def _grant(scope: str, provider: str = "openai:codex") -> None:
    from g6.mcp.cloud_consent import request_grant

    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action="accept", content={"value": "Fixture person"})
    assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id="grant"), scope,
                                     provider))["status"] == "granted"


@pytest.mark.parametrize("client", ["codex", "claude-code"])
def test_a_fresh_client_gets_the_built_in_catalogue(tmp_path, monkeypatch, client):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))
    found, described, listed = _calls([("g6_find", {"query": "check a claim against its sources"}),
                                       ("g6_describe", {"component": "grounding"}),
                                       ("g6_components", {})], client)
    for reply in (found, described, listed):
        assert "G6_E_CLOUD_CONSENT_REQUIRED" not in reply, reply[:400]
    assert "'component': 'grounding'" in found or '"component": "grounding"' in found
    assert "check_claim" in described
    assert "grounding" in listed and "epistemic_integrity" in listed


@pytest.fixture
def extension(tmp_path, monkeypatch, registry_reset):
    package = tmp_path / "ext" / MARKER
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'"""{MARKER} private extension text."""\n', encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "ext"))
    from g6.core import registry
    monkeypatch.setattr(registry.metadata, "entry_points",
                        lambda **kwargs: [SimpleNamespace(name=MARKER, value=MARKER)])
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return MARKER


@pytest.mark.parametrize("granted", [False, True])
def test_an_extensions_text_stays_behind_its_grant(extension, granted):
    if granted:
        _grant("component_catalogue")
    listed, found, described, built_in = _calls([("g6_components", {}), ("g6_find", {"query": extension}),
                                                 ("g6_describe", {"component": extension}),
                                                 ("g6_describe", {"component": "grounding"})])
    assert (extension in listed) is granted and "grounding" in listed
    assert (extension in found) is granted
    assert ("G6_E_CLOUD_CONSENT_REQUIRED" in described) is not granted, described[:400]
    assert (extension in described) is granted
    assert "G6_E_CLOUD_CONSENT_REQUIRED" not in built_in
    for reply in (listed, found):
        assert "G6_E_CLOUD_CONSENT_REQUIRED" not in reply


def test_a_granted_extension_is_recorded_as_a_disclosure(extension):
    from g6.core import cloud_consent as cloud

    _grant("component_catalogue")
    _calls([("g6_components", {})])
    assert any(row.get("provider") == "openai:codex" and "component_catalogue" in json.dumps(row)
               for row in cloud.disclosures())


def test_the_built_in_catalogue_tools_declare_no_retained_source():
    from g6.core.consent_declarations import TOOLS

    for name in ("g6_find", "g6_describe", "g6_components"):
        assert not [c for c in TOOLS[name].classes if c.startswith("source:")], name
        assert "g6_operational" in TOOLS[name].classes
