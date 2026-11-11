# SPDX-License-Identifier: Apache-2.0
"""Review 1, class 4 (findings 6 and 19): the adaptation gate protects what it guards. The protected set is listed from
the functions that judge (the code's own listing, not filename fragments), and a candidate path is compared as the file
system resolves it, in every spelling."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from g6.cognitive_bridge import (
    AdaptationPromotionGate, ControlTrial, GuardChange, InProcessAssuranceEvaluator, PairedTrial,
)
from g6.cognitive_bridge.adaptation import protected_paths
from g6.epistemic_integrity.adjudicator import detect_contested
from g6.reasoning import sign_test_p

ROOT = Path(__file__).resolve().parents[2]
REVIEW_JUDGES = [AdaptationPromotionGate.assess, InProcessAssuranceEvaluator.evaluate, sign_test_p, detect_contested]


def _same_file(spelling: str, path: Path) -> bool:
    candidate = Path(spelling) if Path(spelling).is_absolute() else ROOT / spelling
    return candidate.exists() and candidate.samefile(path)


def _spellings(path: Path) -> list[str]:
    """Each spelling of ``path`` that this file system resolves to the same file (a case or separator variant only
    where the file system treats it as the same file)."""
    relative = path.relative_to(ROOT).as_posix()
    spellings = [relative, str(path), relative.upper(), relative.replace("/", "\\"), f"src/g6/../{relative[4:]}"]
    return [s for s in spellings if _same_file(s, path)]


@pytest.mark.parametrize("spelling", ["absolute", "case"])
def test_finding_6_windows_spellings_cannot_bypass_core_path_protection(spelling):
    original = ROOT / "src" / "g6" / "core" / "door.py"
    candidate = str(original) if spelling == "absolute" else "SRC/G6/CORE/door.py"
    if not _same_file(candidate, original):
        pytest.skip("a case-sensitive file system: this spelling names another file")
    assert (ROOT / candidate).samefile(original)
    assert protected_paths(["src/g6/core/door.py"]) == ["src/g6/core/door.py"]
    assert protected_paths([candidate]) == [candidate], f"core path accepted: {candidate}"


@pytest.mark.parametrize("judge", REVIEW_JUDGES, ids=lambda f: f.__qualname__)
def test_finding_19_protected_paths_cover_the_actual_implementations_that_judge_the_bridge(judge):
    path = Path(inspect.getsourcefile(judge)).relative_to(ROOT).as_posix()
    pairs = [PairedTrial(str(i), "c" * 64, False, True, True, "holdout", f"receipt:{i}") for i in range(6)]
    controls = [ControlTrial("positive", True, "positive", "receipt:p"),
                ControlTrial("negative", True, "negative", "receipt:n")]
    change = GuardChange("change", "a" * 64, "b" * 64, "c" * 64, (path,), "cognitive_bridge", "candidate", "outside",
                         ("false_confidence",))
    gate = AdaptationPromotionGate(admit_pair=lambda p: p in pairs, admit_control=lambda c: c in controls,
                                   min_cases=6, min_discordant=6, max_evaluations=1)
    result = gate.assess(change, pairs, controls, detected_failures=["false_confidence"])
    assert result["completion_state"] == "blocked-escalated" and protected_paths([path]) == [path], (
        f'actual judge can be changed: {path}; recommendation={result["recommendation"]}')


def test_each_listed_judge_is_protected_in_every_spelling():
    from g6.cognitive_bridge.adaptation import judged_files

    files = judged_files()
    for judge in REVIEW_JUDGES:  # the listing holds at least the judges the review named
        assert Path(inspect.getsourcefile(judge)).resolve() in files, judge
    for path in sorted(files):
        for spelling in _spellings(path):
            assert protected_paths([spelling]) == [spelling], spelling


def test_an_ordinary_bridge_file_is_still_open_to_a_guard():
    assert protected_paths(["src/g6/cognitive_bridge/guard.py"]) == []
    assert protected_paths(["src/g6/cognitive_bridge/method_adapter.py"]) == []
