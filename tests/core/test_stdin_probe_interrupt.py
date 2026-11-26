# SPDX-License-Identifier: Apache-2.0
"""Interrupt cache setup in children, before a reader can hide an unclaimed handle."""
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("error_kind", ["keyboard", "cancelled"])
@pytest.mark.parametrize("probe_kind", ["accepted", "refused"])
def test_every_probe_line_and_helper_return_closes_only_the_owned_duplicate(
    tmp_path, error_kind, probe_kind,
):
    done = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(CHILD), str(tmp_path), error_kind, probe_kind],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=90,
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")


CHILD = r'''
import io
import os
import sys
import tempfile

import anyio
import pytest

from g6.mcp import _stdin_cache, stdio

folder, error_kind, probe_kind = sys.argv[1:]
tempfile.tempdir = folder
original_trace = sys.gettrace()
real_dup2 = os.dup2
real_close = stdio._InputDescriptor.close
files = {stdio.__file__, _stdin_cache.__file__}


class InventoryEnd(BaseException):
    pass


def walk(target=None):
    reader, writer = os.pipe()
    encoding = 'utf-8' if probe_kind == 'accepted' else 'cp932'
    wire = b'launcher\nsecond\n' if probe_kind == 'accepted' else b'launcher\n\x87\x90\n'
    os.write(writer, wire)
    os.close(writer)
    source = io.TextIOWrapper(io.BufferedReader(io.FileIO(reader, 'rb')), encoding=encoding)
    assert source.readline() == 'launcher\n'
    duplicates, closed, points = [], [], []
    seen = set()
    injected = False
    active = False
    foreign = None

    def duplicate(fd, slot, **kwargs):
        nonlocal active
        result = real_dup2(fd, slot, **kwargs)
        duplicates.append(slot)
        active = True
        return result

    def close(owner, *, reader=False):
        nonlocal foreign
        fd = owner.file.fileno() if owner.file is not None and owner.claimed == reader else None
        real_close(owner, reader=reader)
        if fd in duplicates:
            closed.append(fd)
            # Reuse immediately: a later finalizer or outer cleanup must leave this open.
            replacement = os.open(os.devnull, os.O_RDONLY)
            if replacement != fd:
                real_dup2(replacement, fd)
                os.close(replacement)
            foreign = fd

    def start(thread):
        nonlocal active
        active = False
        # Stop inventory before publication, without a background reader claiming the file.
        raise InventoryEnd

    async def run():
        nonlocal active
        error_type = KeyboardInterrupt if error_kind == 'keyboard' else anyio.get_cancelled_exc_class()

        def trace(frame, event, arg):
            nonlocal injected, active
            if frame.f_code is stdio._lines.__wrapped__.__code__ and event == 'exception':
                # A refusal ends startup inventory before its cleanup handler runs.
                active = False
            if active and event in ('line', 'return') and (
                frame.f_code.co_filename in files or frame.f_code is duplicate.__code__
            ):
                # Include distinct helper exits/yields; repeated codec-loop visits need one injection per site.
                point = (frame.f_code.co_qualname, event, frame.f_lineno, frame.f_lasti)
                if point not in seen:
                    seen.add(point)
                    points.append(point)
                    if point == target:
                        injected = True
                        active = False
                        raise error_type('probe interruption')
            return trace

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(stdio.os, 'dup2', duplicate)
            patch.setattr(stdio._InputDescriptor, 'close', close)
            patch.setattr(stdio.threading.Thread, 'start', start)
            sys.settrace(trace)
            try:
                async with stdio._lines(anyio.wrap_file(source)):
                    raise AssertionError('startup unexpectedly published a reader')
            except error_type:
                assert target is not None and injected
            except InventoryEnd:
                assert target is None and probe_kind == 'accepted'
            except OSError as exc:
                assert target is None and probe_kind == 'refused', repr(exc)
                assert '[G6_E_STDIN_HANDOFF]' in str(exc)
            finally:
                active = False
                sys.settrace(original_trace)
        assert target is None or injected, target
        assert len(duplicates) == 1, duplicates
        assert closed == duplicates, ('unclaimed probe duplicate', target, closed, duplicates)
        os.fstat(source.fileno())
        os.fstat(foreign)
        # Exercise finalizers while the retired number belongs to somebody else.
        import gc
        gc.collect()
        os.fstat(foreign)

    try:
        anyio.run(run)
        return points
    finally:
        sys.settrace(original_trace)
        source.close()
        if foreign is not None:
            os.close(foreign)


# Warm the inverse memo so inventory and injections follow the same cache path.
walk()
points = walk()
assert any(name.endswith('take') for name, _, _, _ in points), points
assert any(event == 'return' for _, event, _, _ in points), points
for point in points:
    walk(point)
assert not os.listdir(folder), os.listdir(folder)
'''
