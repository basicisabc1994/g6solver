# SPDX-License-Identifier: Apache-2.0
"""Held and dry-run commands describe the repository relative to its declared root."""
import json

import pytest

from g6.sources.ctx_git import config as cfg, ops


@pytest.mark.parametrize("op", ["fetch", "push", "pull"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_held_commands_omit_absolute_repository(tmp_path, op, dry_run):
    root = tmp_path / "root"
    repo = root / "repo"
    config = cfg.GitConfig((root,), (), "test")
    outward = ops.Outward(config, lambda *_: {"decision": "block"})
    result, _ = ops._go(op, ["-C", str(repo), op, "origin"], {}, outward,
                       ops.Call(op, repo=str(repo), dry_run=dry_run))
    assert str(root) not in json.dumps(result)
    assert result["would_run"] == f"git -C repo {op} origin"
