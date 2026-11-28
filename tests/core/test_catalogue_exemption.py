# SPDX-License-Identifier: Apache-2.0
"""G6's own catalogue is exempt from the sharing grant (the owner's decision 1, 2026-11-16; plan revision 8, A+E):
``g6_find``, ``g6_describe`` and ``g6_components`` answer a fresh client that has granted nothing for built-in
components, while an installed entry-point extension's text stays ``component_catalogue``: left out of a listing or a
ranking, and a description of it withheld, until the person grants that source. No test here uses the conftest's
``declared_mcp_grants``."""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from mcp.types import Implementation  # noqa: E402

from g6.mcp import build_server  # noqa: E402

MARKER = "zqxprivateextension"


def _calls(calls: list[tuple[str, dict]], client: str | None = "codex") -> list:
    """Each call's reply as text; ``client`` None leaves FastMCP's own client name, which G6 does not know."""
    async def run() -> list:
        named = {"client_info": Implementation(name=client, version="test")} if client is not None else {}
        async with Client(build_server(), **named) as session:
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


UNNAMED = "some-unnamed-client"


@pytest.mark.parametrize("client", [None, UNNAMED])
def test_a_client_g6_cannot_name_gets_the_built_in_catalogue_and_the_selector(tmp_path, monkeypatch, client):
    """A client whose name G6 does not know (``cloud:unknown``) can grant nothing, so a reply that held only its own
    request was withheld with no way on, the selector's among them; G6's catalogue and selector are its own text
    (the owner's decision 1, 2026-11-16) and now answer it, built-in components only."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))
    selected, found, described, listed, helped = _calls([
        ("g6_select", {"task": "review my patch before I say it is done", "phase": "verify"}),
        ("g6_find", {"query": "check a claim against its sources"}), ("g6_describe", {"component": "grounding"}),
        ("g6_components", {}), ("g6_help", {})], client)
    for reply in (found, described, listed, helped):
        assert "G6_E_CLOUD_CONSENT_REQUIRED" not in reply, reply[:400]
    # the selector's reply names the consent code among its remedies; withheld, it would carry cloud_consent_code
    assert "cloud_consent_code" not in selected and "'candidates'" in selected, selected[:400]
    assert "g6_review_patch" in selected
    assert "'component': 'grounding'" in found and "check_claim" in described, (found[:300], described[:300])
    assert "grounding" in listed and "g6_select" in helped
    assert "host_facts" in described                            # this computer's facts stay out


def test_a_client_g6_cannot_name_gets_no_extension_text(extension):
    listed, found, described = _calls([("g6_components", {}), ("g6_find", {"query": extension}),
                                       ("g6_describe", {"component": extension})], UNNAMED)
    assert extension not in listed and extension not in found and extension not in described, described[:300]
    assert "G6_E_CLOUD_CONSENT_REQUIRED" not in listed + found + described


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


HOMEMARK = "zqxpersonhomemarker"


@pytest.mark.parametrize("granted", [False, True])
def test_a_built_in_description_holds_this_computers_facts_only_with_host_configuration(tmp_path, monkeypatch,
                                                                                         registry_reset, granted):
    monkeypatch.setenv("G6_HOME", str(tmp_path / HOMEMARK / ".g6"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "lean")
    if granted:
        _grant("host_configuration")
    keyed, extras, lean, outside = _calls([("g6_describe", {"component": "ctx_search"}),
                                           ("g6_describe", {"component": "grounding"}),
                                           ("g6_describe", {"component": "lean"}),
                                           ("g6_describe", {"component": "workforce"})])
    for reply in (keyed, extras, lean, outside):
        assert "G6_E_CLOUD_CONSENT_REQUIRED" not in reply and "'available': True" in reply, reply[:300]
    assert "'keys': [{" in keyed and "'extras': [{" in extras and "check_proof" in lean   # the catalogue text stays
    assert "'name': 'pytest'" in outside and "'command': 'git'" in outside   # a declared program's bare name stays
    assert (HOMEMARK in keyed) is granted                                # the key file's path
    assert ("'installed'" in extras) is granted                          # which extras this computer has
    assert ("sensitive_ops_allowed_here" in lean) is granted             # the server's own setting
    assert _holds(outside, sys.executable) is granted                   # the Python found running G6
    if granted:
        from g6.core import cloud_consent as cloud
        assert any(row.get("provider") == "openai:codex" and "host_configuration" in json.dumps(row)
                   for row in cloud.disclosures())


def _holds(reply: str, value: str) -> bool:
    """Whether ``value`` is in a tool reply's text, as written or as Python's repr writes a backslash."""
    return bool(value) and (value in reply or value.replace("\\", "\\\\") in reply)


def test_a_fresh_describe_of_every_shipped_component_holds_nothing_of_this_computer(tmp_path, monkeypatch,
                                                                                      registry_reset):
    import getpass
    import os
    import re
    import tempfile

    from g6.mcp import catalogue

    monkeypatch.setenv("G6_HOME", str(tmp_path / HOMEMARK / ".g6"))
    # a temporary folder only this run has: on Linux the system's is /tmp, a word G6's own text uses (agent_codex
    # says its sandbox writes no /tmp), so a leak of the folder is found by its own name, never by a shared word
    (tmp_path / f"{HOMEMARK}-tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / f"{HOMEMARK}-tmp"))
    names = sorted(catalogue.shipped())
    replies = _calls([("g6_describe", {"component": name}) for name in names])
    here = {sys.executable, sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.expanduser("~"), os.getcwd(),
            tempfile.gettempdir()}
    here |= {value.replace("\\", "/") for value in here} | {HOMEMARK}
    user = getpass.getuser()
    for name, reply in zip(names, replies):
        assert "'available': True" in reply and "host_facts" in reply, (name, reply[:300])
        assert not [value for value in here if _holds(reply, value)], (name, [v for v in here if _holds(reply, v)])
        # a Windows path as this computer writes one (a skill's own examples, such as C:/work/project, are G6's text)
        assert not re.search(r"(?<![\w])[A-Za-z]:\\", reply), (name, "a drive path")
        assert not re.search(r"(?<![\w.~])/(?:home|Users|root|tmp|var|opt|usr|private|nix|Library)/", reply), name
        assert not re.search(r"[\\/]" + re.escape(user) + r"(?!\w)", reply), (name, "the user name")


def test_a_fresh_describe_of_a_built_in_that_fails_to_import_answers_in_g6s_words(tmp_path, monkeypatch,
                                                                                  registry_reset):
    from g6.core import registry

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    real = registry.ComponentRegistry.get_module

    def broken(self, name):
        if name == "grounding":
            # a home folder as Windows writes one, built from its parts so the public scan reads no user path here
            where = "\\".join(("C:", "Users", HOMEMARK, "site-packages", "y.py"))
            raise ImportError(f"cannot import name 'x' from 'y' ({where})")
        return real(self, name)
    monkeypatch.setattr(registry.ComponentRegistry, "get_module", broken)
    described, other = _calls([("g6_describe", {"component": "grounding"}), ("g6_describe", {"component": "lean"})])
    assert HOMEMARK not in described and "site-packages" not in described, described[:400]
    assert "'available': False" in described and "grounding" in described, described[:400]
    assert "'available': True" in other                                  # the next call is answered as before


def test_a_fresh_describe_runs_no_program(tmp_path, monkeypatch, registry_reset):
    """A review signature is checked by running ssh-keygen (``component_certificate.signed_review``), which a fresh
    describe reaches through ``vetting.summary``. It cannot run there: every package G6 ships declares no signed
    review, so the check returns before its program. If a shipped package ever declares one, this test fails, and the
    check must first be made unreachable for a client that has granted nothing."""
    import subprocess

    from g6.core import component_process, vetting
    from g6.core.discovery import _package
    from g6.mcp import catalogue

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    names = sorted(catalogue.shipped())
    declared = {name: vetting.declared(_package(name)) for name in names}
    assert not [name for name, value in declared.items() if isinstance(value, dict) and value.get("signed_review")]

    def no_program(*args, **kwargs):
        raise AssertionError(f"a fresh describe started a program: {args[:1]}")
    monkeypatch.setattr(subprocess, "Popen", no_program)
    monkeypatch.setattr(component_process, "run_contained", no_program)
    replies = _calls([("g6_describe", {"component": name}) for name in names])
    assert all("'available': True" in reply for reply in replies)
    assert all("'signed_review': True" not in reply for reply in replies)


def test_outside_an_mcp_call_a_description_keeps_this_computers_facts(tmp_path, monkeypatch, registry_reset):
    from g6.mcp import describe_reply

    monkeypatch.setenv("G6_HOME", str(tmp_path / HOMEMARK / ".g6"))
    info = describe_reply("ctx_search")
    assert HOMEMARK in json.dumps(info)


def test_the_built_in_catalogue_tools_declare_no_retained_source():
    from g6.core.consent_declarations import TOOLS

    for name in ("g6_find", "g6_describe", "g6_components"):
        assert not [c for c in TOOLS[name].classes if c.startswith("source:")], name
        assert "g6_operational" in TOOLS[name].classes
