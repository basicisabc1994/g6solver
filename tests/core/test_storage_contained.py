# SPDX-License-Identifier: Apache-2.0
"""``g6.core.storage.contained`` and ``plain_file``: the one check that a path a caller names stays inside a folder.

The archive's adversarial boundary suite (``workspace_manager``'s ``test_boundary_adversarial.py``), rewritten for the
shared helper: '..' at any depth, mixed separators, a symbolic link or a Windows junction out of the root, Unicode
forms and case on a case-insensitive file system; and the cases it did not have: a relative path joined onto the root,
never onto the folder G6 runs in (WORKSPACE_MANAGER-04), network and device paths refused before anything is
resolved, and a hard link, which resolving never shows. Each escape is refused; each path inside is allowed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from g6.core import storage
from g6.core.storage import PathRefused, contained, plain_file


def _junction(link: Path, target: Path) -> bool:
    if sys.platform != "win32":
        return False
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                          check=False)
    return done.returncode == 0


def _symlink(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "ws"
    folder.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("x", encoding="utf-8")
    return folder


@pytest.mark.parametrize("raw", ["../secret.txt", "../../../etc/passwd", "sub/../../secret.txt", "a/b/../../../x"])
def test_dot_dot_out_of_the_root_is_refused_at_any_depth(root: Path, raw: str) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, raw)


def test_mixed_separators_do_not_hide_a_traversal(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, str(root) + "/sub/../../secret.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="backslash separators are Windows paths")
def test_backslash_traversal_is_refused_on_windows(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "sub\\..\\..\\secret.txt")


def test_a_symbolic_link_out_of_the_root_counts_as_outside(root: Path) -> None:
    if not _symlink(root / "lnk", root.parent / "outside"):
        pytest.skip("this account may not make symbolic links")
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "lnk/secret.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows'")
def test_a_junction_out_of_the_root_counts_as_outside(root: Path) -> None:
    if not _junction(root / "jct", root.parent / "outside"):
        pytest.skip("junctions cannot be made here")
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "jct/secret.txt")
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "jct/new.txt")  # a file not yet there, under the junction


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and \\\\?\\ paths")
@pytest.mark.parametrize("case", ["dot-dot", "trailing-dot", "not-yet-made", "under-a-junction"])
def test_a_junction_windows_cannot_follow_out_of_the_root_counts_as_outside(root: Path, case: str) -> None:
    # resolving stops at a link it cannot follow and keeps its words ('..', a trailing dot, or a link on the way),
    # which Windows reads again when the value is opened: see test_storage_state_path's test of the same name
    from tests.core.test_storage_state_path import verbatim_junction

    base, outside = root.resolve(), root.parent.resolve() / "outside"
    if case == "trailing-dot":
        verbatim_junction(base / "sub", "\\??\\" + str(outside))
        verbatim_junction(base / "link", "\\??\\" + str(base) + "\\sub.")
    elif case == "under-a-junction":
        verbatim_junction(base / "x", "\\??\\" + str(outside))
        verbatim_junction(base / "link", "\\??\\" + str(base / "x" / "nothere"))
    else:
        leads_to = "outside" if case == "dot-dot" else "made_outside"
        verbatim_junction(base / "link", "\\??\\" + str(base / "nothere" / ".." / ".." / leads_to))
    for raw in ("link/secret.txt", "link/new/kept.json"):
        with pytest.raises(PathRefused, match="outside"):
            contained(root, raw)
        with pytest.raises(PathRefused, match="outside"):
            storage.confined_path(str(base / raw), (root,))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and \\\\?\\ paths")
def test_a_junction_named_with_a_trailing_space_out_of_the_root_counts_as_outside(root: Path) -> None:
    # Windows drops a trailing space from the last part of a path but keeps it on a part before it: a check that asks
    # about 'ws\a ' as a last part asks about 'ws\a', while opening 'ws\a \secret.txt' goes through the junction 'a '
    from tests.core.test_storage_state_path import verbatim_junction

    base, outside = root.resolve(), root.parent.resolve() / "outside"
    link = Path("\\\\?\\" + str(base / "a "))
    verbatim_junction(link, "\\??\\" + str(outside))
    try:
        for raw in ("a /secret.txt", "a \\new\\kept.json"):
            with pytest.raises(PathRefused):
                contained(root, raw)
            with pytest.raises(PathRefused):
                contained(root, raw, refuse_links=True)
            with pytest.raises(PathRefused):
                storage.confined_path(str(base) + "\\" + raw, (root,))
    finally:
        os.rmdir(link)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and \\\\?\\ paths")
def test_the_link_walk_sees_a_junction_named_with_a_trailing_space_in_the_roots_own_words(root: Path) -> None:
    # refuse_links asks about each part as open goes through it, so 'ws\a ' is asked about as 'a ', not 'a'
    from tests.core.test_storage_state_path import verbatim_junction

    base, outside = root.resolve(), root.parent.resolve() / "outside"
    link = Path("\\\\?\\" + str(base / "a "))
    verbatim_junction(link, "\\??\\" + str(outside))
    try:
        for raw in ("secret.txt", "new.txt"):
            with pytest.raises(PathRefused, match="linked"):
                contained(str(base) + "\\a ", raw, refuse_links=True)
    finally:
        os.rmdir(link)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' per-folder case-sensitive flag")
@pytest.mark.parametrize("made", [False, True])
def test_a_folder_named_as_the_root_but_for_case_is_outside_it(tmp_path: Path, made: bool) -> None:
    # in a case-sensitive folder 'WS' is not 'ws', though pathlib compares the two words as equal on Windows: the check
    # must ask the file system which folder the value reaches, not compare the words
    from tests.core.test_storage_state_path import make_case_sensitive

    parent = tmp_path.resolve() / "cs"
    make_case_sensitive(parent)
    try:
        root = parent / "ws"
        root.mkdir()
        if made:
            (parent / "WS").mkdir()
            (parent / "WS" / "secret.txt").write_text("x", encoding="utf-8")
        for raw in ("../WS/made/kept.json", "../WS/secret.txt", "../WS"):
            for refuse_links in (False, True):
                with pytest.raises(PathRefused, match="outside") as refused:
                    contained(root, raw, refuse_links=refuse_links)
                assert refused.value.outside
            with pytest.raises(PathRefused, match="outside"):
                storage.confined_path(str(parent / "WS" / raw.removeprefix("../WS").lstrip("/")), (root,))
        assert contained(root, "made/kept.json") == root / "made" / "kept.json"
    finally:
        shutil.rmtree(parent)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows delete-pending folders")
@pytest.mark.parametrize("refuse_links", [False, True])
def test_a_folder_kept_delete_pending_holds_nothing_and_is_answered_at_once(root: Path, refuse_links: bool) -> None:
    # every lstat below a folder marked for delete answers access denied while another program's handle keeps it.
    # Windows marks only an empty folder and makes nothing in a marked one, so nothing is below it: the path is inside
    # (never OUTSIDE_ROOT, never the raw PermissionError), answered without waiting
    import time

    from tests.core.test_storage_state_path import delete_pending

    base = root.resolve()
    with delete_pending(base / "dp"):
        started = time.perf_counter()
        given = contained(root, "dp/x.json", refuse_links=refuse_links)
        took = time.perf_counter() - started
        assert storage.confined_path(str(base / "dp" / "x.json"), (root,)) == base / "dp" / "x.json"
    assert given == base / "dp" / "x.json"
    assert took < 0.05, f"contained waited {took * 1000:.0f} ms"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows access-denied answers")
@pytest.mark.parametrize("refuse_links", [False, True])
def test_a_folder_that_cannot_be_read_is_refused_soon_as_input_not_as_outside(root: Path, refuse_links: bool,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    # a folder G6 may not read (it is not being deleted) could hide a link: after a brief second ask the path is refused
    # as one G6 cannot read now (a workspace says INPUT_INVALID), never as outside, never with the raw PermissionError
    import time

    base = root.resolve()
    (base / "locked").mkdir()
    real_lstat = os.lstat

    def denied(path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if str(base / "locked") + "\\" in str(path):
            raise OSError(13, "Access is denied", str(path), 5)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", denied)
    started = time.perf_counter()
    with pytest.raises(PathRefused, match="a folder on the path cannot be read now") as refused:
        contained(root, "locked/x.json", refuse_links=refuse_links)
    took = time.perf_counter() - started
    assert refused.value.outside is False
    assert took < 0.05, f"contained waited {took * 1000:.0f} ms"


@pytest.mark.parametrize("refuse_links", [False, True])
def test_paths_inside_are_never_refused_while_folders_on_them_are_removed_and_made(root: Path,
                                                                                 refuse_links: bool) -> None:
    # on Windows resolving a path while another thread removes and makes the folders on it can name a $Extend\$Deleted
    # file or come back with \\?\: the path is inside all the same, so contained looks again at the caller's words
    # joined onto the root, through the same check (state_path does the same)
    import threading

    base = root.resolve()
    stop = threading.Event()
    refused: list[str] = []

    def write(number: int) -> None:
        while not stop.is_set():
            try:
                shutil.rmtree(base / f"d{number}", ignore_errors=True)
                (base / f"d{number}" / "a" / "b").mkdir(parents=True, exist_ok=True)
                (base / f"d{number}" / "a" / "b" / "x.json").write_text("{}", encoding="utf-8")
            except OSError:                               # Windows: another thread holds the folder just now
                pass

    def ask(number: int) -> None:
        for _ in range(1000):
            raw = f"d{number}/a/b/x.json"
            try:
                found = contained(root, raw, refuse_links=refuse_links)
            except (PathRefused, OSError) as error:
                refused.append(f"{type(error).__name__}: {error}")
                continue
            if found != base / raw:
                refused.append(f"gave {found}")

    writers = [threading.Thread(target=write, args=(n,)) for n in range(2)]
    askers = [threading.Thread(target=ask, args=(n,)) for n in range(2)]
    for thread in writers + askers:
        thread.start()
    for thread in askers:
        thread.join()
    stop.set()
    for thread in writers:
        thread.join()
    assert (len(refused), refused[:3]) == (0, [])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drops a trailing space or dot from a name")
@pytest.mark.parametrize("raw", ["a.", "a ", "sub./x.txt", "sub /x.txt", "x.txt.", "sub\\x.txt ", "sub\\...\\x"])
def test_a_part_ending_in_a_space_or_a_dot_is_refused_as_input(root: Path, raw: str) -> None:
    # Windows reads such a name one way as the last part of a path and another way before it, so G6 refuses it
    with pytest.raises(PathRefused, match="space or a dot") as refused:
        contained(root, raw)
    assert refused.value.outside is False


@pytest.mark.skipif(sys.platform != "win32", reason="names Windows cannot hold")
@pytest.mark.parametrize("raw", ["a<b", "?", "x" * 300, "a<b/c.txt"])
def test_a_name_windows_cannot_hold_is_refused_at_once_as_input(root: Path, raw: str,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    # lstat answers such a name with an error that never clears: it is no folder being deleted, so it is not asked
    # again, and the path is refused as input G6 cannot use, not as outside the folder
    waits: list[float] = []
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    with pytest.raises(PathRefused) as refused:
        contained(root, raw)
    assert (refused.value.outside, waits) == (False, [])


@pytest.mark.parametrize("name", ["café.txt", unicodedata.normalize("NFD", "café.txt")])
def test_unicode_names_inside_are_allowed_in_either_form(root: Path, name: str) -> None:
    assert contained(root, name).parent == root.resolve()


def test_a_unicode_name_does_not_smuggle_a_traversal(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "../café-secret.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="the case-insensitive file system here is Windows'")
def test_a_root_spelled_in_another_case_is_the_same_folder(root: Path) -> None:
    assert contained(root, str(root).upper() + "\\file.txt").parent == root.resolve()


def test_a_folder_beside_the_root_in_another_case_is_still_outside(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, str(root.parent / "OUTSIDE" / "file.txt"))


def test_a_relative_path_is_joined_onto_the_root_not_the_working_folder(root: Path, tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    # WORKSPACE_MANAGER-04: the archive resolved Path(target) against the process's working folder
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert contained(root, "notes/a.txt") == root.resolve() / "notes" / "a.txt"


def test_an_absolute_path_is_allowed_only_inside(root: Path) -> None:
    assert contained(root, str(root / "a.txt")) == root.resolve() / "a.txt"
    assert contained(root, ".") == root.resolve()
    with pytest.raises(PathRefused, match="outside"):
        contained(root, str(root.parent / "outside" / "secret.txt"))


@pytest.mark.parametrize("raw", ["\\\\attacker.example\\share\\x.txt", "//attacker.example/share/x.txt",
                                 "\\\\?\\C:\\Windows\\x.txt", "\\\\.\\PhysicalDrive0", "\\??\\C:\\Windows\\x.txt",
                                 "/??/C:/Windows/x.txt"])
def test_a_network_or_device_path_is_refused_before_anything_is_resolved(root: Path, raw: str,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    # resolving \\host\share on Windows opens it, which reaches the host over SMB (and offers it a login); \??\ is the
    # NT form open takes as it is. Refused as outside on the words, in the same fixed words state_path gives, no path
    def never(self: Path, strict: bool = False) -> Path:
        raise AssertionError("resolved a network or device path")

    monkeypatch.setattr(Path, "resolve", never)
    with pytest.raises(PathRefused, match="network or device") as refused:
        contained(root, raw)
    assert refused.value.outside is True
    assert raw not in refused.value.reason
    with pytest.raises(PathRefused) as from_state_path:
        storage.state_path(raw)
    assert from_state_path.value.reason.endswith(refused.value.reason)


@pytest.mark.parametrize("system", ["nt", "posix"])
@pytest.mark.parametrize("raw", ["\\\\name", "\\??\\name", "//name", "/\\name", "\\/name"])
def test_a_network_or_device_form_is_read_as_the_system_reads_it(raw: str, system: str,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows reads '\\name' and '\??\name' as network or device paths; Linux and macOS read a backslash as a character
    # of a plain name, so there only '//name' (a path some systems take as a network one) is refused
    monkeypatch.setattr(storage.os, "name", system)
    why = storage._elsewhere(raw)
    monkeypatch.undo()
    assert (why is not None) == (system == "nt" or raw.startswith("//")), (raw, system, why)


@pytest.mark.skipif(os.name == "nt", reason="POSIX names")
@pytest.mark.parametrize("name", ["\\\\name", "\\??\\name"])
def test_a_posix_name_holding_backslashes_is_a_plain_name(root: Path, name: str) -> None:
    assert contained(root, name) == root.resolve() / name


@pytest.mark.parametrize("raw", ["", "  ", "a\x00b.txt", "a\nb.txt", "C:secret.txt", "notes.txt:hidden", "CON",
                                 "sub/nul.txt", "LPT1.log", "aux"])
def test_empty_control_drive_stream_and_device_names_are_refused(root: Path, raw: str) -> None:
    with pytest.raises(PathRefused):
        contained(root, raw)


def test_a_plain_file_is_one_kept_under_one_name(root: Path) -> None:
    (root / "a.txt").write_text("a", encoding="utf-8")
    assert plain_file(root / "a.txt").st_size == 1
    with pytest.raises(PathRefused, match="not a file"):
        plain_file(root / "missing.txt")
    with pytest.raises(PathRefused, match="not a regular file"):
        plain_file(root)


def test_a_hard_link_to_a_file_elsewhere_is_not_a_plain_file(root: Path) -> None:
    # resolving a hard link gives its own name, so containment alone never sees that it is a file kept elsewhere
    try:
        os.link(root.parent / "outside" / "secret.txt", root / "looks-local.txt")
    except OSError:
        pytest.skip("hard links cannot be made here")
    assert contained(root, "looks-local.txt") == root.resolve() / "looks-local.txt"
    with pytest.raises(PathRefused, match="link"):
        plain_file(root / "looks-local.txt")


def test_a_symbolic_link_is_not_a_plain_file(root: Path) -> None:
    (root / "a.txt").write_text("a", encoding="utf-8")
    if not _symlink(root / "b.txt", root / "a.txt"):
        pytest.skip("this account may not make symbolic links")
    with pytest.raises(PathRefused, match="link"):
        plain_file(root / "b.txt")


def test_state_path_still_refuses_an_escape_from_the_g6_home() -> None:
    with pytest.raises(ValueError, match="escapes"):
        storage.state_path("..", "elsewhere")


#: Names a check and open could read two ways on Windows: a stream (open reads 'j::$INDEX_ALLOCATION' as the folder
#: 'j', pathlib as the drive 'J:'), a control character, half of a UTF-16 character, a device, a trailing space or dot.
TWO_WAYS = ["j::$INDEX_ALLOCATION", "f.txt:s:$DATA", "a\x01b", "dp\udc80", "con.txt", "COM1.log", "nul", "x.", "x "]


def _asked_nothing(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every lstat and resolve; the input gate must refuse before either is asked."""
    asked: list[str] = []
    real_lstat, real_resolve = os.lstat, Path.resolve
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: asked.append(f"lstat {p}") or real_lstat(p, *a, **k))
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: asked.append(f"resolve {self}")
                        or real_resolve(self, strict))
    return asked


@pytest.mark.skipif(sys.platform != "win32", reason="names Windows reads two ways")
@pytest.mark.parametrize("name", TWO_WAYS)
def test_a_name_read_two_ways_is_refused_before_the_file_system_is_asked(root: Path, name: str,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    # one input gate in every entry point, on the caller's words, before any resolve or lstat: G6's own reason, never a
    # path, never refused as outside; each name sits below another in a relative path ('j::$INDEX_ALLOCATION' first
    # is a drive-qualified path, refused as outside)
    base = root.resolve()
    asked = _asked_nothing(monkeypatch)
    calls = [lambda: contained(root, f"sub/{name}/x.json"),
             lambda: contained(root, f"sub/{name}/x.json", refuse_links=True),
             lambda: contained(root, f"{base}\\{name}\\x.json"),
             lambda: contained(root, f"{base}\\{name}\\x.json", refuse_links=True),
             lambda: storage.confined_path(f"{base}\\{name}\\x.json", (root,))]
    for call in calls:
        with pytest.raises(PathRefused) as refused:
            call()
        assert refused.value.outside is False
        assert str(base) not in refused.value.reason and name not in refused.value.reason, refused.value.reason
    assert asked == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letters")
@pytest.mark.parametrize("refuse_links", [False, True])
def test_an_absolute_path_on_another_drive_is_outside_before_the_file_system_is_asked(
        root: Path, refuse_links: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    base = root.resolve()
    other = "Q:" if base.drive.upper() != "Q:" else "R:"
    asked = _asked_nothing(monkeypatch)
    with pytest.raises(PathRefused) as refused:
        contained(root, other + str(base)[2:] + "\\x.json", refuse_links=refuse_links)
    assert (refused.value.outside, asked) == (True, [])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letters")
@pytest.mark.parametrize("raw", ["C:x", "C:", "C:..\\x", "c:secret.txt", "j::$INDEX_ALLOCATION\\x.json"])
@pytest.mark.parametrize("refuse_links", [False, True])
def test_a_drive_qualified_relative_path_is_outside_before_the_file_system_is_asked(
        root: Path, raw: str, refuse_links: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows reads 'C:x' against that drive's current folder, never the root: one rule with state_path, outside, on
    # the words
    asked = _asked_nothing(monkeypatch)
    with pytest.raises(PathRefused) as refused:
        contained(root, raw, refuse_links=refuse_links)
    assert (refused.value.outside, asked) == (True, []), refused.value.reason
    with pytest.raises(PathRefused) as by_state_path:
        storage.state_path(raw)
    assert by_state_path.value.outside is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions and stream names")
@pytest.mark.parametrize("stream",["j::$INDEX_ALLOCATION", "j:$I30:$INDEX_ALLOCATION"])
def test_a_stream_name_on_a_one_letter_junction_out_of_the_root_is_refused(root: Path, stream: str) -> None:
    # open reads '<root>\j::$INDEX_ALLOCATION\secret.txt' as '<root>\j\secret.txt' and goes through the junction;
    # pathlib reads the part as the drive 'J:', so a walk over pathlib's parts never saw the junction
    base = root.resolve()
    if not _junction(base / "j", root.parent / "outside"):
        pytest.skip("junctions cannot be made here")
    try:
        raw = f"{base}\\{stream}\\secret.txt"
        for call in (lambda: contained(root, raw), lambda: contained(root, raw, refuse_links=True),
                     lambda: storage.confined_path(raw, (root,))):
            with pytest.raises(PathRefused):
                call()
    finally:
        os.rmdir(base / "j")


class _Interrupted(os.PathLike):
    def __fspath__(self) -> str:
        raise KeyboardInterrupt


@pytest.mark.parametrize("value", [None, b"x.json", 1], ids=["none", "bytes", "int"])
@pytest.mark.parametrize("call", ["contained", "refuse_links", "confined_path"])
def test_a_value_that_is_not_text_is_refused_in_g6s_words(root: Path, value: object, call: str) -> None:
    # a caller that hands None, bytes or a number gets a refusal in G6's words, not a raw TypeError (nor, for bytes,
    # "the path is empty"); an interrupt while the value is read is not caught
    calls = {"contained": lambda v: contained(root, v), "refuse_links": lambda v: contained(root, v, refuse_links=True),
             "confined_path": lambda v: storage.confined_path(v, (root,))}
    with pytest.raises(PathRefused, match="not text") as refused:
        calls[call](value)
    assert refused.value.outside is False
    with pytest.raises(KeyboardInterrupt):
        calls[call](_Interrupted())


class _Fails(os.PathLike):
    """A path object whose ``__fspath__`` raises the caller's own error."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __fspath__(self) -> str:
        raise self.error


@pytest.mark.parametrize("error", [ValueError("zz-caller"), OSError("zz-caller"), RuntimeError("zz-caller"),
                                   TypeError("zz-caller")], ids=["valueerror", "oserror", "runtimeerror", "typeerror"])
@pytest.mark.parametrize("call", ["contained", "refuse_links", "confined_path"])
def test_a_path_object_that_raises_is_refused_as_not_read_as_text(root: Path, error: Exception, call: str) -> None:
    # the caller's own error while its path object is read is not the file system's answer, and not raw: refused in
    # G6's words, not as outside
    calls = {"contained": lambda v: contained(root, v), "refuse_links": lambda v: contained(root, v, refuse_links=True),
             "confined_path": lambda v: storage.confined_path(v, (root,))}
    with pytest.raises(PathRefused) as refused:
        calls[call](_Fails(error))
    assert refused.value.outside is False
    assert "could not be read as text" in refused.value.reason
    assert "file system" not in refused.value.reason and "zz-" not in refused.value.reason, refused.value.reason


#: What an entry point may meet while it decides: an OS error (WinError 87, a stream name lstat cannot take), a
#: ValueError (a NUL) and a UnicodeError (half of a UTF-16 character encoded for Windows).
ERRORS = [OSError(22, "zz-os-words", None, 87), ValueError("zz-python-words"),
          UnicodeEncodeError("utf-16-le", "\udc80", 0, 1, "zz-codec-words")]


@pytest.mark.parametrize("where", ["lstat", "resolve"])
@pytest.mark.parametrize("error", ERRORS, ids=["oserror", "valueerror", "unicodeerror"])
@pytest.mark.parametrize("call", ["contained", "refuse_links", "confined_path"])
def test_an_error_while_deciding_is_refused_in_g6s_words(root: Path, where: str, error: Exception, call: str,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    # no raw OSError, ValueError or UnicodeError reaches a caller: a PathRefused in G6's words, not as outside (nothing
    # decided that the path is outside)
    base = root.resolve()
    (base / "sub").mkdir()

    def fails(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(*((os, "lstat") if where == "lstat" else (Path, "resolve")), fails)
        try:
            if call == "confined_path":
                storage.confined_path(str(base / "sub" / "x.json"), (base,))
            else:
                contained(base, "sub/x.json", refuse_links=call == "refuse_links")
        except PathRefused as refused:
            caught: Exception = refused
        except Exception as raw:  # noqa: BLE001
            caught = raw
        else:
            caught = AssertionError("nothing refused")
    assert type(caught) is PathRefused, f"{call} let out {type(caught).__name__}: {caught}"
    assert caught.outside is False and "zz-" not in caught.reason, caught.reason
