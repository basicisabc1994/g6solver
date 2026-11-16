# SPDX-License-Identifier: Apache-2.0
"""A storage refusal's outside flag determines ctx_git's classification."""
import pytest

from g6.core.storage import PathRefused
from g6.sources.ctx_git import config


@pytest.mark.parametrize("outside", [False, True])
def test_confined_preserves_the_refusal_class(outside, monkeypatch):
    def refuse(*args, **kwargs):
        raise PathRefused("the path could not be checked", outside=outside)

    monkeypatch.setattr(config, "confined_path", refuse)
    with pytest.raises(config.GitRefused) as refused:
        config._confined("unused", ())
    assert refused.value.code == (config.OUTSIDE_ROOT if outside else "G6_E_GIT_PATH_INVALID")
