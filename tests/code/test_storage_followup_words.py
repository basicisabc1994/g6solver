# SPDX-License-Identifier: Apache-2.0
"""Refusals disclose neither resolved machine paths nor filesystem exception text."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused


@pytest.mark.parametrize("caller", ["creator", "conformance"])
def test_empty_package_refusals_name_only_relative_folders(caller, monkeypatch):
    from g6.code.component_creator import block, conformance

    folder = Path(__file__).resolve().parent
    roots = _roots.for_folder(folder.parent, "test source folder")
    if caller == "creator":
        monkeypatch.setattr(_roots, "walk", lambda *a, **k: _roots.Walk())

        def call():
            return block._modules(SimpleNamespace(source="", path=str(folder), module=""), roots)
    else:
        def call():
            return conformance.check_folder(str(folder), roots)
    with pytest.raises(CodeRefused) as refused:
        call()
    assert str(folder.parent).lower() not in refused.value.reason.lower()


def test_scaffold_write_denial_does_not_repeat_system_words_or_paths(monkeypatch):
    from g6.code.component_creator import ComponentCreatorBlock, ComponentCreatorInput, block

    folder = Path(__file__).resolve().parent
    roots = _roots.for_folder(folder.parent, "test source folder")
    monkeypatch.setattr(_roots, "destination", lambda *a, **k: folder / "new")

    def fail(dest, files):
        raise PermissionError(13, "private-system-details", str(dest / "x.py"))

    monkeypatch.setattr(block, "write", fail)
    spec = {"name": "review_unit", "family": "code", "summary": "Track units.",
            "ops": [{"name": "count", "does": "count units", "kind": "read"}],
            "mitigates": ["incomplete_component"]}
    out = ComponentCreatorBlock(roots=roots).infer(ComponentCreatorInput(
        op="render", spec=spec, dest=str(folder / "new"), dry_run=False)).unwrap()
    assert out["code"] == "G6_E_SCAFFOLD_WRITE_FAILED"
    assert "private-system-details" not in str(out)
    assert str(folder.parent).lower() not in out["warning_card"]["message"].lower()


def test_conformance_unreadable_test_does_not_repeat_system_words_or_paths(monkeypatch):
    from g6.code.component_creator import conformance

    folder = Path(__file__).resolve().parent
    roots = _roots.for_folder(folder.parent, "test source folder")

    def fail(*_, **kwargs):
        raise PermissionError("private-system-details")

    monkeypatch.setattr(conformance._roots, "path_info", fail)
    problems = []
    assert conformance._tests_near(folder, "sample", "", roots, problems) == {}
    assert problems
    assert "private-system-details" not in str(problems)
    assert str(folder.parent).lower() not in str(problems).lower()


@pytest.mark.parametrize("case", ["file", "missing", "occupied", "parent", "outside", "no_root"])
def test_path_refusals_do_not_name_resolved_paths(case, tmp_path, monkeypatch):
    root = tmp_path.resolve() / "declared"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "sub" / "held.txt").write_text("kept", encoding="utf-8")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "private-home"))
    roots = _roots.Roots((root,), "test")
    calls = {"file": lambda: _roots.existing(str(root / "sub"), roots, kind="file"),
             "missing": lambda: _roots.existing(str(root / "absent.txt"), roots),
             "occupied": lambda: _roots.destination(str(root / "sub" / "held.txt"), roots),
             "parent": lambda: _roots.destination(str(root / "absent" / "child"), roots),
             "outside": lambda: _roots.existing(str(tmp_path / "outside.txt"), roots),
             "no_root": lambda: _roots.existing(str(root / "sub"), _roots.Roots((), "none"))}
    with pytest.raises(CodeRefused) as refused:
        calls[case]()
    assert str(tmp_path).lower() not in refused.value.reason.lower()
    if case in {"file", "missing", "occupied", "parent"}:
        assert {"file": "sub", "missing": "absent.txt", "occupied": "sub/held.txt",
                "parent": "absent/child"}[case] in refused.value.reason


def test_declaration_help_names_the_setting_without_resolving_the_home(monkeypatch, tmp_path):
    # Windows refuses a NUL in an environment value, so the home is a real folder whose path must not appear
    home = tmp_path / "private-home-folder"
    monkeypatch.setenv("G6_HOME", str(home))
    text = _roots.how_to_declare()
    assert "code/config.json" in text
    assert "private-home-folder" not in text and str(tmp_path).lower() not in text.lower()


@pytest.mark.parametrize("failure", [UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
                                     OSError("private-system-details"), ValueError("private-system-details")])
def test_config_read_failures_are_g6_refusals(failure, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "code").mkdir(parents=True)
    (home / "code" / "config.json").write_bytes(b"\xff")
    monkeypatch.setenv("G6_HOME", str(home))
    original = Path.read_text

    def fail_config(path, *args, **kwargs):
        if path.name == "config.json":
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_config)
    with pytest.raises(CodeRefused) as refused:
        _roots.load({})
    assert refused.value.code == _roots.CONFIG_INVALID
    assert "private-system-details" not in refused.value.reason
    assert str(home) not in refused.value.reason
