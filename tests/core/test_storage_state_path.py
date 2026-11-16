# SPDX-License-Identifier: Apache-2.0
"""``g6.core.storage.state_path`` under file churn, and the escapes it must still refuse.

On Windows, resolving a whole path while another thread deletes or makes a file or folder on it can come back as a
``\\\\?\\`` path or a ``$Extend\\$Deleted`` path, neither under the G6 home, so a path inside it was refused as escaping.
The churn test makes that happen many times; the others pin what must still be refused and the value callers get.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
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


def make_case_sensitive(folder: Path) -> None:
    """Make ``folder`` (new, empty, in the test's own temporary folder) case-sensitive: Windows' per-folder flag, which
    any user may set on a folder of their own; folders WSL makes have it, and folders made in one inherit it. There
    ``home`` and ``HOME`` are two folders. The test removes ``folder`` at its end."""
    folder.mkdir()
    done = subprocess.run(["fsutil", "file", "setCaseSensitiveInfo", str(folder), "enable"], capture_output=True,
                          text=True, check=False)
    if done.returncode != 0:
        shutil.rmtree(folder)
        pytest.skip(f"the case-sensitive flag cannot be set here: {done.stdout.strip()} {done.stderr.strip()}")


@pytest.fixture
def case_sensitive(tmp_path: Path) -> Iterator[Path]:
    """A case-sensitive folder of the test's own, removed at the end."""
    folder = tmp_path.resolve() / "cs"
    make_case_sensitive(folder)
    yield folder
    shutil.rmtree(folder)


@contextlib.contextmanager
def delete_pending(folder: Path) -> Iterator[None]:
    """``folder`` (made here) marked for delete while a handle, standing for another program's, keeps it open: until
    the handle closes, Windows answers access denied (5) for it and for every path below it. It goes at the end."""
    import ctypes
    from ctypes import wintypes

    folder.mkdir()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    # DELETE | FILE_READ_ATTRIBUTES, share all, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS
    handle = kernel32.CreateFileW(str(folder), 0x00010000 | 0x80, 7, None, 3, 0x02000000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "open for delete")
    try:
        flag = ctypes.c_byte(1)
        if not kernel32.SetFileInformationByHandle(handle, 4, ctypes.byref(flag), 1):   # FileDispositionInfo
            raise OSError(ctypes.get_last_error(), "mark for delete")
        yield
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' per-folder case-sensitive flag")
@pytest.mark.parametrize("made", [False, True])
@pytest.mark.parametrize("home_made", [True, False])
def test_a_folder_named_as_the_home_but_for_case_is_outside_it(case_sensitive: Path, monkeypatch: pytest.MonkeyPatch,
                                                                made: bool, home_made: bool) -> None:
    # in a case-sensitive folder 'HOME' is not 'home', though pathlib compares the two words as equal on Windows: the
    # check must ask the file system which folder the value reaches, not compare the words
    if home_made:
        (case_sensitive / "home").mkdir()
    if made:
        (case_sensitive / "HOME").mkdir()
    monkeypatch.setenv("G6_HOME", str(case_sensitive / "home"))
    for parts in (("..", "HOME", "made", "kept.json"), ("..", "HOME"), ("..", "HOME", "kept.json")):
        with pytest.raises(ValueError, match="escapes"):
            state_path(*parts)
    assert sorted(os.listdir(case_sensitive)) == [name for name, there in (("HOME", made), ("home", home_made)) if there]
    assert state_path("made", "kept.json") == (case_sensitive / "home").resolve() / "made" / "kept.json"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows delete-pending folders")
def test_a_folder_kept_delete_pending_holds_nothing_and_is_answered_at_once(home: Path) -> None:
    # a folder marked for delete stays while another program's handle is open, and every lstat below it answers access
    # denied for as long (a folder being removed does so too, for tens of ms here). Windows marks only an empty folder
    # and makes nothing in a marked one, so nothing is below it: the path is inside, answered without waiting
    root = home.resolve()
    with delete_pending(root / "dp"):
        started = time.perf_counter()
        given = state_path("dp", "x.json")
        took = time.perf_counter() - started
    assert given == root / "dp" / "x.json"
    assert took < 0.05, f"state_path waited {took * 1000:.0f} ms"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows access-denied answers")
def test_a_folder_that_cannot_be_read_is_refused_soon_as_unreadable_not_as_escaping(
        home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # a folder G6 may not read (it is not being deleted) could hide a link: after a brief second ask it is refused, as
    # a path G6 cannot read now, not as one escaping the home
    root = home.resolve()
    (root / "locked").mkdir()
    real_lstat = os.lstat

    def denied(path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if str(root / "locked") + "\\" in str(path):
            raise OSError(13, "Access is denied", str(path), 5)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", denied)
    started = time.perf_counter()
    with pytest.raises(ValueError, match="a folder on the path cannot be read now") as refused:
        state_path("locked", "x.json")
    took = time.perf_counter() - started
    assert "escapes" not in str(refused.value) and "locked" not in str(refused.value), str(refused.value)
    assert took < 0.05, f"state_path waited {took * 1000:.0f} ms"


def test_a_refusal_after_resolving_is_in_g6s_fixed_words_not_the_parts(home: Path, tmp_path: Path) -> None:
    # every state_path refusal is in G6's fixed words: never the parts' text, which may name a person's file
    from g6.core.storage import PathRefused

    if not _link(home / "churn" / "out", tmp_path / "outside"):
        pytest.skip("neither a symbolic link nor a junction can be made here")
    with pytest.raises(PathRefused, match="escapes G6_HOME") as refused:
        state_path("churn", "out", "private-name.json")
    assert refused.value.outside is True
    assert "private-name" not in refused.value.reason and "churn" not in refused.value.reason, refused.value.reason


@pytest.mark.skipif(sys.platform != "win32", reason="names Windows cannot hold")
def test_a_name_windows_cannot_hold_is_refused_in_g6s_fixed_words(home: Path) -> None:
    from g6.core.storage import PathRefused

    with pytest.raises(PathRefused, match="not one Windows can hold") as refused:
        state_path("churn", "private<name.json")
    assert refused.value.outside is False and "private" not in refused.value.reason, refused.value.reason


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


#: Names a check and open could read two ways on Windows (see test_storage_contained's TWO_WAYS); a NUL everywhere.
TWO_WAYS = ["a\x00b"] + (["j::$INDEX_ALLOCATION", "f.txt:s:$DATA", "a\x01b", "dp\udc80", "con.txt", "COM1.log", "nul",
                          "x.", "x "] if sys.platform == "win32" else [])


def _asked_nothing(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every lstat and resolve; a refusal on the words must come before either is asked."""
    asked: list[str] = []
    real_lstat, real_resolve = os.lstat, Path.resolve
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: asked.append(f"lstat {p}") or real_lstat(p, *a, **k))
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: asked.append(f"resolve {self}")
                        or real_resolve(self, strict))
    return asked


@pytest.mark.parametrize("name", TWO_WAYS)
def test_a_name_read_two_ways_is_refused_before_the_file_system_is_asked(home: Path, name: str,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    # the input gate refuses before any resolve or lstat, in G6's words (never Python's), naming no path; each name sits
    # below another in its part ('j::$INDEX_ALLOCATION' as a whole part is a drive-qualified part, refused as outside)
    from g6.core.storage import PathRefused

    asked = _asked_nothing(monkeypatch)
    for parts in ((f"churn/{name}", "x.json"), ("churn", f"sub/{name}"), (f"churn/{name}/x.json",)):
        with pytest.raises(PathRefused, match="G6_HOME") as refused:
            state_path(*parts)
        assert refused.value.outside is False
        assert str(home) not in refused.value.reason and name not in refused.value.reason, refused.value.reason
    assert asked == []


#: Parts that are not names below the home: absolute, rooted, drive-qualified, network, device or \\?\ forms, and
#: parts that split into an empty, '.' or '..' name. Joined onto the home, each either replaces it or leaves it.
ELSEWHERE = ["", ".", "..", "a/", "a//b", "a/./b", "a/../b", "/rooted", "//host/share/x"] + (
    ["C:", "C:x", "j::$INDEX_ALLOCATION", "C:\\elsewhere", "\\rooted", "\\\\host\\share\\x", "\\\\.\\pipe\\x",
     "//./pipe/x", "\\\\?\\C:\\x", "\\??\\C:\\x", "a\\..\\b", "a\\\\b"] if sys.platform == "win32" else [])


@pytest.mark.parametrize("part", ELSEWHERE)
def test_a_part_that_is_not_names_below_the_home_is_refused_as_outside_on_its_words(
        home: Path, part: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # decided on the words alone, so refused as outside before anything is joined, resolved or asked about, in G6's
    # fixed words (never the part's)
    from g6.core.storage import PathRefused

    asked = _asked_nothing(monkeypatch)
    for parts in ((part, "x.json"), ("churn", part)):
        with pytest.raises(PathRefused, match="escapes G6_HOME") as refused:
            state_path(*parts)
        assert refused.value.outside is True, refused.value.reason
        assert len(part) < 3 or part not in refused.value.reason, refused.value.reason
    assert asked == []


@pytest.fixture
def pipe() -> Iterator[tuple[str, Callable[[], bool]]]:
    """A local named pipe served here (no network), and a check of whether anything opened it."""
    import ctypes
    import secrets
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR] + [wintypes.DWORD] * 6 + [wintypes.LPVOID]
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    name = "\\\\.\\pipe\\g6test-" + secrets.token_hex(6)
    # PIPE_ACCESS_DUPLEX; PIPE_TYPE_BYTE | PIPE_NOWAIT; one instance
    handle = kernel32.CreateNamedPipeW(name, 3, 1, 1, 512, 512, 0, None)
    assert handle not in (None, wintypes.HANDLE(-1).value)

    def opened() -> bool:
        # ERROR_PIPE_CONNECTED (535) or ERROR_NO_DATA (232: opened and closed); 536 means never opened
        return bool(kernel32.ConnectNamedPipe(handle, None)) or ctypes.get_last_error() in (535, 232)

    yield name, opened
    kernel32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes")
@pytest.mark.parametrize("form", ["device", "forward slashes", "after a plain part"])
def test_a_device_part_is_refused_as_outside_and_never_opened(home: Path, pipe: tuple[str, Callable[[], bool]], form: str) -> None:
    from g6.core.storage import PathRefused

    name, opened = pipe
    parts = {"device": (name,), "forward slashes": (name.replace("\\", "/"),), "after a plain part": ("churn", name)}
    with pytest.raises(PathRefused) as refused:
        state_path(*parts[form])
    assert refused.value.outside is True, refused.value.reason
    assert not opened(), "state_path opened the pipe while it decided"


def _two_faced(real: str, shown: str, after: int) -> str:
    """A str whose value is ``real``, but whose own methods show the checks ``shown``; pathlib's parse
    (``replace('/', '\\\\')``) sees ``shown`` for its first ``after`` calls, then ``real``."""
    class TwoFaced(str):
        parses = 0

        def replace(self, old: str, new: str, *rest: int) -> str:  # type: ignore[override]
            if (old, new) == ("/", "\\"):
                TwoFaced.parses += 1
                return shown if TwoFaced.parses <= after else str.replace(str(self), old, new, *rest)
            return str.replace(shown, old, new, *rest)

        def __getitem__(self, key):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN204
            return shown[:4] if key == slice(None, 4, None) else str.__getitem__(self, key)

        def strip(self, *chars: str) -> str:  # type: ignore[override]
            return shown.strip(*chars)

    return TwoFaced(real)


#: Every entry point that takes a caller's path.
ENTRY_POINTS = ["state_path", "state_path after a plain part", "contained", "contained refuse_links", "confined_path",
                "Workspace.read", "code existing"]


def _enter(call: str, root: Path, raw: object) -> object:
    from g6.code import _roots
    from g6.core import storage
    from g6.workspace._files import Workspace

    return {"state_path": lambda: state_path(raw), "state_path after a plain part": lambda: state_path("churn", raw),
            "contained": lambda: storage.contained(root, raw),
            "contained refuse_links": lambda: storage.contained(root, raw, refuse_links=True),
            "confined_path": lambda: storage.confined_path(raw, (root,)),
            "Workspace.read": lambda: Workspace(root).read(raw),
            "code existing": lambda: _roots.existing(raw, _roots.Roots((root,), "test"))}[call]()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes")
@pytest.mark.parametrize("after", range(5))
@pytest.mark.parametrize("call", ENTRY_POINTS)
def test_a_str_subclass_cannot_show_the_checks_one_path_and_open_another(
        home: Path, tmp_path: Path, pipe: tuple[str, Callable[[], bool]], call: str, after: int) -> None:
    # each entry point reads the value once, as a plain str, so every check and the open read the same characters: a
    # str subclass whose methods show the checks a plain name never has its device path joined, resolved or opened
    from g6.code._types import CodeRefused
    from g6.workspace._envelope import WorkspaceRefused

    name, opened = pipe
    root = (tmp_path / "root").resolve()
    root.mkdir()
    with contextlib.suppress(ValueError, WorkspaceRefused, CodeRefused):
        _enter(call, root, _two_faced(name, "plain", after))
    assert not opened(), f"{call} opened the device path while it decided (switch after {after} parses)"


#: Run in a fresh interpreter: a str subclass that records every method of its own that runs, handed to each entry
#: point. ``_text`` reads it without running any of them and gives a plain str; nothing after it may run one either.
_NO_METHOD_RUNS = r"""
import json, sys
from pathlib import Path
from g6.core import storage
from g6.code import _roots
from g6.workspace._files import Workspace

ran = []
KEEP = {"__new__", "__init__", "__init_subclass__", "__subclasshook__", "__getattribute__", "__setattr__",
        "__delattr__", "__class__", "__dir__", "__reduce__", "__reduce_ex__", "__getstate__", "__sizeof__",
        "__getnewargs__"}

def spy(name):
    real = getattr(str, name)
    def method(self, *args, **kwargs):
        ran.append(name)
        return real(self, *args, **kwargs)
    return method

Spy = type("Spy", (str,), {n: spy(n) for n in dir(str) if callable(getattr(str, n)) and n not in KEEP})
root = Path(sys.argv[1]).resolve()
calls = {
    "_text": lambda v: type(storage._text(Spy(v))) is str or "not a plain str",
    "state_path": lambda v: storage.state_path(Spy(v)),
    "state_path after a plain part": lambda v: storage.state_path("churn", Spy(v)),
    "contained": lambda v: storage.contained(root, Spy(v)),
    "contained refuse_links": lambda v: storage.contained(root, Spy(v), refuse_links=True),
    "confined_path": lambda v: storage.confined_path(Spy(str(root / v)), (root,)),
    "Workspace.read": lambda v: Workspace(root).read(Spy(v)),
    "code existing": lambda v: _roots.existing(Spy(str(root / v)), _roots.Roots((root,), "test")),
}
out = {}
for call, run in calls.items():
    for value in ("a.json", "..\\x", "con.txt"):
        del ran[:]
        try:
            answer = run(value)
        except Exception as error:
            answer = type(error).__name__
        if ran or answer == "not a plain str":
            out[f"{call} {value}"] = [answer if isinstance(answer, str) else "", sorted(set(ran))]
print(json.dumps(out))
"""


def test_no_method_of_a_str_subclass_runs_after_the_value_is_read(home: Path, tmp_path: Path) -> None:
    import json

    root = tmp_path / "root"
    root.mkdir()
    done = subprocess.run([sys.executable, "-X", "utf8", "-c", _NO_METHOD_RUNS, str(root)], capture_output=True,
                          text=True, check=False, env={**os.environ, "G6_HOME": str(home)}, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout) == {}, done.stdout


class _Interrupted(os.PathLike):
    def __fspath__(self) -> str:
        raise KeyboardInterrupt


@pytest.mark.parametrize("value", [None, b"x.json", 1], ids=["none", "bytes", "int"])
def test_a_part_that_is_not_text_is_refused_in_g6s_words(home: Path, value: object) -> None:
    # not a raw TypeError; an interrupt while a part is read is not caught
    from g6.core.storage import PathRefused

    for parts in ((value,), ("churn", value)):
        with pytest.raises(PathRefused, match="not text") as refused:
            state_path(*parts)
        assert refused.value.outside is False and "G6_HOME" in refused.value.reason
    with pytest.raises(KeyboardInterrupt):
        state_path("churn", _Interrupted())


class _Fails(os.PathLike):
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __fspath__(self) -> str:
        raise self.error


@pytest.mark.parametrize("error", [ValueError("zz-caller"), OSError("zz-caller"), RuntimeError("zz-caller"),
                                   TypeError("zz-caller")], ids=["valueerror", "oserror", "runtimeerror", "typeerror"])
def test_a_part_whose_path_object_raises_is_refused_as_not_read_as_text(home: Path, error: Exception) -> None:
    # the caller's own error is not "the file system could not answer", and never raw
    from g6.core.storage import PathRefused

    for parts in ((_Fails(error),), ("churn", _Fails(error))):
        with pytest.raises(PathRefused, match="could not be read as text") as refused:
            state_path(*parts)
        assert refused.value.outside is False and "G6_HOME" in refused.value.reason
        assert "file system" not in refused.value.reason and "zz-" not in refused.value.reason, refused.value.reason


@pytest.mark.parametrize("where", ["lstat", "resolve"])
@pytest.mark.parametrize("error", [OSError(22, "zz-os-words", None, 87), ValueError("zz-python-words"),
                                   UnicodeEncodeError("utf-16-le", "\udc80", 0, 1, "zz-codec-words")],
                         ids=["oserror", "valueerror", "unicodeerror"])
def test_an_error_while_deciding_is_refused_in_g6s_words(home: Path, where: str, error: Exception,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core.storage import PathRefused

    def fails(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(*((os, "lstat") if where == "lstat" else (Path, "resolve")), fails)
        try:
            state_path("churn", "x.json")
        except Exception as raw:  # noqa: BLE001
            caught = raw
        else:
            caught = AssertionError("nothing refused")
    assert type(caught) is PathRefused, f"state_path let out {type(caught).__name__}: {caught}"
    assert caught.outside is False and "zz-" not in caught.reason and "G6_HOME" in caught.reason, caught.reason
