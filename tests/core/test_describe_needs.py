# SPDX-License-Identifier: Apache-2.0
"""``g6_describe`` serves what a component needs from outside (FW-05): ``__g6_outside__`` (programs the person installs)
and ``__g6_extras__`` (each extra and the imports it provides, with whether they are installed), running nothing."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import g6
from g6.core import discovery, registry
from g6.mcp import describe_reply

MEMBER = '''"""The tool_user test member."""
from g6.core.keys import Key
from g6.core.outside import OutsideProgram

__g6_keys__ = (Key("TOOL_API_KEY", "the tool's paid service", "https://example.invalid/keys", paid=True),)

__g6_outside__ = (OutsideProgram(name="Tool", purpose="runs the tool", install="pip install tool", licence="MIT",
                                 command="g6-no-such-program", env="G6_TOOL"),)
__g6_extras__ = {"db": ("json",), "media": ("g6_no_such_module",)}
'''


@pytest.fixture
def members(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_reset) -> Path:
    fam = tmp_path / "g6" / "needstest"
    for name, text in (("tool_user", MEMBER), ("plain", '"""The plain test member."""\n')):
        (fam / name).mkdir(parents=True)
        (fam / name / "__init__.py").write_text(text, encoding="utf-8")
    (fam / "__init__.py").write_text('__g6_family__ = "needstest"\n', encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(tmp_path / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {"needstest"})
    importlib.invalidate_caches()
    yield fam
    for name in [m for m in sys.modules if m == "g6.needstest" or m.startswith("g6.needstest.")]:
        del sys.modules[name]


def test_describe_lists_outside_programs_and_extras(members: Path) -> None:
    info = discovery.describe("tool_user")
    assert info["outside"] == [{"name": "Tool", "purpose": "runs the tool", "install": "pip install tool",
                                "licence": "MIT", "command": "g6-no-such-program", "url": "", "env": "G6_TOOL",
                                "paid": False, "online": False}]
    assert info["extras"] == [{"extra": "db", "provides": ["json"], "installed": True},
                              {"extra": "media", "provides": ["g6_no_such_module"], "installed": False}]


def test_describe_lists_the_keys_it_needs_and_the_file_they_go_in(members: Path) -> None:
    # owner ruling 2026-10-21: the person gives a key by writing it in their key file, so describe says which and where
    from g6.core import keys

    assert discovery.describe("tool_user")["keys"] == [
        {"name": "TOOL_API_KEY", "purpose": "the tool's paid service", "where": "https://example.invalid/keys",
         "paid": True, "file": str(keys.path())}]


def test_the_mcp_reply_carries_them(members: Path) -> None:
    reply = describe_reply("tool_user")
    assert reply["outside"][0]["name"] == "Tool" and reply["keys"][0]["name"] == "TOOL_API_KEY"
    assert [e["extra"] for e in reply["extras"]] == ["db", "media"]


def test_a_component_that_needs_nothing_says_so(members: Path) -> None:
    info = discovery.describe("plain")
    assert (info["outside"], info["extras"], info["keys"]) == ([], [], [])
