# SPDX-License-Identifier: Apache-2.0
"""ST-005, extended when the improvement loop was ported: a self-modification may not touch anything that judges it.
Only ``self_training/patch_promotion_gate`` was protected, so a patch could rewrite the loop, the rubrics or the
scorecard store, and the global kill switch and the envelope checker were open too."""
from __future__ import annotations

import pytest

from g6.self_training.patch_promotion_gate import HarnessPatch, protected_paths_touched

_JUDGES = ["src/g6/self_training/improvement_loop.py", "src/g6/self_training/rubrics.py",
           "src/g6/self_training/persistence.py", "src/g6/self_training/change_scope.py",
           "src/g6/core/stop.py", "src/g6/invariants/envelope.py"]


def _patch(path: str) -> HarnessPatch:
    return HarnessPatch(patch_id="p", touched_paths=[path])


@pytest.mark.parametrize("path", _JUDGES)
def test_st005_a_patch_may_not_touch_what_judges_it(path: str) -> None:
    assert protected_paths_touched(_patch(path)) == [path]


def test_st005_survival_an_ordinary_component_file_is_not_protected() -> None:
    assert protected_paths_touched(_patch("src/g6/debate/agreement.py")) == []
