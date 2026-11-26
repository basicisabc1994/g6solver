# SPDX-License-Identifier: Apache-2.0
"""A link on a path that leads to a device is refused before anything goes through it (review-sfu2#2).

A mount point (made in any folder one can write, with no privilege) may target ``\\??\\PIPE\\``: opening
``<mount point>\\<name>`` then connects to the named pipe ``\\\\.\\pipe\\<name>``, a client connection its server sees.
:func:`g6.core.storage.contained` resolved such a path, and :func:`g6.core.storage.plain_file` lstat'ed it, before
refusing, so the pipe was connected first. Both now read each link on the path and refuse one whose target is a network
or device path, as outside, before going through it. The pipe here is local (no network); every mount point is removed
(the link only) at teardown.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows mount points and named pipes")

PIPE_DEVICE = "\\??\\PIPE\\"


def _set_mount_point(folder: Path, substitute: str) -> None:
    """Make the empty ``folder`` a mount point whose target is the NT path ``substitute`` exactly as written."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                         wintypes.LPVOID]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    # GENERIC_WRITE, share all, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
    handle = kernel32.CreateFileW(str(folder), 0x40000000, 7, None, 3, 0x02000000 | 0x00200000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "open for a reparse point")
    try:
        name = substitute.encode("utf-16-le")
        body = (0).to_bytes(2, "little") + len(name).to_bytes(2, "little") + (len(name) + 2).to_bytes(2, "little") \
            + (0).to_bytes(2, "little") + name + b"\0\0" + b"\0\0"
        data = (0xA0000003).to_bytes(4, "little") + len(body).to_bytes(2, "little") + b"\0\0" + body
        buffer = ctypes.create_string_buffer(data, len(data))
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(handle, 0x000900A4, buffer, len(data), None, 0, ctypes.byref(returned),
                                        None):                                      # FSCTL_SET_REPARSE_POINT
            raise OSError(ctypes.get_last_error(), "FSCTL_SET_REPARSE_POINT")
    finally:
        kernel32.CloseHandle(handle)


@pytest.fixture
def mount_points() -> Iterator[Callable[[Path, str], Path]]:
    made: list[Path] = []

    def make(folder: Path, substitute: str) -> Path:
        folder.mkdir()
        made.append(folder)
        _set_mount_point(folder, substitute)
        return folder

    yield make
    for link in reversed(made):
        try:
            os.rmdir("\\\\?\\" + str(link))
        except FileNotFoundError:
            pass


@pytest.fixture
def pipe() -> Iterator[tuple[str, Callable[[], bool]]]:
    """A local named pipe served here (no network): its name below ``\\\\.\\pipe\\``, and whether anything opened it."""
    import ctypes
    import secrets
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR] + [wintypes.DWORD] * 6 + [wintypes.LPVOID]
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    leaf = "g6test-" + secrets.token_hex(6)
    # PIPE_ACCESS_DUPLEX; PIPE_TYPE_BYTE | PIPE_NOWAIT; one instance
    handle = kernel32.CreateNamedPipeW("\\\\.\\pipe\\" + leaf, 3, 1, 1, 512, 512, 0, None)
    assert handle not in (None, wintypes.HANDLE(-1).value)

    def opened() -> bool:
        # ERROR_PIPE_CONNECTED (535) or ERROR_NO_DATA (232: opened and closed); 536 means never opened
        return bool(kernel32.ConnectNamedPipe(handle, None)) or ctypes.get_last_error() in (535, 232)

    yield leaf, opened
    kernel32.CloseHandle(handle)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path.resolve() / "root"
    folder.mkdir()
    return folder


@pytest.mark.parametrize("relative", [False, True])
def test_contained_refuses_a_mount_point_to_the_pipe_device_without_connecting(root: Path, pipe, mount_points,
                                                                               relative: bool) -> None:
    from g6.core.storage import PathRefused, contained

    leaf, opened = pipe
    mount_points(root / "j", PIPE_DEVICE)
    with pytest.raises(PathRefused) as refused:
        contained(root, f"j\\{leaf}" if relative else root / "j" / leaf)
    assert refused.value.outside is True, refused.value.reason
    assert not opened(), "contained connected to the pipe before refusing"


def test_contained_with_refuse_links_does_not_connect(root: Path, pipe, mount_points) -> None:
    from g6.core.storage import PathRefused, contained

    leaf, opened = pipe
    mount_points(root / "j", PIPE_DEVICE)
    with pytest.raises(PathRefused):
        contained(root, root / "j" / leaf, refuse_links=True)
    assert not opened()


def test_plain_file_refuses_a_mount_point_to_the_pipe_device_without_connecting(root: Path, pipe,
                                                                               mount_points) -> None:
    from g6.core.storage import PathRefused, plain_file

    leaf, opened = pipe
    mount_points(root / "j", PIPE_DEVICE)
    with pytest.raises(PathRefused) as refused:
        plain_file(root / "j" / leaf)
    assert refused.value.outside is True, refused.value.reason
    assert not opened(), "plain_file connected to the pipe before refusing"


@pytest.mark.parametrize("call", ["existing", "read_text"])
def test_a_declared_root_refuses_the_mount_point_as_outside_without_connecting(root: Path, pipe, mount_points,
                                                                             call: str) -> None:
    from g6.code import _roots
    from g6.code._types import CodeRefused

    leaf, opened = pipe
    roots = _roots.Roots((root,), "test")
    mount_points(root / "j", PIPE_DEVICE)
    target = root / "j" / leaf
    with pytest.raises(CodeRefused) as refused:
        if call == "existing":
            _roots.existing(str(target), roots, kind="file")
        else:
            _roots.read_text(target, 1000, roots=roots)
    assert not opened(), f"{call} connected to the pipe before refusing"
    assert refused.value.code == _roots.OUTSIDE_ROOT, refused.value.reason


def test_a_folder_swapped_for_the_mount_point_after_the_name_check_is_not_connected(root: Path, pipe, mount_points,
                                                                                    monkeypatch) -> None:
    from g6.code import _roots
    from g6.code._types import CodeRefused

    leaf, opened = pipe
    (root / "sub").mkdir()
    (root / "sub" / leaf).write_text("INSIDE = 1\n", encoding="utf-8")
    roots = _roots.Roots((root,), "test")
    real = _roots.confined_path
    swapped: list[bool] = []

    def check_then_swap(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        out = real(*args, **kwargs)
        if not swapped:
            swapped.append(True)
            os.rename(root / "sub", root / "sub-was")
            mount_points(root / "sub", PIPE_DEVICE)
        return out

    monkeypatch.setattr(_roots, "confined_path", check_then_swap)
    with pytest.raises(CodeRefused):
        _roots.read_text(root / "sub" / leaf, 1000, roots=roots)
    assert swapped and not opened(), "plain_file connected to the pipe while the name was checked"


def test_a_mount_point_to_a_folder_inside_the_root_is_still_followed(root: Path, mount_points) -> None:
    from g6.core.storage import contained, plain_file

    (root / "real").mkdir()
    (root / "real" / "a.txt").write_text("x", encoding="utf-8")
    mount_points(root / "j", "\\??\\" + str(root / "real"))
    assert contained(root, root / "j" / "a.txt") == root / "real" / "a.txt"
    assert plain_file(root / "j" / "a.txt").st_size == 1
