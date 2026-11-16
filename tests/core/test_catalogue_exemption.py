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
    listed, found, described, built_in, absent = _calls([
        ("g6_components", {}), ("g6_find", {"query": extension}), ("g6_describe", {"component": extension}),
        ("g6_describe", {"component": "grounding"}), ("g6_describe", {"component": "zqxnosuchcomponent"})])
    assert (extension in listed) is granted and "grounding" in listed
    assert (extension in found) is granted
    assert (extension in described) is granted and "G6_E_CLOUD_CONSENT_REQUIRED" not in described, described[:400]
    if not granted:     # no existence oracle: an installed extension and a name nothing provides get one reply
        assert described == absent and "'available': False" in described, (described[:300], absent[:300])
    assert "G6_E_CLOUD_CONSENT_REQUIRED" not in built_in
    for reply in (listed, found):
        assert "G6_E_CLOUD_CONSENT_REQUIRED" not in reply


def test_a_granted_extension_is_recorded_as_a_disclosure(extension):
    from g6.core import cloud_consent as cloud

    _grant("component_catalogue")
    _calls([("g6_components", {})])
    assert any(row.get("provider") == "openai:codex" and "component_catalogue" in json.dumps(row)
               for row in cloud.disclosures())


DROPIN = "zqxdropinpackage"


@pytest.fixture
def dropin(tmp_path, monkeypatch, registry_reset):
    """A package put into G6's own namespace (as a third-party wheel shipping g6/<name>/ would be), simulated by a
    folder added to g6.__path__; nothing is installed."""
    import g6

    folder = tmp_path / "ns"
    (folder / DROPIN).mkdir(parents=True)
    (folder / DROPIN / "__init__.py").write_text(f'"""{DROPIN} third-party private text."""\n', encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(folder)])
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return DROPIN


@pytest.mark.parametrize("granted", [False, True])
def test_a_package_dropped_into_g6s_namespace_is_an_extension(dropin, granted):
    from g6.core.registry import get_registry
    from g6.mcp import catalogue

    assert dropin in get_registry().names()                    # the registry finds it under g6.__path__ ...
    assert not catalogue.built_in(dropin) and catalogue.extension(dropin)   # ... but G6 did not ship it
    if granted:
        _grant("component_catalogue")
    listed, found = _calls([("g6_components", {}), ("g6_find", {"query": f"{dropin} third-party private"})])
    assert (dropin in listed) is granted and (dropin in found) is granted


def test_the_shipped_list_is_every_built_in_component_g6_ships():
    from g6.core.registry import get_registry
    from g6.mcp import catalogue

    assert catalogue.shipped() == {name: ref.module for name, ref in get_registry().refs.items()
                                   if ref.source == "builtin"}, "run: python -P -m g6.mcp.catalogue --write"
    assert catalogue.PATH.read_text(encoding="utf-8") == catalogue.dumps(catalogue.build())
    assert all(catalogue.built_in(name) for name in catalogue.shipped())


def test_the_built_in_catalogue_tools_declare_no_retained_source():
    from g6.core.consent_declarations import TOOLS

    for name in ("g6_find", "g6_describe", "g6_components"):
        assert not [c for c in TOOLS[name].classes if c.startswith("source:")], name
        assert "g6_operational" in TOOLS[name].classes
