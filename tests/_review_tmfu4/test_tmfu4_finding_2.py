"""Review 4 of claude/template-methods-fu (1464c340), finding 2.

Class: an exception whose chain holds a missing import is withheld even when it is the confirmed outcome, so a
fallback that runs inside the guard's handler can never support (or refute) a confirmed raise.

The ordinary shape::

    def run(text):
        try:
            import fastparser
        except ImportError:
            return int(text)          # the fallback, called inside the handler

The fallback's own ValueError carries the caught ModuleNotFoundError as its ``__context__``. Repair 3's
``unavailable_import`` walks ``__cause__`` and ``__context__`` and marks the outcome, and ``finish`` now withholds a
marked outcome before it compares it (the condition that let a confirmed, matching raise through is removed). So a
person's confirmed case ``run('x') raises ValueError`` reads G6_E_OBSERVATION_UNAVAILABLE on the base tree, where
the code does exactly that, and a change that stops raising is not found. The repair's own rule for a returned
value ("a matching fallback still supports the confirmed value", ``tests/harness/test_template_tmfu3_boundaries``)
does not hold for a raise. b27712b1 read the base as passed and the broken patch as a counterexample.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tmfu4_support as support  # noqa: E402
from tmfu4_support import vs  # noqa: E402

X = support.EXTERNAL
RAISES = {"function": "subject.run", "cases": [{"args": ["x"], "raises": "ValueError"}]}
STATED = "def run(value):\n    return int(value)\n"
INSIDE = f"def run(value):\n    try:\n        import {X}\n    except ImportError:\n        return int(value)\n"
#: The change's defect: the fallback no longer raises on a bad input.
BROKEN = f"def run(value):\n    try:\n        import {X}\n    except ImportError:\n        return 0\n"
#: The same fallback called after the handler ends: no exception chain.
AFTER = (f"def run(value):\n    try:\n        import {X}\n    except ImportError:\n        {X} = None\n"
         "    return int(value)\n")


def test_a_confirmed_raise_of_the_fallback_inside_the_handler_is_supported(request):
    disk = support.disk_for(request.node.nodeid)
    reading, found, ran = support.one_reading(disk / "tree", "example.v1", INSIDE, bindings=RAISES, stated=STATED)
    assert reading["result"] == "passed", (
        "the person confirmed run('x') raises ValueError and the code raises exactly that from its fallback; the "
        f"reading is {reading['result']!r}: {reading['detail']!r}")


def test_a_fallback_that_stops_raising_the_confirmed_error_is_a_counterexample(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "example.v1", (INSIDE, None), (BROKEN, None), bindings=RAISES, stated=STATED)
    assert verdict["result"] == vs.COUNTEREXAMPLE, (
        f"base {at_base['result']!r} ({at_base['detail']!r}), patch {on_patch['result']!r} "
        f"({on_patch['detail']!r}); the finding is {verdict['result']!r}: {verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_the_same_fallback_called_after_the_handler_is_supported(request):
    disk = support.disk_for(request.node.nodeid)
    reading, found, ran = support.one_reading(disk / "tree", "example.v1", AFTER, bindings=RAISES, stated=STATED)
    assert reading["result"] == "passed", (found, ran.stderr)


def test_control_a_matching_value_returned_inside_the_handler_is_supported(request):
    source = f"def run(value):\n    try:\n        import {X}\n    except ImportError:\n        return 1\n"
    disk = support.disk_for(request.node.nodeid)
    reading, found, ran = support.one_reading(disk / "tree", "example.v1", source)
    assert reading["result"] == "passed", (found, ran.stderr)


def test_control_the_runner_before_repair_3_supported_and_refuted_it(request):
    old = support.runner_at("b27712b1")
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "example.v1", (INSIDE, None), (BROKEN, None), bindings=RAISES, stated=STATED, module=old)
    assert at_base["result"] == "passed" and verdict["result"] == vs.COUNTEREXAMPLE, (at_base, verdict)


def test_control_a_confirmed_import_error_on_an_import_g6_cannot_satisfy_stays_withheld(request):
    # Review 3's finding 5, which the removed condition answered: the raise is the unsatisfied import itself.
    bindings = {"function": "subject.run", "cases": [{"args": [1], "raises": "ImportError"}]}
    disk = support.disk_for(request.node.nodeid)
    reading, found, ran = support.one_reading(disk / "tree", "example.v1",
                                              f"def run(value):\n    import {X}\n    return value\n",
                                              bindings=bindings)
    assert reading["result"] == "error" and support.UNAVAILABLE in reading["detail"], found
