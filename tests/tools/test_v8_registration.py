# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("v8_sample", Path(__file__).resolve().parents[2] /
                                            "validation/v8_composition/sample.py")
sampler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sampler)


def test_python_only_new_split_excludes_seen_nonpilot_tasks_and_is_reproducible():
    rows = [{"instance_id": str(i), "repo": "internetarchive/openlibrary", "repo_language": "python"}
            for i in range(20)] + [{"instance_id": "js", "repo": "NodeBB/NodeBB", "repo_language": "js"}]
    v7 = {"pilot": ["0", "js", "1"], "dev": ["0", "1", "2", "js"], "dataset": {"sha256": "pinned"}}
    split = sampler.draw(rows, v7, dev_count=4, scored_count=6)
    assert split == sampler.draw(rows, v7, dev_count=4, scored_count=6)
    assert split["reused_pilots"] == ["0", "1"]
    assert not set(split["scored"]) & set(v7["dev"] + split["dev"])
    assert "2" not in split["dev"] and "js" not in split["repositories"]
    with pytest.raises(ValueError, match="insufficient"):
        sampler.draw(rows, v7, scored_count=100)
