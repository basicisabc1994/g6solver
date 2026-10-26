# SPDX-License-Identifier: Apache-2.0
"""``AIBlock >> AIBlock`` never labels a composition ``verified`` for free (CORE_LABELS-01).

Library blocks report their state inside the value dict, not as a ``Result`` attribute, so the composition must
read it there, take the weakest stage, and default to ``qualified-draft``.
"""
from __future__ import annotations

from typing import Any

import pytest

from g6.core import AIBlock, Result


class _Says(AIBlock):
    """A stage whose value dict reports ``state`` (or no state at all when ``state`` is None)."""

    def __init__(self, name: str, state: str | None) -> None:
        super().__init__(name=name)
        self._state = state

    def infer(self, data: Any) -> Result[Any]:
        value: dict[str, Any] = {"answer": data}
        if self._state is not None:
            value["completion_state"] = self._state
        return Result.ok(value)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("qualified-draft", "qualified-draft", "qualified-draft"),
        ("qualified-draft", "blocked-escalated", "blocked-escalated"),
        ("blocked-escalated", "verified", "blocked-escalated"),
        ("verified", "qualified-draft", "qualified-draft"),
        ("verified", None, "qualified-draft"),
    ],
)
def test_the_composition_takes_the_weakest_state_the_values_report(
    left: str, right: str | None, expected: str
) -> None:
    # Fails if ai_block._stage_state stops reading value["completion_state"] (the chain would come out "verified").
    result = (_Says("L", left) >> _Says("R", right)).infer("x")
    assert result.is_ok()
    assert result.completion_state == expected


def test_a_chain_with_no_state_anywhere_is_a_qualified_draft() -> None:
    # Fails if ai_block._annotate_result or _worst_completion_state defaults to "verified" again.
    result = (_Says("L", None) >> _Says("R", None)).infer("x")
    assert result.completion_state == "qualified-draft"


def test_a_three_stage_chain_keeps_the_weakest_stage() -> None:
    # Fails if a nested PipelineBlock's own result attribute is ignored in favour of its value dict alone.
    chain = _Says("A", "blocked-escalated") >> _Says("B", "verified") >> _Says("C", "verified")
    assert chain.infer("x").completion_state == "blocked-escalated"


def test_a_state_outside_the_three_is_never_relayed_as_verified() -> None:
    # Fails if ai_block._stage_state passes an unknown word through instead of reading it as qualified-draft.
    result = (_Says("L", "verified") >> _Says("R", "done")).infer("x")
    assert result.completion_state == "qualified-draft"


def test_two_self_verified_stages_are_held() -> None:
    """Plan A1: a stage's own verified is a claim; the chain keeps it only when G6's re-check reproduces it."""
    result = (_Says("L", "verified") >> _Says("R", "verified")).infer("x")
    assert result.completion_state == "qualified-draft"
    assert result.claimed_completion_state == "verified"
    assert result.warning_card["code"] == "G6_E_PROMOTION_LAW"


class _Integrity(AIBlock):
    """A stage named for the integrity package, returning a pinned integrity result over ``root``: its name is not
    where its code lives, so it is not G6's integrity code."""

    def __init__(self, root) -> None:
        super().__init__(name="integrity")
        self._root = root

    def infer(self, data: Any) -> Result[Any]:
        from dataclasses import asdict

        from g6.integrity import build_manifest, manifest_sha256, verify_tree

        manifest = build_manifest(self._root)
        found = verify_tree(self._root, manifest, expected_manifest_sha256=manifest_sha256(manifest))
        return Result.ok({**asdict(found), "completion_state": found.reliability_label})


def test_a_stage_named_for_g6s_integrity_package_is_held(tmp_path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    assert (_Integrity(root) >> _Integrity(root)).infer("x").completion_state == "qualified-draft"