# SPDX-License-Identifier: Apache-2.0
"""Run writers must reject linked paths before following or changing them."""
import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path

import pytest

from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._envelope import WorkspaceRefused


def _snapshot(folder):
    return {str(path.relative_to(folder)): path.read_bytes() if path.is_file() else None
            for path in folder.rglob("*")}


@pytest.mark.parametrize("writer", ["step", "advance", "prepare", "settle", "save", "standalone_save", "note_model_call"])
@pytest.mark.parametrize("component", ["run", "parent"])
@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_run_writers_refuse_linked_components(tmp_path, monkeypatch, writer, component, link_kind):
    if link_kind == "junction" and os.name != "nt":
        pytest.skip("junctions require Windows")
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    client = "path-gate"
    name = hashlib.sha256(f"mcp:{client}".encode()).hexdigest()[:32]
    root = home / "completion_loop"
    run, target = root / name, outside / name
    row = json.dumps({"session": name, "cause": "still-owed"})
    state = {"attempted": 1, "done": True, "row_due": row, "row": "keep retry"}
    loop._save(target, dict(state), sources=())
    # Keep marker creation out of the observed operation's effects.
    marks = (root if component == "run" else outside) / "owing"
    marks.mkdir(parents=True)
    (marks / name).touch()
    link, destination = (run, target) if component == "run" else (root, outside)
    before = _snapshot(outside)
    with ExitStack() as stack:
        # Capture the plain absolute run before a link replaces its missing path.
        # The later send must still pass the gate, even with a pinned observer.
        if writer == "note_model_call":
            stack.enter_context(loop._run_count(run, dict(state), fresh=False))
        elif writer == "save":
            stack.enter_context(loop._writing(home / "plain-owner"))
        if link_kind == "junction":
            import _winapi
            _winapi.CreateJunction(str(destination), str(link))
        else:
            try:
                link.symlink_to(destination, target_is_directory=True)
            except OSError as error:
                if getattr(error, "winerror", None) != 1314:
                    raise
                # This Windows account may not create symlinks; Linux CI runs these cases.
                pytest.skip("symlinks need a privilege this account does not hold")
        try:
            if writer == "step":
                result = review_loop.step(str(tmp_path), "task", client)
                assert result["code"] == "G6_E_WORKSPACE_OUTSIDE_ROOT"
            elif writer == "advance":
                result = loop.advance(tmp_path, "task", "HEAD", run)
                assert result.code == "G6_E_WORKSPACE_OUTSIDE_ROOT"
            elif writer == "settle":
                loop.settle()
                assert (marks / name).exists()
                with pytest.raises(WorkspaceRefused) as refused:
                    with loop._writing(run):
                        pass
                assert refused.value.code == "G6_E_WORKSPACE_OUTSIDE_ROOT"
            else:
                with pytest.raises(WorkspaceRefused) as refused:
                    if writer == "prepare":
                        monkeypatch.setattr(loop, "_before_fix", lambda: True)
                        monkeypatch.setattr(loop, "critic_host", lambda: None)
                        loop.prepare(tmp_path, "task", "HEAD", run)
                    elif writer == "note_model_call":
                        note_model_call()
                    else:
                        loop._save(run, dict(state), sources=())
                assert refused.value.code == "G6_E_WORKSPACE_OUTSIDE_ROOT"
            assert _snapshot(outside) == before
        finally:
            if link.is_symlink():
                link.unlink()
            else:
                os.rmdir(link)


def test_plain_relative_run_stays_pinned_after_cwd_change(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "completion_loop"
    root.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.chdir(root)
    with loop._writing(Path("run")) as owner:
        with loop._run_count(owner.session, {"attempted": 0}, fresh=False):
            monkeypatch.chdir(elsewhere)
            note_model_call()
    assert loop._load(root / "run")["attempted"] == 1
    assert list(elsewhere.iterdir()) == []
