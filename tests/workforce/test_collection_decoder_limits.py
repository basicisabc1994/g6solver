# SPDX-License-Identifier: Apache-2.0
"""Decoder exhaustion is an unavailable compilation, never a usable identity."""
from types import SimpleNamespace

import pytest

from g6.workforce import repo_tests


@pytest.mark.parametrize("error", [RecursionError, MemoryError])
def test_compiler_response_limits_are_refused(tmp_path, monkeypatch, error):
    monkeypatch.setattr(repo_tests.component_process, "run_contained",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"{}"))
    def exhausted(*args, **kwargs):
        raise error("decoder exhausted")
    monkeypatch.setattr(repo_tests.json, "loads", exhausted)
    with pytest.raises(ValueError, match="decoder limits"):
        repo_tests._compile_collection_sources(tmp_path, {}, {}, python=__import__("sys").executable)
