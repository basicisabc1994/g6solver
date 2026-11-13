"""The folder G6 writes holds only the manifest, and its reading lists what a path carries beyond names and bytes."""
import hashlib
import os
import time
from pathlib import Path

import pytest

from g6.harness import behaviour_runner as runner
from g6.workforce import repo_tests as rt

WINDOWS = os.name == 'nt'
FILES = {'a.py': b'VALUE = -1\n', 'pkg/__init__.py': b'', 'pkg/b.py': b'B = 1\r\n', 'pkg/deep/c.dat': bytes(range(256))}


def _copy(tmp_path):
    copy = tmp_path / 'copy'
    for rel, data in FILES.items():
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / rel).write_bytes(data)
    return copy


def _soon():
    return time.monotonic() + 60


def test_made_folder_is_only_the_manifest(tmp_path):
    copy = _copy(tmp_path)
    twin = tmp_path / 'twin.py'
    twin.write_bytes(FILES['pkg/b.py'])
    (copy / 'pkg' / 'b.py').unlink()
    os.link(twin, copy / 'pkg' / 'b.py')   # another name for a listed file, outside the copy
    if WINDOWS:
        for carrier in (copy / 'a.py', copy / 'pkg', copy):
            Path(str(carrier) + ':helper.py').write_bytes(b'VALUE = 5\n')
    (copy / 'VALUE=5').mkdir()   # a folder that holds nothing: its name is all it carries
    files = rt._copy_snapshot(copy)
    assert files == {rel: hashlib.sha256(data).hexdigest() for rel, data in FILES.items()}
    assert set(runner._beyond(copy)) >= {'pkg/b.py (another name)', 'VALUE=5/'}
    tree = tmp_path / 'tree'
    runner._made(tree, copy, files, _soon())
    assert rt._copy_snapshot(tree) == files
    assert runner._beyond(tree) == {}, 'nothing beyond the manifest is carried into the folder G6 writes'
    twin.write_bytes(b'B = 5\r\n')   # written through the other name: the copy's file changes, the made one cannot
    assert (copy / 'pkg' / 'b.py').read_bytes() == b'B = 5\r\n'
    assert (tree / 'pkg' / 'b.py').read_bytes() == FILES['pkg/b.py']
    assert sorted(p.relative_to(tree).as_posix() for p in tree.rglob('*')) == sorted(
        {*FILES, 'pkg', 'pkg/deep'})


@pytest.mark.parametrize('record', ['link:../outside', 'unreadable', '', 'A' * 64, 'g' * 64, '0' * 63, '0' * 65, 5, None])
def test_what_g6_cannot_write_refuses_the_run(tmp_path, record):
    copy = _copy(tmp_path)
    files = {**rt._copy_snapshot(copy), 'a.py': record}
    with pytest.raises(rt.CopyNotRun, match='G6_E_EXECUTION_ARTIFACT_UNAVAILABLE'):
        runner._made(tmp_path / 'tree', copy, files, _soon())


@pytest.mark.parametrize('name', ['', '../escape.py', 'pkg/../../escape.py', '/escape.py', 'C:/escape.py',
                                  'a.py:helper.py'])
def test_a_name_that_is_not_a_plain_path_below_the_folder_refuses_the_run(tmp_path, name):
    if name in ('C:/escape.py', 'a.py:helper.py') and not WINDOWS:
        pytest.skip('a colon is an ordinary character in a name here')
    copy = _copy(tmp_path)
    files = {**rt._copy_snapshot(copy), name: hashlib.sha256(b'').hexdigest()}
    with pytest.raises(rt.CopyNotRun, match='G6_E_EXECUTION_ARTIFACT_UNAVAILABLE'):
        runner._made(tmp_path / 'tree', copy, files, _soon())
    assert not (tmp_path / 'escape.py').exists()


def test_bytes_that_changed_since_the_manifest_was_read_refuse_the_run(tmp_path):
    copy = _copy(tmp_path)
    files = rt._copy_snapshot(copy)
    (copy / 'a.py').write_bytes(b'VALUE = 5\n')
    with pytest.raises(rt.CopyNotRun, match='the copy changed'):
        runner._made(tmp_path / 'tree', copy, files, _soon())


def test_a_listed_file_that_is_gone_is_an_error_not_an_empty_file(tmp_path):
    copy = _copy(tmp_path)
    files = rt._copy_snapshot(copy)
    (copy / 'a.py').unlink()
    with pytest.raises(OSError):
        runner._made(tmp_path / 'tree', copy, files, _soon())


def test_two_spellings_of_one_name_refuse_the_run(tmp_path):
    copy = _copy(tmp_path)
    if not (copy / 'A.PY').exists():
        pytest.skip('this folder tells the two spellings apart: they are two files')
    files = rt._copy_snapshot(copy)
    files['A.py'] = files['a.py']
    with pytest.raises(rt.CopyNotRun, match='two names of the manifest are one name'):
        runner._made(tmp_path / 'tree', copy, files, _soon())


def test_the_time_cap_refuses_the_run(tmp_path):
    copy = _copy(tmp_path)
    with pytest.raises(rt.CopyNotRun):
        runner._made(tmp_path / 'tree', copy, rt._copy_snapshot(copy), time.monotonic() - 1)


def test_the_folder_must_be_new(tmp_path):
    copy = _copy(tmp_path)
    (tmp_path / 'tree').mkdir()
    with pytest.raises(FileExistsError):
        runner._made(tmp_path / 'tree', copy, rt._copy_snapshot(copy), _soon())


def test_reading_lists_each_stream_and_each_other_name(tmp_path):
    copy = _copy(tmp_path)
    assert runner._beyond(copy) == {}
    os.link(copy / 'a.py', tmp_path / 'other-name.py')
    (copy / 'pkg' / 'VALUE=5').mkdir()
    (copy / 'holds-only-a-folder' / 'inner').mkdir(parents=True)
    expected = {'a.py (another name)': 'hard link', 'pkg/VALUE=5/': 'empty folder',
                'holds-only-a-folder/inner/': 'empty folder'}
    if WINDOWS:
        for carrier, name in ((copy / 'a.py', 'one.py'), (copy / 'a.py', 'two'), (copy / 'pkg', 'on-a-folder.py'),
                              (copy / 'pkg' / 'deep', 'nested'), (copy / 'pkg' / 'deep' / 'c.dat', 'x'),
                              (copy, 'on-the-top.py')):
            Path(str(carrier) + ':' + name).write_bytes(b'VALUE = 5\n')
        expected.update({'a.py:one.py:$DATA': 'stream', 'a.py:two:$DATA': 'stream',
                         'pkg:on-a-folder.py:$DATA': 'stream', 'pkg/deep:nested:$DATA': 'stream',
                         'pkg/deep/c.dat:x:$DATA': 'stream', '.:on-the-top.py:$DATA': 'stream'})
    before = rt._copy_snapshot(copy)
    assert runner._beyond(copy) == expected
    after = {**rt._copy_snapshot(copy), **runner._beyond(copy)}
    assert rt._moved(before, after) == sorted(expected), 'each is a change by the ordinary comparison'


@pytest.mark.skipif(not WINDOWS, reason='alternate data streams are an NTFS feature')
def test_a_stream_listing_that_fails_is_an_entry_never_read_as_no_stream(tmp_path, monkeypatch):
    import ctypes
    import ctypes.wintypes
    copy = _copy(tmp_path)
    real = ctypes.WinDLL

    class Refusing:
        def __init__(self, *args, **kwargs):
            self._real = real(*args, **kwargs)

        def __getattr__(self, name):
            found = getattr(self._real, name)
            if name != 'FindFirstStreamW':
                return found

            def refused(*args):
                ctypes.set_last_error(5)   # ERROR_ACCESS_DENIED
                return ctypes.wintypes.HANDLE(-1).value
            return refused

    monkeypatch.setattr(ctypes, 'WinDLL', Refusing)
    found = runner._streams(copy)
    assert found and all(key.endswith(':') and 'not read (error 5)' in value for key, value in found.items()), found
    assert set(found) == {rel + ':' for rel in ('.', 'pkg', 'pkg/deep', *FILES)}
