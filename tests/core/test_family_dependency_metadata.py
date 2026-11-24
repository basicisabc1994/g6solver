# SPDX-License-Identifier: Apache-2.0
"""Required roots use the same metadata/fallback mapping as declared optional roots."""
from __future__ import annotations

from importlib import metadata
from types import SimpleNamespace

import pytest


LAYOUTS = ("wheel", "top-level-only", "record-only", "editable-pth", "no-record", "no-top-level", "neither")
DISTRIBUTIONS = ("fastmcp", "pydocket", "redis", "Pillow", "bridge-pkg")


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("subject", DISTRIBUTIONS)
def test_required_roots_never_become_optional(tmp_path, monkeypatch, layout, subject):
    from g6.mcp import dependencies

    requirements = {
        "g6": ['fastmcp; extra == "mcp"', 'mcp; extra == "mcp"', "Pillow", "bridge-pkg",
               *[f'{name}; extra == "abstraction"' for name in DISTRIBUTIONS],
               'g6_absent_extra; extra == "abstraction"'],
        "fastmcp": ["pydocket[queue]"], "mcp": [],
        "pydocket": ['redis; extra == "queue"', 'inactive; python_version < "2"'],
        "redis": ["fastmcp"], "Pillow": [], "bridge-pkg": [],
    }
    fallback = {"Pillow": "PIL", "bridge-pkg": "bridge_pkg"}
    expected_roots = {}
    for name, needs in requirements.items():
        root = fallback.get(name, name)
        directory = tmp_path / (name.replace("-", "_") + "-1.0.dist-info")
        directory.mkdir()
        text = f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
        text += "".join(f"Requires-Dist: {need}\n" for need in needs)
        (directory / "METADATA").write_text(text, encoding="utf-8")
        selected = layout if name == subject else "wheel"
        # Non-default roots ensure actual metadata wins over the name fallback.
        mapped = root + "_actual" if selected in {"wheel", "top-level-only", "record-only"} else root
        expected_roots[name] = mapped
        if selected in {"wheel", "top-level-only", "no-record"}:
            top = mapped if selected != "no-record" else root
            (directory / "top_level.txt").write_text(top + "\n", encoding="utf-8")
        if selected in {"wheel", "record-only", "no-top-level", "editable-pth"}:
            record = f"{mapped}/__init__.py,,\n"
            if selected == "editable-pth":
                record = f"_{root}.pth,,\n"
                (tmp_path / f"_{root}.pth").write_text("editable-source\n", encoding="utf-8")
            (directory / "RECORD").write_text(record, encoding="utf-8")

    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(tmp_path)])}
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(tmp_path)]))
    monkeypatch.setattr(dependencies, "metadata", SimpleNamespace(
        requires=lambda name: records[name].requires,
        packages_distributions=metadata.packages_distributions,
    ))
    dependencies.optional_import_roots.cache_clear()
    try:
        assert dependencies.optional_import_roots("abstraction,mcp") == frozenset({"g6_absent_extra"})
        for name in (*DISTRIBUTIONS, "mcp"):
            root = expected_roots[name]
            failure = ModuleNotFoundError("broken required install", name=root + ".server")
            assert dependencies.optional_dependency_missing("abstraction", failure) is False
        assert dependencies.extra_import_roots("abstraction") == frozenset({
            "g6_absent_extra", *(expected_roots[name] for name in DISTRIBUTIONS),
        })
    finally:
        dependencies.optional_import_roots.cache_clear()


@pytest.mark.parametrize("top_level", (None, "declared_root\n"), ids=("record-only", "top-level-wins"))
def test_record_roots_use_import_paths_only(tmp_path, monkeypatch, top_level):
    from g6.mcp import dependencies

    directory = tmp_path / "g6_record_subject-1.0.dist-info"
    directory.mkdir()
    (directory / "METADATA").write_text(
        "Metadata-Version: 2.3\nName: g6-record-subject\nVersion: 1.0\n", encoding="utf-8")
    (directory / "RECORD").write_text(
        "actual_package/__init__.py,,\n"
        "actual_package/nested/module.py,,\n"
        '"namespace_package/with,comma.py",,\n'
        "standalone_module.py,,\n"
        "g6_record_subject-1.0.dist-info/METADATA,,\n"
        "g6_record_subject-1.0.data/purelib/relocated/__init__.py,,\n"
        "__pycache__/cached.py,,\n"
        "injected.pth,,\n"
        "../escaped_package/__init__.py,,\n"
        "inside/../../escaped.py,,\n"
        "/absolute_package/__init__.py,,\n"
        "C:/absolute_package/__init__.py,,\n"
        "..\\windows_escape\\__init__.py,,\n"
        "standalone_data.txt,,\n", encoding="utf-8")
    if top_level is not None:
        (directory / "top_level.txt").write_text(top_level, encoding="utf-8")
    scan = metadata.distributions
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(tmp_path)]))
    monkeypatch.setattr(dependencies, "metadata", SimpleNamespace(
        requires=lambda name: ['g6-record-subject; extra == "abstraction"'],
        packages_distributions=metadata.packages_distributions,
    ))
    expected = {"actual_package", "namespace_package", "standalone_module"} if top_level is None else {"declared_root"}
    assert dependencies.extra_import_roots("abstraction") == frozenset(expected)
