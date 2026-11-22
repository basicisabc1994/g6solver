# SPDX-License-Identifier: Apache-2.0
"""Install guidance through doctor's real checks, without uninstalling dependencies."""
from __future__ import annotations

import importlib.abc
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cli import doctor
from g6.core import confine

ROOT = Path(__file__).resolve().parents[2]
#: G6 is installed from a copy of its repository, never by name (G6 is not on PyPI).
CLONE = "git clone https://github.com/basicisabc1994/g6solver"


class Missing(importlib.abc.MetaPathFinder):
    def __init__(self, name):
        self.name = name

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name:
            raise ModuleNotFoundError(fullname)


@pytest.mark.parametrize(("module", "extra", "capability"), [
    ("fastmcp", "mcp", "MCP server"), ("mcp", "mcp", "MCP server"),
    ("anthropic", "anthropic", "Anthropic API"),
    ("z3", "formal", "Z3"), ("ortools", "optim", "OR-Tools"),
])
def test_missing_dependency_names_blocked_capability_and_command(monkeypatch, module, extra, capability):
    monkeypatch.delitem(sys.modules, module, raising=False)
    monkeypatch.setattr(sys, "meta_path", [Missing(module), *sys.meta_path])
    checks = doctor._extras()
    check = next(c for c in checks if c.name == f"extra: {extra}")
    assert check.status == "warn"
    assert module in check.detail and capability in check.detail
    assert "unavailable" in check.detail
    assert check.fix == f'Inside a copy of G6 ({CLONE}): python -m pip install ".[{extra}]"'
    assert check.fix in doctor.render([check])


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_doctor_and_install_page_agree(monkeypatch, platform):
    monkeypatch.setattr(doctor.sys, "platform", platform)
    page = (ROOT / "site/docs/install.md").read_text(encoding="utf-8")
    check = doctor._platform()
    assert check.detail in page
    for extra, (_, _, command) in doctor.EXTRAS.items():
        assert command == f'python -m pip install ".[{extra}]"' and command in page
    assert CLONE in page
    # The published Get started page may hold no sign-in or log-in text (web/pages/guard.py, tests/site/test_honesty.py),
    # so the page sends the reader to doctor for the client's own command, and
    # test_unconfigured_model_prints_sign_in_guidance holds that doctor prints both commands.
    assert "g6 doctor" in page and "with the command doctor prints" in page


def test_unconfigured_model_prints_sign_in_guidance():
    text = doctor.render([doctor._model({}, {})])
    assert "Claude Code" in text and "Codex" in text
    assert "claude auth login" in text and "codex login" in text
    assert "not contacted" in text


def test_located_host_program_does_not_assert_sign_in(monkeypatch):
    from g6.core import host_models

    class Caller:
        program = SimpleNamespace(name="Claude Code")

        def locate(self):
            return "fixture-claude"

    monkeypatch.setitem(host_models.CALLERS, "claude_code", Caller)
    check = doctor._model({"G6_LLM_PROVIDER": "claude_code"}, {"G6_LLM_PROVIDER": "claude_code"})
    assert check.status == "ok"
    assert "sign-in status is not checked" in check.detail
    assert "claude auth login" in check.fix


def test_selected_anthropic_model_handles_a_blocked_sdk_import(monkeypatch):
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setattr(sys, "meta_path", [Missing("anthropic"), *sys.meta_path])
    check = doctor._model({"G6_LLM_PROVIDER": "anthropic"}, {})
    assert check.status == "fail" and "SDK is missing" in check.detail
    assert check.fix == f'Inside a copy of G6 ({CLONE}): python -m pip install ".[anthropic]"'


def test_windows_boundary_fix_and_documented_template_agree(monkeypatch):
    command = confine._grant_command("<python-folder>")
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.delenv("G6_PYTHON", raising=False)
    monkeypatch.setattr(confine, "backend", lambda *a, **k: (None, "unavailable: run once in a terminal: " + command))
    monkeypatch.setattr(confine, "required", lambda: True)
    check = doctor._isolation()
    assert command in check.fix
    assert command in (ROOT / "site/docs/install.md").read_text(encoding="utf-8")


def test_public_get_started_uses_the_same_install_guidance():
    def guidance(path):
        return path.read_text(encoding="utf-8").split("## Install for a coding agent or Python\n", 1)[1].split(
            "## 1. Choose a worked example", 1)[0]

    assert guidance(ROOT / "web/content/install.md") == guidance(ROOT / "site/docs/install.md")


def test_missing_linux_boundary_names_patch_review_and_exact_fix(monkeypatch):
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    monkeypatch.delenv("G6_PYTHON", raising=False)
    monkeypatch.setattr(confine, "backend", lambda *a, **k: (None, confine.bwrap_reason({"program": None})))
    monkeypatch.setattr(confine, "required", lambda: True)
    text = doctor.render([doctor._isolation()])
    assert "patch review" in text
    assert "sudo apt-get install bubblewrap" in text
