"""Windows DOS-device aliases must be refused before file-system queries."""
import ctypes
import os
from pathlib import Path

import pytest

from g6.core.storage import _elsewhere
from g6.mcp import improve, workflow
from g6.workforce import repo_tests


@pytest.mark.skipif(os.name != "nt", reason="Windows DOS-device normalization")
@pytest.mark.parametrize("tool", ["checked_repo", "workflow_project", "improve_start", "completion_hook"])
def test_dos_device_alias_is_refused_before_any_probe(monkeypatch, tool):
    raw = r"C:\NUL"
    canonical = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetFullPathNameW(raw, len(canonical), canonical, None)
    assert 0 < size < len(canonical)
    assert canonical.value == r"\\.\NUL"
    assert _elsewhere(canonical.value)

    touched = []

    def is_dir(path):
        touched.append(("is_dir", str(path)))
        return False

    def resolve(path, *args, **kwargs):
        touched.append(("resolve", str(path)))
        return path

    def is_file(path):
        touched.append(("is_file", str(path)))
        return False

    def git(repo, *args, **kwargs):
        touched.append(("git", str(repo)))
        raise repo_tests.RepoRefused("G6_E_GIT_REPO_INVALID", "synthetic missing repo")

    if tool == "completion_hook":
        from g6.harness import completion_hook
        monkeypatch.setattr(repo_tests, "_git", git)
        assert completion_hook._here(raw) is None
    else:
        monkeypatch.setattr(type(Path()), "is_dir", is_dir)
        monkeypatch.setattr(type(Path()), "resolve", resolve)
        monkeypatch.setattr(type(Path()), "is_file", is_file)
        if tool == "checked_repo":
            with pytest.raises(repo_tests.RepoRefused) as refused:
                repo_tests.checked_repo(raw)
            code = refused.value.code
        elif tool == "workflow_project":
            assert workflow._project(raw) is None
        else:
            reply = improve.asked(raw)
    assert not touched, touched
    if tool == "checked_repo":
        assert code == "G6_E_INPUT_VALIDATION"
    if tool == "improve_start":
        assert improve.NOT_A_FOLDER in str(reply)


@pytest.mark.skipif(os.name != "nt", reason="Windows DOS-device names")
@pytest.mark.parametrize("path", [r"C:\NUL", r"C:\work\nul.txt", r"C:\work\Com1", "C:/work/LPT9.tar.gz",
                                  r"C:\work\con .", r"C:\work\AUX\repo", r"C:\work\prn.", "C:\\work\\COM\u00b9"])
def test_the_shared_rule_refuses_a_device_name_in_any_folder(path):
    assert repo_tests.elsewhere(path)


@pytest.mark.parametrize("path", [r"C:\work\console", r"C:\work\nullable\repo", r"C:\work\com10", r"C:\work\auxiliary"])
def test_the_shared_rule_lets_ordinary_names_through(path):
    assert repo_tests.elsewhere(path) is None
