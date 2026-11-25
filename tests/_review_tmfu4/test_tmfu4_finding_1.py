"""Review 4 of claude/template-methods-fu (1464c340), finding 1.

Class: a recorded missing import withholds every non-passing reading of the run, whatever the import had to do with
it, so a real defect of the change is unavailable in any module that keeps an ordinary guarded import.

Repair 3 marks every outcome of a run once ``_missing_imports`` holds anything (``behaviour_runner.py``, the call
loop: ``if _missing_imports: outcomes[-1]['missing_import'] = ...``), and ``finish`` then returns
G6_E_OBSERVATION_UNAVAILABLE for any failed case, and for every probe (``"cases" not in data``), pass or not. G6's
runner is ``-I -S``: it has no installed package at all, so every guard of a third-party import takes its fallback
there, on the base tree and on the patched tree alike. The guard is recorded when the module loads and is never
forgotten.

The shapes below keep the same guard on both trees (the change does not touch it) and change only what the
function returns. Both runs take the same fallback for the same reason, so the difference between them is the
change's. b27712b1 read each as a counterexample (example.v1) or a changed behaviour (differential.v1); this tip
reads G6_E_OBSERVATION_UNAVAILABLE, and differential.v1 gives no answer at all for such a module, changed or not.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu4_support as support  # noqa: E402
from tmfu4_support import vs  # noqa: E402

X = support.EXTERNAL
#: A module of the standard library this platform does not have: the ordinary cross-platform guard.
PLATFORM = "fcntl" if os.name == "nt" else "msvcrt"
GOOD = "def run(value):\n    return value\n"
BROKEN = "def run(value):\n    return value + 1\n"   # the change's defect: nothing to do with any import
GUARDS = {
    "optional-third-party-package": (f"try:\n    import {X}\nexcept ImportError:\n    {X} = None\n", None),
    "platform-module": (f"try:\n    import {PLATFORM}\nexcept ImportError:\n    {PLATFORM} = None\n", None),
    "guard-in-another-module-of-the-tree": (
        "import compat\n", {"compat.py": f"try:\n    import {X} as json\nexcept ImportError:\n    import json\n"}),
    "ignored-local-settings": ("try:\n    from local_settings import *\nexcept ImportError:\n    pass\n", None),
}


def both(request, template, guard, patched=BROKEN, **kwargs):
    head, extra = GUARDS[guard]
    return support.base_and_patch(request, template, (head + GOOD, extra), (head + patched, extra), **kwargs)


@pytest.mark.parametrize("guard", list(GUARDS))
def test_a_defect_beside_a_guard_both_trees_share_is_a_counterexample(request, guard):
    at_base, on_patch, verdict, found, ran = both(request, "example.v1", guard)
    assert at_base["result"] == "passed", at_base
    assert verdict["result"] == vs.COUNTEREXAMPLE, (
        "the base tree and the patched tree hold the same guarded import and took the same fallback; the patch "
        f"returns another value. The reading on the patch is {on_patch['result']!r} ({on_patch['detail']!r}) and "
        f"the finding is {verdict['result']!r}: {verdict['detail']!r}")


@pytest.mark.parametrize("guard", list(GUARDS))
def test_a_changed_output_beside_a_guard_both_trees_share_is_observed(request, guard):
    at_base, on_patch, verdict, found, ran = both(request, "differential.v1", guard)
    assert verdict["result"] == vs.BEHAVIOUR_CHANGED, (
        "the two trees hold the same guarded import and return different values; the readings are "
        f"{at_base['result']!r} at the base and {on_patch['result']!r} on the patch ({on_patch['detail']!r}), and "
        f"the finding is {verdict['result']!r}: {verdict['detail']!r}")


def test_an_unchanged_function_beside_a_guard_is_observed_at_all(request):
    at_base, on_patch, verdict, found, ran = both(request, "differential.v1", "optional-third-party-package", GOOD)
    assert verdict["result"] == vs.NOT_REFUTED, (
        "differential.v1 gives no answer for a module that keeps a guarded import G6's runner cannot satisfy: "
        f"base {at_base['result']!r}, patch {on_patch['result']!r}, finding {verdict['result']!r}: "
        f"{verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template,wanted", [("example.v1", vs.COUNTEREXAMPLE),
                                             ("differential.v1", vs.BEHAVIOUR_CHANGED)])
def test_control_the_same_defect_with_no_guard_is_found(request, template, wanted):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, template, (GOOD, None), (BROKEN, None))
    assert verdict["result"] == wanted, (verdict, found)


@pytest.mark.parametrize("template,wanted", [("example.v1", vs.COUNTEREXAMPLE),
                                             ("differential.v1", vs.BEHAVIOUR_CHANGED)])
def test_control_the_runner_before_repair_3_found_it(request, template, wanted):
    old = support.runner_at("b27712b1")
    at_base, on_patch, verdict, found, ran = both(request, template, "optional-third-party-package", module=old)
    assert verdict["result"] == wanted, (verdict, found)


def test_control_a_fallback_only_the_patch_takes_is_still_not_a_counterexample(request):
    # Review 3's finding 2: the guard is the change's own, so the base run gives no reading of the fallback.
    patch = f"def run(value):\n    try:\n        import {X}\n    except ImportError:\n        return 0\n    return value\n"
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", (GOOD, None),
                                                                    (patch, None))
    assert on_patch["result"] == "error" and support.UNAVAILABLE in on_patch["detail"], found
    assert verdict["result"] == vs.CHECK_ERROR


def test_control_a_guard_both_trees_share_with_nothing_changed_passes_an_example(request):
    at_base, on_patch, verdict, found, ran = both(request, "example.v1", "optional-third-party-package", GOOD)
    assert verdict["result"] == vs.NOT_REFUTED, (verdict, found)
