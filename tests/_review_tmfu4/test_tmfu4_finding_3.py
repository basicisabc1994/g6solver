"""Review 4 of claude/template-methods-fu (1464c340), finding 3.

Class: the missing-import mark discounts a failure and never a pass, so a change whose new code sits behind an
import G6's runner cannot satisfy is "not refuted" by a run that never executed it, while the same record says the
import did not complete.

The patch below adds an optional fast path. In G6's runner (``-I -S``, no installed package) the import fails, the
guard returns the old value, and the new line never runs. The frame holds ``missing_import`` on that outcome
(``G6_E_OBSERVATION_UNAVAILABLE: import or lazy lookup did not complete``); ``finish`` reads it only when a case
failed. example.v1 says passed and the finding is not_refuted, "passes at the base commit and on the patch", with
nothing in the reading that says the fallback was taken. differential.v1, on the same two trees, says unavailable.
Review 3's finding 5 was this shape for ``unavailable_import`` (a pass beside a record that says the import did not
run); repair 3 added the second mark and left its pass side open.

The pass predates repair 3 (b27712b1 reads the same), and review 3's control
``test_control_a_guard_whose_fallback_returns_the_confirmed_value_passes`` and the repair's
``test_a_matching_fallback_still_supports_the_confirmed_value`` pin it for a guard whose import is unused, so the
root needs the lead's ruling. With finding 1 it points at one rule: an import both trees miss alike leaves their
comparison the change's; an import only the patch misses leaves neither a failure nor a pass established.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tmfu4_support as support  # noqa: E402
from tmfu4_support import vs  # noqa: E402

X = support.EXTERNAL
GOOD = "def run(value):\n    return value\n"
#: The change: a fast path that is wrong wherever the package is installed, behind a guard that keeps the old value.
FAST_PATH = (f"def run(value):\n    try:\n        import {X}\n    except ImportError:\n        return value\n"
             f"    return {X}.transform(value) + 1\n")


def test_a_pass_by_a_fallback_only_the_patch_takes_is_not_read_as_not_refuted(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", (GOOD, None),
                                                                    (FAST_PATH, None))
    marked = [one.get("missing_import") for one in found["observed"]["outcomes"]]
    assert marked == [support.runner.ObservationUnavailable.DETAIL], marked   # G6's own record of the call
    assert verdict["result"] != vs.NOT_REFUTED, (
        "the patch's new line never ran: G6's runner could not import the package it needs, and its record of "
        f"the call says so ({marked[0]!r}); the reading is {on_patch['result']!r} ({on_patch['detail']!r}) and the "
        f"finding is {verdict['result']!r}: {verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_the_differential_template_withholds_the_same_two_trees(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "differential.v1", (GOOD, None),
                                                                    (FAST_PATH, None))
    assert on_patch["result"] == "error" and support.UNAVAILABLE in on_patch["detail"], found
    assert verdict["result"] == vs.CHECK_ERROR


def test_control_the_same_import_with_no_guard_is_unavailable(request):
    patch = f"def run(value):\n    import {X}\n    return {X}.transform(value) + 1\n"
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", (GOOD, None),
                                                                    (patch, None))
    assert on_patch["result"] == "error" and support.UNAVAILABLE in on_patch["detail"], found
    assert verdict["result"] == vs.CHECK_ERROR


def test_control_a_patch_with_no_import_that_returns_the_confirmed_value_is_not_refuted(request):
    patch = "def run(value):\n    return value + 0\n"
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", (GOOD, None),
                                                                    (patch, None))
    assert verdict["result"] == vs.NOT_REFUTED, (verdict, found)
