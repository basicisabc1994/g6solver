# SPDX-License-Identifier: Apache-2.0
"""Every completion-owned mutation goes through the shared file gate."""
import ast
import os
from collections import Counter
from pathlib import Path

import pytest

from g6.harness import completion_hook as hook, completion_loop as loop
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused


@pytest.mark.parametrize("name", [
    ".write-version", "state.json", "state.sha256", "state.json.new", "state.sha256.new",
    "task.json", "task.sha256", "task.json.new", "task.sha256.new", "report.md",
    "rounds/round-1.patch", "sets/critic/test_critic_1.py", ".turn_ended", "repair_hints.json",
    ".step/mutex", ".prompt/mutex",
])
def test_internal_names_refuse_hard_links(tmp_path, monkeypatch, name):
    from g6.harness import _completion_storage as store

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    run = hook.folder("internal-files")
    linked = run / name
    linked.parent.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    os.link(victim, linked)
    try:
        with pytest.raises(WorkspaceRefused) as refused:
            store.write_bytes(linked, b"replace")
        assert refused.value.code == OUTSIDE_ROOT
        assert victim.read_bytes() == b"keep"
    finally:
        linked.unlink()


@pytest.mark.parametrize("name", ["state.json", "state.sha256", "state.json.new", "state.sha256.new"])
def test_save_preflights_all_retained_names_before_publication(tmp_path, monkeypatch, name):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    run = hook.folder("preflight")
    loop._save(run, {"attempted": 0}, sources=())
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    linked = run / name
    linked.unlink(missing_ok=True)
    os.link(victim, linked)
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    try:
        with pytest.raises(WorkspaceRefused) as refused:
            loop._save(run, {"attempted": 1}, sources=())
        assert refused.value.code == OUTSIDE_ROOT
        assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before
        assert victim.read_bytes() == b"keep"
    finally:
        linked.unlink()


def test_a_link_above_home_is_trusted_for_direct_writers(tmp_path, monkeypatch):
    real, alias = tmp_path / "profiles", tmp_path / "profile-alias"
    (real / "state").mkdir(parents=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(real), str(alias))
    else:
        alias.symlink_to(real, target_is_directory=True)
    try:
        monkeypatch.setenv("G6_HOME", str(alias / "state"))
        run = alias / "state" / "completion_loop" / "direct-run"
        loop._save(run, {"attempted": 0}, sources=())
        assert loop.attempted(loop._load(run)) == 0
        assert hook.folder("linked-profile").parent == (real / "state" / "completion_loop").resolve()
        loop.settle()
    finally:
        if alias.is_symlink():
            alias.unlink()
        else:
            os.rmdir(alias)


def test_completion_mutation_inventory_has_no_unguarded_writer():
    """A newly added direct writer fails this inventory, even for a new filename."""
    import g6.mcp.review_loop as mcp_loop

    mutations = {"write_bytes", "write_text", "write_retained", "mkdir", "unlink", "touch", "replace",
                 "rename", "rmtree", "copytree", "open", "mkdtemp", "makedirs", "remove", "rmdir",
                 "write", "writelines", "symlink_to", "hardlink_to", "link", "symlink", "mkfifo",
                 "NamedTemporaryFile", "TemporaryDirectory", "copy", "copy2", "copyfile"}
    found = set()

    def visit(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = f"{scope}.{node.name}" if scope else node.name
        if isinstance(node, ast.Call):
            call = ast.unparse(node.func)
            name = call.rsplit(".", 1)[-1]
            # str.replace takes two strings; Path.replace takes one destination.
            text_replace = name == "replace" and len(node.args) >= 2 and not call.startswith(("os.", "store."))
            if name in mutations and not text_replace and not call.startswith("store."):
                found.add((scope, call))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    for module in (hook, loop, mcp_loop):
        visit(ast.parse(Path(module.__file__).read_text(encoding="utf-8")))
    assert not found, found

    from g6.harness import _completion_storage as store

    # The adapter is an explicit inventory too: any new raw writer there needs
    # an audited gate and an inventory update, rather than inheriting an exemption.
    found = []

    def adapter(node, scope=""):
        if isinstance(node, ast.FunctionDef):
            scope = node.name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in mutations:
                found.append((scope, ast.unparse(node.func)))
        for child in ast.iter_child_nodes(node):
            adapter(child, scope)

    adapter(ast.parse(Path(store.__file__).read_text(encoding="utf-8")))
    assert Counter(found) == Counter([
        ("mkdir", "path(folder).mkdir"), ("write_bytes", "path(target).write_bytes"),
        ("write_text", "path(target).write_text"), ("unlink", "path(target).unlink"),
        ("touch", "path(target).touch"), ("write_retained", "cloud.write_retained"),
        ("sync_state", "path(session, name).open"), ("sync_state", "os.open"),
        ("remove_tree", "shutil.rmtree"), ("append_text", "os.open"),
        ("append_text", "out.write"), ("replace", "os.replace"),
        ("_copy_tree", "shutil.copy2"),
    ])


def test_visible_copy_checks_a_temporary_folder_below_the_run(tmp_path, monkeypatch):
    import tempfile
    from g6.harness import _completion_storage as store

    home, outside = tmp_path / "home", tmp_path / "outside"
    source, link = home / "completion_loop" / "run" / "sets", home / "completion_loop" / "run" / "temp"
    source.mkdir(parents=True)
    (source / "tree.json").write_text("{}", encoding="utf-8")
    outside.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(link))
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(WorkspaceRefused) as refused:
            store.visible_copy(source)
        assert refused.value.code == OUTSIDE_ROOT
        assert list(outside.iterdir()) == []
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(link)
