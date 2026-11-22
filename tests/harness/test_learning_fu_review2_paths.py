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


def _windows_reads(text: str) -> list[str]:
    """What Windows makes of ``text`` and of each folder on it as Python spells them (its full path), as a test oracle."""
    forms = [text, *(str(p) for p in (Path(text), *Path(text).parents))]
    read = []
    for form in forms:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetFullPathNameW(form, len(buffer), buffer, None)
        read.append(buffer.value if 0 < size < len(buffer) else "")
    return read


@pytest.mark.skipif(os.name != "nt", reason="Windows device paths")
@pytest.mark.parametrize("path", [r"C:\NUL", r"C:\NUL:", "COM1 :", "C:/NUL", r"C:\work\y\..\NUL", r"C:\work\NUL.",
                                  r"C:\work\NUL\.", "C:\\work\\NUL\\", r"C:\work\NUL \repo", r"C:\work\NUL\repo",
                                  r"\\?\C:\work", r"\\.\C:\work", r"\.\??\C:\work", "CONIN$", "C:\\work\x00\\NUL"])
def test_the_shared_rule_refuses_what_windows_reads_as_a_device(path):
    assert repo_tests.elsewhere(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows device paths")
@pytest.mark.parametrize("path", [r"C:\CON:", "nul.txt:", r"C:\work\nul.txt", r"C:\work\Com1", "C:/work/LPT9.tar.gz",
                                  r"C:\work\con .", r"C:\work\AUX\repo", r"C:\work\prn.", "C:\\work\\COM\u00b9"])
def test_a_device_like_name_is_refused_unless_windows_reads_every_form_as_a_plain_path(path):
    # the system decides: a build that reads one of these as a device refuses it; one that reads it as a file lets it by
    plain = all(full and not full.startswith("\\\\") for full in _windows_reads(path))
    assert repo_tests.elsewhere(path) or plain, _windows_reads(path)


@pytest.mark.parametrize("path", [r"C:\work\console", r"C:\work\nullable\repo", r"C:\work\com10", r"C:\work\auxiliary",
                                  "", "  ", "repo", r"..\repo"])
def test_the_shared_rule_lets_ordinary_names_through(path):
    assert repo_tests.elsewhere(path) is None
