# SPDX-License-Identifier: Apache-2.0
"""Git metadata and audit failures never disclose OS text or escape as raw errors."""
from pathlib import Path

import pytest

from g6.sources.ctx_git import block, config


@pytest.mark.parametrize("metadata", [".git", "commondir", "alternates"])
def test_git_metadata_read_failure_is_refused(metadata, monkeypatch):
    repo = Path(__file__).resolve().parent
    cfg = config.GitConfig((repo,), (), "test")
    monkeypatch.setattr(config, "_confined", lambda path, *a: Path(path))
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(Path, "exists", lambda _: True)
    monkeypatch.setattr(Path, "is_file", lambda _: True)

    def read(path, **kwargs):
        if path.name == metadata:
            raise PermissionError("private-system-details")
        return "gitdir: git-folder" if path.name == ".git" else "."

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(config.GitRefused) as refused:
        config.repository(str(repo), cfg)
    assert refused.value.code == config.PATH_INVALID
    assert "private-system-details" not in refused.value.reason
    assert str(repo) not in refused.value.reason


def test_audit_failure_uses_only_g6_words(monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("private-system-details")

    monkeypatch.setattr(block.runner, "record", fail)
    with pytest.raises(config.GitRefused) as refused:
        block._intend({"op": "status"})
    assert refused.value.code == block.AUDIT
    assert "private-system-details" not in refused.value.reason
    reply = {"warning_card": {"message": "observed"}, "evidence": []}
    out = block._unrecorded(reply, block._write({}, "outcome"))
    assert "private-system-details" not in repr(out)
