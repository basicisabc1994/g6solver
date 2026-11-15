# SPDX-License-Identifier: Apache-2.0
"""A pytest plugin that runs one shard of the suite: ``python -m pytest -p tests.g6_shard --g6-shard K/N``.

CI shares the suite among N jobs per platform (tools/suite_split.ps1 -Shard), each inside the hosted runner's time
limit. Collection is unchanged, so every conftest's ignore rules still apply; only the collected tests are divided, by a
stable hash of each test's file path, so each file runs in exactly one shard. It is a plugin of its own, loaded only
when asked for, rather than part of tests/conftest.py, whose text the source-test inventory reviews."""
from __future__ import annotations

import os
import zlib

import pytest


def pytest_addoption(parser):
    parser.addoption('--g6-shard', default=None, metavar='K/N',
                     help='run only shard K of N: the tests in the files whose path hashes to K-1 modulo N')


def shard_of(name: str, shards: int) -> int:
    """The 1-based shard a test file belongs to, from its path relative to the root with / separators."""
    return zlib.crc32(name.encode('utf-8')) % shards + 1


def pytest_collection_modifyitems(config, items):
    shard = config.getoption('--g6-shard')
    if not shard:
        return
    try:
        k, n = (int(part) for part in shard.split('/'))
    except ValueError:
        raise pytest.UsageError(f'--g6-shard {shard!r}: expected K/N') from None
    if not 1 <= k <= n:
        raise pytest.UsageError(f'--g6-shard {shard!r}: K must be between 1 and N')
    keep, drop = [], []
    for item in items:
        name = os.path.relpath(str(item.path), str(config.rootpath)).replace(os.sep, '/')
        (keep if shard_of(name, n) == k else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
