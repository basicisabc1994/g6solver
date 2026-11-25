"""Optional in-memory fallback for the sandbox's inaccessible basetemp."""
import contextlib
import hashlib
import os
import stat
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path(request, monkeypatch):
    if os.environ.get("G6_REVIEW_MEMORY") != "1":
        return request.getfixturevalue("tmp_path_factory").mktemp("review")

    from worked_examples import replay_rc as rc

    base = Path(request.config.getoption("basetemp")).absolute()
    root = base / hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
    directories = {base, root}
    files = {}

    def virtual(path):
        return Path(path).is_relative_to(base)

    def mkdir(path, mode=0o777, parents=False, exist_ok=False):
        path = Path(path)
        if path in directories and not exist_ok:
            raise FileExistsError(str(path))
        if parents:
            directories.update(p for p in path.parents if virtual(p))
        directories.add(path)

    def read_bytes(path):
        try:
            return files[Path(path)]
        except KeyError:
            raise FileNotFoundError(str(path)) from None

    def write_bytes(path, value):
        files[Path(path)] = bytes(value)
        return len(value)

    def write_text(path, value, encoding=None, errors=None, newline=None):
        files[Path(path)] = value.encode(encoding or "utf-8", errors or "strict")
        return len(value)

    def read_text(path, encoding=None, errors=None):
        return read_bytes(path).decode(encoding or "utf-8", errors or "strict")

    def glob(path, pattern, **kwargs):
        return iter(p for p in files if p.is_relative_to(path) and p.relative_to(path).match(pattern))

    for name, replacement in {
        "exists": lambda p: Path(p) in files or Path(p) in directories,
        "is_file": lambda p: Path(p) in files,
        "is_dir": lambda p: Path(p) in directories,
        "mkdir": mkdir, "read_bytes": read_bytes, "write_bytes": write_bytes,
        "read_text": read_text, "write_text": write_text, "glob": glob,
    }.items():
        original = getattr(Path, name)

        def routed(path, *args, _original=original, _replacement=replacement, **kwargs):
            return (_replacement if virtual(path) else _original)(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, routed)

    original_opened = rc._open_handle

    @contextlib.contextmanager
    def opened(path, **kwargs):
        path = rc._absolute_path(path)
        if not virtual(path):
            with original_opened(path, **kwargs) as result:
                yield result
            return
        mode = stat.S_IFREG if path in files else stat.S_IFDIR if path in directories else None
        if mode is None and not kwargs.get("missing"):
            raise FileNotFoundError(str(path))
        yield rc._Opened(path, None, mode)

    original_read = rc.Source.read_bytes

    def source_read(source, rel):
        if not virtual(source.root):
            return original_read(source, rel)
        path = source.path(rel)
        try:
            content = read_bytes(path)
        except FileNotFoundError:
            source._remember(path, None)
            raise
        if source.evidence_files is not None:
            source._remember(path, hashlib.sha256(content).hexdigest())
        return content

    def append(archive, row):
        archive.root.mkdir(parents=True, exist_ok=True)
        files[archive.index] = files.get(archive.index, b"") + (json_dumps(row) + "\n").encode()

    from json import dumps as json_dumps

    monkeypatch.setattr(rc, "_open_handle", opened)
    monkeypatch.setattr(rc.Source, "read_bytes", source_read)
    monkeypatch.setattr(rc.c.Archive, "append", append)
    return root
