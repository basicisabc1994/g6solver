# SPDX-License-Identifier: Apache-2.0
"""The stdio server's file tools: refused unless the person who runs the server allows them, and confined to the state
directory that person declares.

Before: ``load_dataset``, ``save_state`` and ``load_state`` read and wrote any path the agent gave, and with
``ABSTRACTION_MCP_STATE`` set the server loaded and saved that file wherever it pointed. Now each file tool runs only
when ``G6_MCP_SENSITIVE`` lists ``abstraction`` or ``abstraction.<tool>``, as g6-mcp's sensitive ops do, and every path
(the agent's and ``ABSTRACTION_MCP_STATE``) must resolve inside ``ABSTRACTION_MCP_DIR``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from g6.abstraction import server as S
from g6.abstraction.library import Library

DENIED = "G6_E_SENSITIVE_COMPONENT_DENIED"
REFUSED = "G6_E_ABSTRACTION_PATH_REFUSED"
CSV = "x,score\n" + "".join(f"{i / 9},{-(i / 9 - 0.3) ** 2}\n" for i in range(10))


def call(fn, **kw):
    return json.loads(getattr(fn, "fn", fn)(**kw))


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    S._domains.clear()
    S._library = Library(k_permutations=4)
    for var in ("G6_MCP_SENSITIVE", "ABSTRACTION_MCP_DIR", "ABSTRACTION_MCP_STATE"):
        monkeypatch.delenv(var, raising=False)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "trace.csv").write_text(CSV)
    return state_dir


def _refused(reply: dict, code: str) -> None:
    assert reply.get("code") == code and reply.get("completion_state") == "blocked-escalated", reply
    assert reply["error"].startswith(f"[{code}]")


def test_the_file_tools_are_refused_unless_the_person_running_the_server_allows_them(
        monkeypatch: pytest.MonkeyPatch, clean: Path) -> None:
    monkeypatch.setenv("ABSTRACTION_MCP_DIR", str(clean))
    _refused(call(S.load_dataset, path="trace.csv", objective="score"), DENIED)
    _refused(call(S.save_state, path="saved.json"), DENIED)
    _refused(call(S.load_state, path="saved.json"), DENIED)
    assert not (clean / "saved.json").exists() and S._domains == {}


def test_an_allowed_file_tool_still_needs_a_declared_state_directory(monkeypatch: pytest.MonkeyPatch,
                                                                     clean: Path) -> None:
    monkeypatch.setenv("G6_MCP_SENSITIVE", "abstraction")
    _refused(call(S.load_dataset, path=str(clean / "trace.csv"), objective="score"), REFUSED)
    _refused(call(S.save_state, path=str(clean / "saved.json")), REFUSED)
    assert not (clean / "saved.json").exists()


def test_a_path_outside_the_state_directory_is_refused(monkeypatch: pytest.MonkeyPatch, clean: Path,
                                                       tmp_path: Path) -> None:
    monkeypatch.setenv("G6_MCP_SENSITIVE", "abstraction")
    monkeypatch.setenv("ABSTRACTION_MCP_DIR", str(clean))
    (tmp_path / "outside.csv").write_text(CSV)
    for path in (str(tmp_path / "outside.csv"), "../outside.csv"):
        _refused(call(S.load_dataset, path=path, objective="score"), REFUSED)
    for path in (str(tmp_path / "escaped.json"), "../escaped.json"):
        _refused(call(S.save_state, path=path), REFUSED)
    assert not (tmp_path / "escaped.json").exists() and S._domains == {}


def test_survival_inside_the_directory_with_the_tools_allowed_they_work(monkeypatch: pytest.MonkeyPatch,
                                                                        clean: Path) -> None:
    monkeypatch.setenv("G6_MCP_SENSITIVE", "abstraction")
    monkeypatch.setenv("ABSTRACTION_MCP_DIR", str(clean))
    loaded = call(S.load_dataset, path="trace.csv", objective="score", name="toy")
    assert loaded["loaded"] == "toy"
    assert call(S.save_state, path="saved.json")["n_domains"] == 1 and (clean / "saved.json").exists()
    S._domains.clear()
    assert call(S.load_state, path=str(clean / "saved.json"))["domains"] == ["toy"]


def test_one_op_can_be_allowed_without_the_others(monkeypatch: pytest.MonkeyPatch, clean: Path) -> None:
    monkeypatch.setenv("G6_MCP_SENSITIVE", "abstraction.save_state")
    monkeypatch.setenv("ABSTRACTION_MCP_DIR", str(clean))
    assert "error" not in call(S.save_state, path="saved.json")
    _refused(call(S.load_dataset, path="trace.csv", objective="score"), DENIED)
    _refused(call(S.load_state, path="saved.json"), DENIED)


def test_the_server_reads_g6_mcp_sensitive_exactly_as_g6_mcp_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """A component may not import g6.mcp (the MCP surface sits above the components, and g6.mcp imports the
    meta-harness), so the server parses the setting itself; this keeps the two parsers in step."""
    import ast

    import g6.mcp

    assert S.SENSITIVE_ENV == g6.mcp.SENSITIVE_ENV
    for setting in ("", "abstraction", " abstraction.save_state , lean ", "*", ",,", "abstraction,,abstraction"):
        monkeypatch.setenv("G6_MCP_SENSITIVE", setting)
        assert S._allowed_sensitive() == g6.mcp.allowed_sensitive(), setting
    tree = ast.parse(Path(S.__file__).read_text(encoding="utf-8"))
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(m and (m == "g6.mcp" or m.startswith("g6.mcp.")) for m in imported), imported


def test_the_autosave_file_must_lie_in_the_state_directory(monkeypatch: pytest.MonkeyPatch, clean: Path,
                                                           tmp_path: Path) -> None:
    monkeypatch.setenv("ABSTRACTION_MCP_STATE", str(tmp_path / "anywhere.json"))
    with pytest.raises(ValueError, match=REFUSED):
        S._autoload()   # no state directory declared: the server does not start
    monkeypatch.setenv("ABSTRACTION_MCP_DIR", str(clean))
    with pytest.raises(ValueError, match=REFUSED):
        S._autoload()   # declared, but the state file is outside it
    monkeypatch.setenv("ABSTRACTION_MCP_STATE", "auto.json")
    S._autoload()       # survival: inside the directory, nothing to load yet
    S._domains["toy"] = S.ConcreteDomain.from_csv(str(clean / "trace.csv"), "score", name="toy")
    S._autosave()
    assert (clean / "auto.json").exists() and not (tmp_path / "anywhere.json").exists()
