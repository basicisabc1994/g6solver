# SPDX-License-Identifier: Apache-2.0
"""``weakened_tests`` never reads a test file it could not parse as clean (the wire-parsers repair, stage 1): a
before-file that does not parse is a finding, not an empty list."""
from __future__ import annotations

from g6.workforce import verify


def test_a_before_file_that_does_not_parse_is_a_finding():
    found = verify.weakened_tests({"test_x.py": "def test_a(:\n    assert 1\n"},
                                  {"test_x.py": "def test_a():\n    assert 1\n"})
    assert found and all(line.startswith("test_x.py") for line in found)
