# SPDX-License-Identifier: Apache-2.0
"""One decoder source, generated inside each consumer's allowed import boundary."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "src/g6/_decoder_limits.py"
HEADER = (b"# SPDX-License-Identifier: Apache-2.0\n"
          b"# Generated from src/g6/_decoder_limits.py; run tools/sync_decoder_limits.py --write.\n")
COPIES = {
    "src/g6/core/decoder_limits.py",
    "src/g6/self_training/_decoder_limits.py",
    "src/g6/research/mission_bridge/_decoder_limits.py",
}


def _sync():
    spec = importlib.util.spec_from_file_location("sync_decoder_limits", ROOT / "tools/sync_decoder_limits.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root):
    source = root / SOURCE
    source.parent.mkdir(parents=True)
    source.write_bytes((ROOT / SOURCE).read_bytes())
    return _sync()


def test_every_decoder_copy_is_known_and_byte_equal_to_the_one_source():
    expected = HEADER + (ROOT / SOURCE).read_bytes()
    for name in sorted(COPIES):
        assert (ROOT / name).read_bytes() == expected, name
    sync = _sync()
    assert set(sync.COPIES) == COPIES
    assert set(sync.discover()) == COPIES
    assert sync.problems() == []
    assert sync.main(["--check"]) == 0


@pytest.mark.parametrize("name", sorted(COPIES | {SOURCE}))
def test_every_decoder_implementation_starts_with_the_license(name):
    assert (ROOT / name).read_bytes().splitlines()[0] == b"# SPDX-License-Identifier: Apache-2.0", name


def test_every_decoder_implementation_imports_only_the_standard_library():
    for name in sorted(COPIES | {SOURCE}):
        tree = ast.parse((ROOT / name).read_bytes())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, name
                imports = [node.module]
            else:
                continue
            assert all(module.split(".")[0] in sys.stdlib_module_names for module in imports), (name, imports)


@pytest.mark.parametrize("name,boundary", [
    ("self_training/persistence.py", "g6.self_training._decoder_limits"),
    ("self_training/rubrics.py", "g6.self_training._decoder_limits"),
    ("research/mission_bridge/fixtures.py", "g6.research.mission_bridge._decoder_limits"),
    ("operator/queue.py", "g6.core.decoder_limits"),
    ("operator/store.py", "g6.core.decoder_limits"),
    ("sources/ctx_git/config.py", "g6.core.decoder_limits"),
    ("sources/ctx_git/runner.py", "g6.core.decoder_limits"),
])
def test_consumers_import_the_decoder_inside_their_allowed_boundary(name, boundary):
    tree = ast.parse((ROOT / "src/g6" / name).read_bytes())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               and "decoder_limits" in (node.module or "")]
    assert imports and set(imports) == {boundary}, name


def test_sync_reports_missing_and_drifted_copies_and_write_is_idempotent(tmp_path):
    sync = _tree(tmp_path)
    assert sync.problems(tmp_path) == [f"{name} is missing" for name in sorted(COPIES)]
    sync.write(tmp_path)
    expected = {name: (tmp_path / name).read_bytes() for name in COPIES}
    assert all(data == HEADER + (tmp_path / SOURCE).read_bytes() for data in expected.values())
    target = tmp_path / sorted(COPIES)[0]
    target.write_bytes(target.read_bytes() + b"# drift\n")
    assert sync.problems(tmp_path) == [f"{sorted(COPIES)[0]} differs"]
    sync.write(tmp_path)
    assert sync.problems(tmp_path) == []
    sync.write(tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in COPIES} == expected


@pytest.mark.parametrize("style", ["header", "legacy-header", "no-header", "renamed", "drifted"])
def test_sync_rejects_every_unknown_copy_without_deleting_it(tmp_path, style):
    sync = _tree(tmp_path)
    sync.write(tmp_path)
    body = (tmp_path / SOURCE).read_bytes()
    filename = "extra.py" if style in {"renamed", "legacy-header"} else "_decoder_limits.py"
    unknown = tmp_path / "src/g6/extra" / filename
    unknown.parent.mkdir()
    if style == "header":
        body = HEADER + body
    elif style == "legacy-header":
        body = HEADER.split(b"\n", 1)[1] + b"# unrelated body\n"
    elif style == "drifted":
        body = body.replace(b"return int(digits)", b"return 0")
    unknown.write_bytes(body)
    relative = unknown.relative_to(tmp_path).as_posix()
    assert relative in sync.discover(tmp_path)
    assert sync.problems(tmp_path) == [f"{relative} is not a registered copy"]
    sync.write(tmp_path)
    assert unknown.read_bytes() == body
    assert sync.problems(tmp_path) == [f"{relative} is not a registered copy"]


def test_sync_check_never_writes_and_write_still_refuses_unknown_copies(tmp_path, monkeypatch):
    sync = _tree(tmp_path)
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    assert sync.main(["--check"]) == 1
    assert all(not (tmp_path / name).exists() for name in COPIES)
    assert sync.main(["--write"]) == 0
    unknown = tmp_path / "src/g6/unknown.py"
    unknown.write_bytes(HEADER + b"# unrelated body\n")
    assert sync.main(["--write"]) == 1
    assert unknown.read_bytes() == HEADER + b"# unrelated body\n"
    assert sync.main([]) == 2
