# SPDX-License-Identifier: Apache-2.0
"""Clone actions and held commands expose relative destinations only."""
import json

from g6.sources.ctx_git import config as cfg, ops


def test_clone_gate_receives_relative_destination_and_held_reply(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    captured = []

    def gate(action, _):
        captured.append(action)
        return {"decision": "block"}

    outward = ops.Outward(cfg.GitConfig((root,), ("example.invalid",), "test"), gate,
                          resolver=lambda *_: ["93.184.216.34"])
    result = ops.clone(ops.Call("clone", url="https://example.invalid/a.git",
                                dest=str(root / "copy"), dry_run=True), outward)
    assert str(root) not in json.dumps(captured)
    assert str(root) not in json.dumps(result.reply)
    assert result.reply["would_run"] == 'git clone --no-recurse-submodules -- https://example.invalid/a.git copy'
