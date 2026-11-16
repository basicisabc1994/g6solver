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


def verbatim_junction(link: Path, substitute: str) -> None:
    """Make ``link`` (a new, empty folder) a Windows junction whose target is ``substitute`` exactly as written (an NT
    path such as ``\\??\\C:\\x\\..\\y``), which Windows may then be unable to follow. ``mklink /J`` would normalise
    it; setting the reparse point on a folder of one's own needs no privilege."""
    import ctypes
    from ctypes import wintypes

    link.mkdir()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                         wintypes.LPVOID]
    # GENERIC_WRITE, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
    handle = kernel32.CreateFileW(str(link), 0x40000000, 0, None, 3, 0x02000000 | 0x00200000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "open for a reparse point")
    try:
        name = substitute.encode("utf-16-le")
        shown = substitute.removeprefix("\\??\\").encode("utf-16-le")
        body = (0).to_bytes(2, "little") + len(name).to_bytes(2, "little") + (len(name) + 2).to_bytes(2, "little") \
            + len(shown).to_bytes(2, "little") + name + b"\0\0" + shown + b"\0\0"
        data = (0xA0000003).to_bytes(4, "little") + len(body).to_bytes(2, "little") + b"\0\0" + body
        buffer = ctypes.create_string_buffer(data, len(data))
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(handle, 0x000900A4, buffer, len(data), None, 0, ctypes.byref(returned),
                                        None):                                      # FSCTL_SET_REPARSE_POINT
            raise OSError(ctypes.get_last_error(), "FSCTL_SET_REPARSE_POINT")
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and \\\\?\\ paths")
@pytest.mark.parametrize("case", ["dot-dot", "trailing-dot", "not-yet-made", "under-a-junction"])
def test_a_junction_windows_cannot_follow_is_refused(home: Path, tmp_path: Path, case: str) -> None:
    # Path.resolve stops at a link it cannot follow and gives the link's own words joined with the rest, '..' and a
    # trailing dot kept; opening that value, Windows reads the words again and lands where the words lead. dot-dot:
    # 'link' -> <home>\nothere\..\..\outside. trailing-dot: 'link' -> <home>\sub. with 'sub' a junction to outside
    # (Windows drops the dot when opening). not-yet-made: the folder the words lead to does not exist yet, so a caller's
    # mkdir(parents=True) would make it outside. under-a-junction: 'link' -> <home>\x\nothere, plain words, with 'x' a
    # junction to outside and 'nothere' not made yet: the words stay inside, but a caller's mkdir goes through 'x'.
    root = home.resolve()
    outside = tmp_path.resolve() / "outside"
    if case == "trailing-dot":
        verbatim_junction(root / "sub", "\\??\\" + str(outside))
        verbatim_junction(root / "link", "\\??\\" + str(root) + "\\sub.")
    elif case == "under-a-junction":
        verbatim_junction(root / "x", "\\??\\" + str(outside))
        verbatim_junction(root / "link", "\\??\\" + str(root / "x" / "nothere"))
    else:
        leads_to = "outside" if case == "dot-dot" else "made_outside"
        verbatim_junction(root / "link", "\\??\\" + str(root / "nothere" / ".." / ".." / leads_to))
    for parts in (("link", "secret.txt"), ("link", "new", "kept.json")):
        with pytest.raises(ValueError, match="escapes"):
            state_path(*parts)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and \\\\?\\ paths")
def test_a_junction_named_with_a_trailing_space_is_refused(home: Path, tmp_path: Path) -> None:
    # Windows drops a trailing space from the last part of a path but keeps it on a part before it: a walk that asks
    # about '<home>\a ' as a last part asks about '<home>\a' (not there), while opening '<home>\a \secret.txt' goes
    # through the junction 'a ' (made through \\?\, which keeps the space)
    root = home.resolve()
    link = Path("\\\\?\\" + str(root / "a "))
    verbatim_junction(link, "\\??\\" + str(tmp_path.resolve() / "outside"))
    try:
        for parts in (("a ", "secret.txt"), ("a ", "new", "kept.json"), ("a /secret.txt",)):
            with pytest.raises(ValueError, match="escapes"):
                state_path(*parts)
    finally:
        os.rmdir(link)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drops a trailing space or dot from a name")
@pytest.mark.parametrize("parts", [("churn.", "a.json"), ("churn ", "a.json"), ("churn", "a.json."),
                                   ("churn", "a.json "), ("churn. /a.json",)])
def test_a_part_ending_in_a_space_or_a_dot_is_refused(home: Path, parts: tuple[str, ...]) -> None:
    # Windows reads such a name one way as the last part of a path and another way before it, so G6 refuses it
    with pytest.raises(ValueError, match="space or a dot"):
        state_path(*parts)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and \\\\?\\ paths")
def test_a_junction_windows_cannot_follow_whose_words_stay_inside_gives_the_words_as_open_reads_them(
        home: Path) -> None:
    # the value is what open reads: '..' taken, no \\?\ prefix, and no link on it, so opening it stays inside
    root = home.resolve()
    verbatim_junction(root / "link", "\\??\\" + str(root / "nothere" / ".." / "churn"))
    assert str(state_path("link", "a.json")) == str(root / "churn" / "a.json")


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
