"""Completion and checkout boundaries for acquisition material."""
from __future__ import annotations

import os
import subprocess

import pytest

from validation.distill.pilot import _material
from validation.distill.pilot.m1b import allocation_m1b, chooser, counts, material
from validation.distill.pilot.m1b._mine_io import digest


def allocation_inputs():
    entries = [{"repository": f"org/repo{i:02}", "pr": 1,
                "parent": "b" * 40, "merge_commit": "a" * 40} for i in range(16)]
    environment = {"resolvable_modules": [], "loaded_packages": [], "pytest_dependencies": [],
                   "interpreter_exists": False}
    values = dict.fromkeys(counts.CONFIGURATIONS, 16)
    inventory = {"complete": True, "counts": values, "selection": chooser.choose(values).public(),
                 "binding": digest({"entries": entries, "environment": environment, "version": 1}),
                 "completed": {e["repository"] + "#1": {
                     "summary": {"counts": dict.fromkeys(counts.CONFIGURATIONS, 1)}} for e in entries}}
    return inventory, {"complete": True, "entries": entries}, environment


@pytest.mark.parametrize("completion", [False, None, 0, 1, "true", "missing"])
def test_allocation_requires_pool_completion_boolean(completion):
    inventory, pool, environment = allocation_inputs()
    manifest = allocation_m1b.build_manifest(inventory, pool, environment)
    assert allocation_m1b.check(manifest, inventory, pool, environment) == []
    if completion == "missing":
        del pool["complete"]
    else:
        pool["complete"] = completion
    with pytest.raises(ValueError, match="complete pool required"):
        allocation_m1b.build_manifest(inventory, pool, environment)
    assert allocation_m1b.check(manifest, inventory, pool, environment)


def git(repo, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                           "-c", f"core.attributesFile={os.devnull}",
                           "-c", f"core.hooksPath={repo / 'empty-hooks'}", "-C", str(repo), *args],
                          env=environment, capture_output=True, check=True).stdout


def repository(tmp_path, base, fixed):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "--template=", "--initial-branch=main")
    commits = []
    for message, files in (("base", base), ("fixed", fixed)):
        for name, data in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        git(repo, "add", ".")
        git(repo, "commit", "-m", message)
        commits.append(git(repo, "rev-parse", "HEAD").decode().strip())
    return repo, {"repository": "org/fixture", "pr": 1,
                  "parent": commits[0], "merge_commit": commits[1]}


def extract(tmp_path, repo, entry):
    return material._read(entry, tmp_path / "cache", source=repo,
                          repository_root=tmp_path / "host-repository")


def assert_scratch_removed(tmp_path):
    assert not list((tmp_path / "cache").glob("*/material-*"))


def test_each_checkout_uses_its_own_nested_attributes(tmp_path):
    repo, entry = repository(tmp_path, {
        "pkg/.gitattributes": b"*.py ident text eol=crlf\n",
        "pkg/subject.py": b'IDENT = "$Id$"\nVALUE = 1\n',
    }, {
        "pkg/.gitattributes": b"*.py -ident text eol=lf\n",
        "pkg/subject.py": b'IDENT = "$Id$"\nVALUE = 2\n',
    })
    blob = git(repo, "rev-parse", f"{entry['parent']}:pkg/subject.py").decode().strip()
    actual = extract(tmp_path, repo, entry)
    assert actual["base"]["pkg/subject.py"] == f'IDENT = "$Id: {blob} $"\nVALUE = 1\n'
    assert actual["fixed"]["pkg/subject.py"] == 'IDENT = "$Id$"\nVALUE = 2\n'
    assert_scratch_removed(tmp_path)


def test_fixed_attributes_and_binary_patch_match_image_descriptors(tmp_path):
    repo, entry = repository(tmp_path, {
        ".gitattributes": b"subject.py diff\n",
        "subject.py": b"VALUE = 1\n",
        "data.bin": b"\x00old\n",
    }, {
        ".gitattributes": b"subject.py -diff\n",
        "subject.py": b"VALUE = 2\n",
        "data.bin": b"\x00new\n",
    })
    actual = extract(tmp_path, repo, entry)
    expected = git(repo, "diff", "--binary", "--no-renames", entry["parent"], entry["merge_commit"])
    assert actual["patch"] == expected.decode().replace("\r\n", "\n").replace("\r", "\n")
    assert "GIT binary patch" in actual["patch"]
    assert _material.changed_lines(actual, "subject.py") == ()
    assert_scratch_removed(tmp_path)


def test_source_limit_applies_after_checkout_conversion(tmp_path):
    repo, entry = repository(tmp_path, {
        ".gitattributes": b"*.py text eol=crlf\n",
        "context.py": b"#\n" * 65536,
        "subject.py": b"VALUE = 1\n",
    }, {"subject.py": b"VALUE = 2\n"})
    with pytest.raises(_material.MaterialError, match="^support_missing_or_large$"):
        extract(tmp_path, repo, entry)
    assert_scratch_removed(tmp_path)


@pytest.mark.parametrize("directory,refused", [("nested/fixture.py", True), (".hidden/fixture.py", False)])
def test_python_directory_obeys_support_visibility(tmp_path, directory, refused):
    repo, entry = repository(tmp_path, {
        "subject.py": b"VALUE = 1\n", f"{directory}/payload.txt": b"fixture\n",
    }, {"subject.py": b"VALUE = 2\n"})
    if refused:
        with pytest.raises(_material.MaterialError, match="^support_missing_or_large$"):
            extract(tmp_path, repo, entry)
    else:
        assert extract(tmp_path, repo, entry)["fixed"] == {"subject.py": "VALUE = 2\n"}
    assert_scratch_removed(tmp_path)
