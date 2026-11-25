"""Review 4 of claude/template-methods-fu (1464c340), finding 5.

Class: the address normalization removes `` at 0x<hex>`` from a value's content, not only from an object's default
display, so two different values are shown, and in one path compared, as the same.

(a) New in repair 3. The removal moved from ``_plain`` into ``_shown`` (both copies), and ``_shown`` also writes
relation.v1's failure line (``"input %d: %s gave %s where %s was expected"``) and access_rule.v1's case label. A
text result such as ``'segment at 0x10'`` (a disassembler, a debugger, a memory map) is now displayed without its
address, so the line for a broken round trip reads ``gave 'segment' where 'segment' was expected``. b27712b1's
``_shown`` displayed both texts whole.

(b) As the branch's base (86204ecf), re-homed by repair 3. ``_plain`` compares a value that has no attributes by
``_shown``'s text. ``bytes`` is such a value: ``b'fault at 0x10'`` and ``b'fault at 0x20'`` are one observation, and
differential.v1 reads a change of the returned bytes as not refuted. A text (``str``) is compared whole.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu4_support as support  # noqa: E402
from tmfu4_support import vs  # noqa: E402
from tests.harness.test_template_runtime_binding import _compiled  # noqa: E402


@pytest.mark.parametrize("template", ["relation.v1", "access_rule.v1"])
def test_two_different_texts_are_not_displayed_as_the_same(template):
    namespace, _ = _compiled(template)
    left, right = namespace["_shown"]("segment at 0x10"), namespace["_shown"]("segment at 0x20")
    assert left != right, (
        f"{template}'s failure line would read: input 0: round_trip gave {left} where {right} was expected")


def test_a_change_of_returned_bytes_is_observed(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "differential.v1", ("def run(value):\n    return b'fault at 0x10'\n", None),
        ("def run(value):\n    return b'fault at 0x20'\n", None))
    assert verdict["result"] == vs.BEHAVIOUR_CHANGED, (
        f"the base returns b'fault at 0x10' and the patch b'fault at 0x20'; G6 observed {found['observations']!r} "
        f"on the patch and the finding is {verdict['result']!r}: {verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["relation.v1", "access_rule.v1"])
def test_control_an_objects_address_is_still_removed_and_other_texts_differ(template):
    namespace, _ = _compiled(template)
    assert namespace["_shown"](object()) == "<object object>"
    assert namespace["_shown"]("segment 0x10") != namespace["_shown"]("segment 0x20")


def test_control_a_change_of_returned_text_is_observed(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "differential.v1", ("def run(value):\n    return 'fault at 0x10'\n", None),
        ("def run(value):\n    return 'fault at 0x20'\n", None))
    assert verdict["result"] == vs.BEHAVIOUR_CHANGED, (verdict, found)


def test_control_a_change_of_other_returned_bytes_is_observed(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "differential.v1", ("def run(value):\n    return b'fault 0x10'\n", None),
        ("def run(value):\n    return b'fault 0x20'\n", None))
    assert verdict["result"] == vs.BEHAVIOUR_CHANGED, (verdict, found)


def test_control_the_branch_base_compared_the_bytes_the_same_way(request):
    old = support.runner_at("86204ecf")
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "differential.v1", ("def run(value):\n    return b'fault at 0x10'\n", None),
        ("def run(value):\n    return b'fault at 0x20'\n", None), module=old)
    assert verdict["result"] == vs.NOT_REFUTED, (verdict, found)
