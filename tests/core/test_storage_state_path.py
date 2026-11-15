# SPDX-License-Identifier: Apache-2.0
"""``g6.core.storage.state_path`` under file churn, and the escapes it must still refuse.

On Windows, resolving a whole path while another thread deletes or makes a file or folder on it can come back as a
``\\\\?\\`` path or a ``$Extend\\$Deleted`` path, neither under the G6 home, so a path inside it was refused as escaping.
The churn test makes that happen many times; the others pin what must still be refused and the value callers get.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from g6.core.storage import state_path

WRITERS, READERS, FILES, RESOLVES = 4, 4, 4, 2500


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    (folder / "churn").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("G6_HOME", str(folder))
    return folder


def _link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32" and target.is_dir():
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                              check=False)
        return done.returncode == 0
    return False


def test_files_and_folders_being_made_and_removed_are_never_refused_as_escaping(home: Path) -> None:
    # the writers delete and make files (as a lock or a temporary file is) and folders (as a store's first write makes
    # its folders); the readers ask for paths to both, and every answer must be the path inside the home
    names = [f"f{n}.json" for n in range(FILES)]
    for name in names:
        (home / "churn" / name).write_text("{}", encoding="utf-8")
    stop = threading.Event()
    refused: list[str] = []
    wrong: list[Path] = []

    def write(number: int) -> None:
        count = 0
        while not stop.is_set():
            count += 1
            try:
                if number % 2:
                    shutil.rmtree(home / f"d{number}", ignore_errors=True)
                    (home / f"d{number}" / "a" / "b").mkdir(parents=True, exist_ok=True)
                else:
                    target = home / "churn" / names[count % FILES]
                    target.unlink(missing_ok=True)
                    target.write_text(str(count), encoding="utf-8")
            except OSError:                               # Windows: another writer holds the file or folder just now
                pass

    def read(number: int) -> None:
        root = home.resolve()
        for count in range(RESOLVES):
            if count % 2:
                parts = (f"d{1 + 2 * (count % (WRITERS // 2))}", "a", "b", "x.json")
            else:
                parts = ("churn", names[(number + count) % FILES])
            try:
                found = state_path(*parts)
            except ValueError as error:
                refused.append(str(error))
                continue
            if found != root.joinpath(*parts):
                wrong.append(found)

    writers = [threading.Thread(target=write, args=(n,)) for n in range(WRITERS)]
    readers = [threading.Thread(target=read, args=(n,)) for n in range(READERS)]
    for thread in writers + readers:
        thread.start()
    for thread in readers:
        thread.join()
    stop.set()
    for thread in writers:
        thread.join()
    assert (len(refused), refused[:3]) == (0, [])
    assert (len(wrong), wrong[:3]) == (0, [])


def test_the_value_is_the_resolved_absolute_path_under_the_resolved_home(home: Path) -> None:
    (home / "churn" / "a.json").write_text("{}", encoding="utf-8")
    root = home.resolve()
    assert state_path() == root
    assert state_path("churn", "a.json") == root / "churn" / "a.json" == (home / "churn" / "a.json").resolve()
    assert state_path("churn", "missing", "b.json") == root / "churn" / "missing" / "b.json"
    assert state_path("churn/a.json") == root / "churn" / "a.json"
    assert state_path("churn", "..") == root
    assert state_path("churn", "..", "churn", "a.json") == root / "churn" / "a.json"
    assert state_path("churn", "a.json").is_absolute()


@pytest.mark.parametrize("parts", [("..",), ("..", "outside", "secret.txt"), ("churn", "..", "..", "x"),
                                   ("churn", "..", ".."), ("a", "b", "..", "..", "..", "x")])
def test_dot_dot_out_of_the_home_is_refused(home: Path, parts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="escapes"):
        state_path(*parts)


def test_an_absolute_part_outside_the_home_is_refused(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        state_path(str(tmp_path / "outside" / "secret.txt"))
    with pytest.raises(ValueError, match="escapes"):
        state_path("churn", str(tmp_path / "outside"))


def test_a_link_inside_the_home_to_outside_is_refused(home: Path, tmp_path: Path) -> None:
    if not _link(home / "churn" / "out", tmp_path / "outside"):
        pytest.skip("neither a symbolic link nor a junction can be made here")
    for parts in (("churn", "out"), ("churn", "out", "secret.txt"), ("churn", "out", "new.txt"),
                  ("churn", "out", "sub", "new.txt")):
        with pytest.raises(ValueError, match="escapes"):
            state_path(*parts)


def test_a_file_link_inside_the_home_to_a_file_outside_is_refused(home: Path, tmp_path: Path) -> None:
    try:
        os.symlink(tmp_path / "outside" / "secret.txt", home / "churn" / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this account may not make symbolic links")
    with pytest.raises(ValueError, match="escapes"):
        state_path("churn", "secret.txt")


def test_a_link_inside_the_home_to_inside_it_resolves_to_its_target(home: Path) -> None:
    (home / "kept").mkdir()
    if not _link(home / "churn" / "in", home / "kept"):
        pytest.skip("neither a symbolic link nor a junction can be made here")
    assert state_path("churn", "in") == home.resolve() / "kept"
    assert state_path("churn", "in", "a.json") == home.resolve() / "kept" / "a.json"


def test_a_home_reached_through_a_link_is_its_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # the home is resolved first, so a G6_HOME that is itself a link names the folder it points to, and paths under
    # that folder are inside
    (tmp_path / "real-home").mkdir()
    if not _link(tmp_path / "home-link", tmp_path / "real-home"):
        pytest.skip("neither a symbolic link nor a junction can be made here")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home-link"))
    real = (tmp_path / "real-home").resolve()
    assert state_path() == real
    assert state_path("a", "b.json") == real / "a" / "b.json"
    with pytest.raises(ValueError, match="escapes"):
        state_path("..", "x")
