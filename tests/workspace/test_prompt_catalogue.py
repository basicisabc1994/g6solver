# SPDX-License-Identifier: Apache-2.0
"""The one prompt catalogue (P1, PRs #4 and #6): every entry is tied to a mode and a pipeline step, none decides a
gate, rendering is the library's strict one-pass fill, and installing it is a sensitive op that never takes a name a
person already uses."""
from __future__ import annotations

import pytest

from g6.mcp import invoke_reply
from g6.workspace import _envelope as E
from g6.workspace.align_prompt_library import catalogue, library
from g6.workspace.align_prompt_library.library import Library, digest_of

WorkspaceRefused = E.WorkspaceRefused


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    return tmp_path


def test_every_entry_is_tied_to_a_mode_and_step_and_none_decides_a_gate():
    assert len(catalogue.ENTRIES) == 14 and len({e.name for e in catalogue.ENTRIES}) == 14
    for e in catalogue.ENTRIES:
        assert e.mode in catalogue.MODES and e.step in catalogue.STEPS and e.step != "gate"
        assert "$" not in e.template and e.digest == digest_of(e.template)
    assert catalogue.entries(mode="mission", step="approve") == (catalogue.entry("cb-user-facing"),)


def test_render_is_the_librarys_strict_one_pass_fill(monkeypatch):
    with pytest.raises(WorkspaceRefused) as missing:
        catalogue.render("cb-critique", {"candidate": "x"})
    assert missing.value.code == E.LIBRARY_MISSING_VALUE
    with pytest.raises(WorkspaceRefused) as extra:
        catalogue.render("cb-critique", {"candidate": "x", "constraints": "y", "colour": "z"})
    assert extra.value.code == E.LIBRARY_EXTRA_VALUE
    got = catalogue.render("cb-critique", {"candidate": "{{constraints}}", "constraints": "c"})
    assert "{{constraints}}" in got["text"] and got["template_digest"] == catalogue.entry("cb-critique").digest
    assert got["rendered_digest"] == digest_of(got["text"])
    calls = []
    real = library.fill
    monkeypatch.setattr(library, "fill", lambda *a, **k: calls.append(k.get("label")) or real(*a, **k))
    catalogue.render("cb-critique", {"candidate": "x", "constraints": "y"})
    lib = Library(None)
    lib.put("prompt", "t", "Hello {{who}}")
    lib.render("t", values={"who": "you"})
    assert len(calls) == 2


def test_an_unknown_entry_is_refused():
    with pytest.raises(WorkspaceRefused) as unknown:
        catalogue.entry("cb-nothing")
    assert unknown.value.code == E.LIBRARY_NOT_FOUND


def test_install_is_sensitive_over_mcp_and_writes_nothing_when_refused(home):
    reply = invoke_reply("align_prompt_library", "install_catalog", "{}")
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and not Library().path.exists()


def test_install_when_allowed_stores_each_entry_once_and_never_takes_a_users_name(home):
    Library().put("prompt", "cb-critique", "my own critique prompt")
    allow = frozenset({"align_prompt_library.install_catalog"})
    first = invoke_reply("align_prompt_library", "install_catalog", "{}", allow_sensitive=allow)["result"]
    assert first["warning_card"]["code"] == "G6_E_LIBRARY_INSTALL_PARTIAL"
    assert [r["name"] for r in first["refused"]] == ["cb-critique"] and first["refused"][0]["code"] == "G6_E_LIBRARY_NAME_TAKEN"
    assert Library().get("cb-critique")["content"] == "my own critique prompt"
    again = invoke_reply("align_prompt_library", "install_catalog", "{}", allow_sensitive=allow)["result"]
    assert all(r["unchanged"] for r in again["stored"])


def test_the_catalogue_op_lists_entries_by_mode_and_step():
    reply = invoke_reply("align_prompt_library", "catalogue", '{"mode": "premortem", "step": "test"}')
    assert reply["completion_state"] == "qualified-draft" and reply["warning_card"]["code"] == "G6_E_LIBRARY_CATALOGUE"
    assert [e["name"] for e in reply["result"]["entries"]] == ["cb-search-query"]
