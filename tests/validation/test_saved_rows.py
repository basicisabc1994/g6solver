# SPDX-License-Identifier: Apache-2.0
"""The saved rows have one owner (study review 1, class 1: attribution from a row's claims, met a sixth time). ``wecommon`` alone
names their folders, finds a file in them, reads a row or writes one (``shelf``, ``keep``); a store kept beside them is reached by
its name (``beside``). The inventory is made from the code's own listing: every module of the study, parsed, and every expression in
it that names the rows' folders (by the owner's own constants) or reads a field of a row the old reader handed out (``.row``)."""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(STUDY))

import wecommon as c  # noqa: E402

OWNER = "wecommon"
#: what names a saved row's folder or a row: the owner's constants and its name for a row's file (from its own code)
NAMING = {"SAVED", "AMENDED", "saved_name"}
PATH_CALLS = {"Path", "PurePath", "PurePosixPath", "PureWindowsPath", "joinpath", "join"}


def _folders() -> set[str]:
    return {c.SAVED, c.AMENDED}


def inventory(folder: Path = STUDY) -> list[str]:
    """Every expression in every module of ``folder`` (``ast``) that names the saved rows' folders or reads a row's field, as
    "module:line function: what"."""
    folders, found = _folders(), []
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names if alias.name == OWNER}
        imported = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    and node.module == OWNER for alias in node.names if alias.name in NAMING}
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def where(node: ast.AST) -> str:
            up = node
            while up in parents:
                up = parents[up]
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    return up.name
            return "<module>"

        for node in ast.walk(tree):
            what = ""
            up = parents.get(node)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases \
                    and node.attr in NAMING:
                what = f"{node.value.id}.{node.attr}"
            elif isinstance(node, ast.Name) and node.id in imported:
                what = node.id
            elif isinstance(node, ast.Attribute) and node.attr == "row" and isinstance(node.ctx, ast.Load):
                what = ".row (a row's fields)"
            elif isinstance(node, ast.Attribute) and node.attr in ("parent", "parents") and isinstance(node.value, ast.Call) \
                    and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "beside" \
                    and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id in aliases:
                what = f"{node.value.func.value.id}.beside(...).{node.attr} (the rows' folder)"
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                as_path = (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Div)) or (
                    isinstance(up, ast.Call) and node in up.args
                    and getattr(up.func, "attr", getattr(up.func, "id", "")) in PATH_CALLS)
                if (text in folders and as_path) or text.startswith((f"{c.SAVED}/", f"{c.SAVED}\\")):
                    what = repr(text)
            if what:
                found.append((path.relative_to(folder).with_suffix("").as_posix(), node.lineno, where(node), what))
    return [f"{module}:{line} {name}: {what}" for module, line, name, what in sorted(found)]


#: the calls that read the saved rows: the owner's readers, and ``compare.saved``, which reads them through it
READERS = {(OWNER, "shelf"), (OWNER, "saved_rows"), (OWNER, "saved_row"), ("compare", "saved")}


def two_reads(folder: Path = STUDY) -> list[str]:
    """Every function of every module of ``folder`` that reads the saved rows more than once (``READERS``): one that could pair a
    run from one read with a score from another (study review 1, finding 22), as "module function: the reads"."""
    found = []
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = {alias.asname or alias.name: alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                  for alias in node.names}
        for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            reads = [f"{module[call.func.value.id]}.{call.func.attr}" for call in ast.walk(function)
                     if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                     and isinstance(call.func.value, ast.Name) and call.func.value.id in module
                     and (module[call.func.value.id], call.func.attr) in READERS]
            if len(reads) > 1:
                found.append(f"{path.relative_to(folder).with_suffix('').as_posix()} {function.name}: {', '.join(reads)}")
    return found


def test_no_module_but_the_owner_names_the_saved_rows_folders_or_reads_a_rows_field():
    outside = [line for line in inventory() if not line.startswith(f"{OWNER}:")]
    assert outside == [], "\n".join(["read or named outside wecommon:", *outside])


def test_no_function_takes_a_run_and_its_score_from_two_reads_of_the_saved_rows():
    assert two_reads() == [], "\n".join(["read more than once in one function:", *two_reads()])


def test_the_inventory_sees_what_it_looks_for(tmp_path):
    """The listing's own control: it finds each kind of expression it lists, in a module written for it, and nothing else."""
    (tmp_path / "reader.py").write_text(
        "import wecommon as w\nfrom pathlib import Path\n"
        "def a(root):\n    return root / w.SAVED\n"
        "def b(root):\n    return (root / 'loop').glob('*.json')\n"
        "def d(root):\n    return Path(root, 'loop')\n"
        "def e(root):\n    return open(f'{root}/x') or 'loop/K2-x-1.json'\n"
        "def f(one):\n    return one.row\n"
        "def g(root):\n    return root / 'amended'\n"
        "def h(rows):\n    return {'loop': 1}, rows.get('amended'), w.beside(rows, 'final_review')\n"
        "import compare as cm\n"
        "def twice(root):\n    return cm.saved(root), w.saved_rows(root, None)\n"
        "def once(root):\n    return w.shelf(root, None)\n"
        "def up(root):\n    return w.beside(root, 'x').parent.glob('*.json')\n", encoding="utf-8")
    assert [line.split(" ", 1)[1] for line in inventory(tmp_path)] == [
        "a: w.SAVED", "b: 'loop'", "d: 'loop'", "e: 'loop/K2-x-1.json'", "f: .row (a row's fields)", "g: 'amended'",
        "up: w.beside(...).parent (the rows' folder)"]
    assert two_reads(tmp_path) == ["reader twice: compare.saved, wecommon.saved_rows"]


def test_a_saved_row_carries_no_row_so_no_reader_can_read_one():
    assert "row" not in {f.name for f in dataclasses.fields(c.Saved)} and not hasattr(c.Saved, "row")
    assert all(f.type not in ("Mapping[str, Any]", "dict") for f in dataclasses.fields(c.Saved))


def test_a_store_beside_the_rows_is_never_their_folder_nor_the_amended_verdicts(tmp_path):
    assert c.beside(tmp_path, "final_review") == tmp_path / c.SAVED / "final_review"
    for name in ("", ".", "..", c.AMENDED, "a/b", "a\\b"):
        with pytest.raises(ValueError):
            c.beside(tmp_path, name)


def _run(root, arm="K2", task="bug", repeat=1):
    archive = c.Archive(root)
    run = archive.start("run", task, arm=arm, repeat=repeat)
    run.write("final.patch", b"patch\n")
    run.finish({"agent_started": True}, "finished")
    return archive, run, {"run": run.rel, "arm": arm, "task": task, "repeat": repeat, "passes": True, "failing": 0}


def test_the_reader_finds_every_json_whatever_its_name_and_refuses_what_is_no_runs_own_row(tmp_path):
    archive, run, row = _run(tmp_path)
    _, other, other_row = _run(tmp_path, arm="A")
    folder = tmp_path / c.SAVED
    folder.mkdir()
    (folder / "anything.json").write_text(json.dumps(row), "utf-8")                         # its run's own, under any name
    (folder / "A-bug-1.json").write_text(json.dumps({**other_row, "arm": "K2"}), "utf-8")    # says another arm of its run
    (folder / "cegis.json").write_text(json.dumps({"status": "stopped"}), "utf-8")         # no row: names no run
    (folder / "torn.json").write_bytes(b'{"run":')
    got = c.shelf(archive, lambda task: None)
    assert [(one.key, one.file, one.passes) for one in got.rows] == [(("K2", "bug", 1), "anything.json", True)]
    why = {f.file: (f.why, f.torn) for f in got.found if f.why}
    assert set(why) == {"A-bug-1.json", "cegis.json", "torn.json"} and why["torn.json"][1] and not why["cegis.json"][1]
    assert got.bars(("A", "bug", 1)) and not got.bars(("K2", "bug", 1))          # the refused row bars its run's key alone
    assert got.blocks(("A", "bug", 1)) and not got.blocks(("K2", "bug", 1), file="anything.json", run=run.rel)


def test_two_files_naming_one_run_or_two_runs_holding_one_key_give_no_row(tmp_path):
    archive, run, row = _run(tmp_path)
    folder = tmp_path / c.SAVED
    folder.mkdir()
    for name in ("K2-bug-1.json", "copy.json"):
        (folder / name).write_text(json.dumps(row), "utf-8")
    assert c.shelf(archive, lambda task: None).rows == () and c.shelf(archive, lambda task: None).bars(("K2", "bug", 1))
    (folder / "copy.json").unlink()
    _, again, again_row = _run(tmp_path)                    # a second run started for the same arm, bug and repeat
    (folder / "again.json").write_text(json.dumps(again_row), "utf-8")
    assert c.shelf(archive, lambda task: None).rows == ()


def test_a_verdict_that_is_not_true_or_false_is_none_never_a_pass(tmp_path):
    archive, run, row = _run(tmp_path)
    for said in ("yes", 1, None):
        assert c.verdict(archive, run.rel, {**row, "passes": said}) is None
        assert c.verdict(archive, run.rel, {**row, "amended_passes": said, "amendment_sha256": "d"}, amendment="d") is None
    assert c.verdict(archive, run.rel, row) is True


@pytest.mark.skipif(os.name != "nt", reason="names that differ only in case are one file on Windows alone")
def test_a_row_kept_under_the_keys_name_in_another_case_bars_it_as_the_file_it_is(tmp_path):
    archive, run, row = _run(tmp_path)
    _, other, other_row = _run(tmp_path, arm="A")
    folder = tmp_path / c.SAVED
    folder.mkdir()
    (folder / "k2-bug-1.json").write_text(json.dumps(other_row), "utf-8")      # the file K2-bug-1.json, on this file system
    assert c.shelf(archive, lambda task: None).blocks(("K2", "bug", 1))        # no row of K2's run is written over it


def test_the_writer_never_writes_over_another_runs_row_and_replaces_a_torn_one(tmp_path):
    archive, run, row = _run(tmp_path)
    row = {**row, c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}      # as loop.one writes it (follow-up review 2, 1)
    _, other, other_row = _run(tmp_path, arm="A")
    folder = tmp_path / c.SAVED
    folder.mkdir()
    (folder / "K2-bug-1.json").write_text(json.dumps(other_row), "utf-8")
    with pytest.raises(c.ArchiveError, match="another run's"):
        c.keep(archive, row)
    assert json.loads((folder / "K2-bug-1.json").read_text("utf-8")) == other_row
    (folder / "K2-bug-1.json").write_bytes(b'{"run":')
    c.keep(archive, row)
    assert json.loads((folder / "K2-bug-1.json").read_text("utf-8")) == row
    with pytest.raises(c.ArchiveError, match="start names"):
        c.keep(archive, {**row, "arm": "A"})
    assert [p.name for p in folder.iterdir()] == ["K2-bug-1.json"]        # nothing left beside it
