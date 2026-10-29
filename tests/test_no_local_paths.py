# SPDX-License-Identifier: Apache-2.0
"""No tracked file holds the home folder of the person running the tests: a private path never reaches the repository.

The home folder is read at run time (``Path.home()``), never written here, and looked for in every written form a
file is likely to hold: as the system writes it, with forward slashes, with its backslashes doubled (as JSON and Python
strings hold them), and on Windows as a shell writes it (``/c/...``), without regard to case on Windows. Only the files
git tracks are read (``git ls-files``); binary files (a NUL byte in their first 8 kilobytes) and files over 2 megabytes
are skipped. A finding names the file and the line, with the path itself masked. When the home folder is not a
person's own (fewer than two folders below the root, such as a container's ``/root``), there is nothing to look for, and
the check is skipped."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePath, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2 * 1024 * 1024
SNIFF_BYTES = 8192
MASK = "<home folder>"


def personal(home: PurePath) -> bool:
    """Whether ``home`` is a person's own folder: at least two folders below its root (``/home/<name>``)."""
    return len(home.parts) >= 3


def home_pattern(home: PurePath) -> re.Pattern:
    """Every written form of ``home``, as one pattern that does not match a longer folder name."""
    windows = isinstance(home, PureWindowsPath) or bool(home.drive)
    native = str(home)
    forms = {native, native.replace("\\", "/"), native.replace("\\", "\\\\")}
    if windows and home.drive:
        forms.add("/" + home.drive[0].lower() + "/" + "/".join(home.parts[1:]))
    alternatives = "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
    return re.compile(f"(?:{alternatives})(?![A-Za-z0-9._-])", re.IGNORECASE if windows else 0)


def scan(root: Path, home: PurePath) -> list[str]:
    """Each line of a tracked text file under ``root`` that holds ``home``: ``file:line: text``, the path masked."""
    pattern = home_pattern(home)
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
    found = []
    for rel in listed.decode("utf-8", "surrogateescape").split("\0"):
        path = root / rel
        if not rel or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:SNIFF_BYTES]:
            continue
        text = data.decode("utf-8", "replace")
        if not pattern.search(text):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                found.append(f"{rel}:{number}: {pattern.sub(MASK, line).strip()[:200]}")
    return found


def test_no_tracked_file_holds_the_home_folder():
    home = Path.home()
    if not personal(home):
        pytest.skip("the home folder is not a person's own, so no private path can come from it")
    found = scan(ROOT, home)
    assert not found, "tracked files hold the home folder (masked here):\n" + "\n".join(found[:50])


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_a_planted_home_folder_is_found_in_a_temporary_checkout_and_masked_in_the_report(tmp_path):
    home = tmp_path / "people" / "someone"                     # a home folder made for the test, never a real one
    root = tmp_path / "checkout"
    root.mkdir()
    native, forward = str(home), str(home).replace("\\", "/")
    (root / "notes.md").write_text(f"first line\nthe data is in {native}{'/'}cache\nlast line\n", encoding="utf-8")
    (root / "config.json").write_text('{"path": "' + native.replace("\\", "\\\\") + '"}\n', encoding="utf-8")
    (root / "script.sh").write_text(f"cd {forward}/work\n", encoding="utf-8")
    (root / "longer.txt").write_text(f"{native}-other is another folder\n", encoding="utf-8")
    (root / "binary.dat").write_bytes(b"\0" + native.encode())
    (root / "large.txt").write_text("x" * MAX_BYTES + native, encoding="utf-8")
    (root / "untracked.txt").write_text(native, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "notes.md", "config.json", "script.sh", "longer.txt", "binary.dat", "large.txt")
    found = scan(root, home)
    assert [line.partition(":")[0] for line in found] == ["config.json", "notes.md", "script.sh"]
    assert found[1].startswith("notes.md:2: ") and MASK in found[1]
    assert all(native not in line and forward not in line for line in found)
    if home.drive:
        (root / "shell.txt").write_text(f"/{home.drive[0].lower()}/" + "/".join(home.parts[1:]).upper() + "\n",
                                        encoding="utf-8")
        _git(root, "add", "shell.txt")
        assert any(line.startswith("shell.txt:1: ") for line in scan(root, home))


def test_a_home_folder_that_is_not_a_persons_own_is_not_looked_for():
    assert personal(PureWindowsPath("C:/Users/someone")) and not personal(PurePath("/root"))
