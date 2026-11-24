"""Review 3 of claude/template-methods-fu (b27712b1), finding 1.

Class: a cause G6 records and then does not read (review 2's findings 1 and 2, for the shapes repair 2's two rules
leave between them).

Repair 2 records every import of a non-own module that could not be found (``track_imports``: ``_missing_imports``),
"even if a guard catches them". The record is read in two places only: an ImportError that reaches the call boundary
is unavailable when it IS the recorded exception object or its frame stopped on an import instruction
(``unavailable_import``), and a held module's missing name is unavailable when a non-own import failed DURING
PREPARATION (``_missing_preparation_imports``, filled only while ``setup_call`` is on the stack). So:

- the ordinary lazy optional import, ``try: import pydantic / except ImportError: raise ImportError("install ...")``
  inside the function, raises a new exception object from a ``raise`` statement: the recorded cause is its
  ``__context__``, and G6 reads the code's own failure (a counterexample, or a changed behaviour for a probe);
- ``from held import fast`` inside the function, where the held package defines ``fast`` only beside an installed
  package (review 2's finding 2, moved into the call): the guard's import failed during the call, so the
  preparation set is empty and the missing name is the code's failed call.

The same statements at the top of the module read G6_E_OBSERVATION_UNAVAILABLE (controls). ``pydantic`` stands for an
installed package: this interpreter has it, G6's isolated child (``-I -S``) does not.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu3_support as support  # noqa: E402
from tmfu3_support import vs  # noqa: E402

RUNS, UNAVAILABLE = support.RUNS, support.UNAVAILABLE
GUARDED = "try:\n    import pydantic\n    fast = 1\nexcept ImportError:\n    pass\n"
FRIENDLY = ("def run(value):\n    try:\n        import pydantic\n    except ImportError:\n"
            "        raise ImportError('install pydantic to use run()')\n    return value\n")
FRIENDLY_FROM = ("def run(value):\n    try:\n        import pydantic\n    except ImportError as exc:\n"
                 "        raise RuntimeError('install pydantic to use run()') from exc\n    return value\n")
LAZY_HELD = "def run(value):\n    from held import fast\n    return value\n"
SHAPES = {"friendly-import-error": (FRIENDLY, None), "friendly-error-from-the-cause": (FRIENDLY_FROM, None),
          "held-name-behind-a-guard": (LAZY_HELD, {"held/__init__.py": GUARDED})}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_guarded_import_the_runner_cannot_satisfy_inside_the_call_is_not_a_counterexample(request, shape):
    import pydantic  # noqa: F401  the person's interpreter has the package: the patched function returns its value
    source, extra = SHAPES[shape]
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", source, extra)
    assert at_base["result"] == "passed", at_base
    assert on_patch["result"] == "error" and UNAVAILABLE in on_patch["detail"], (
        "the patched function's import of an installed package failed in G6's isolated interpreter and nowhere "
        "else; G6 recorded that missing import and still reads the call as the code's own outcome: "
        f"{on_patch['result']!r} ({on_patch['detail']!r}); the finding is {verdict['result']!r}: "
        f"{verdict['detail']!r}")


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_guarded_import_the_runner_cannot_satisfy_inside_the_call_is_not_a_changed_behaviour(request, shape):
    source, extra = SHAPES[shape]
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "differential.v1", source, extra)
    assert at_base["result"] == "passed", at_base
    assert verdict["result"] not in (vs.BEHAVIOUR_CHANGED, vs.COUNTEREXAMPLE), (
        "the probe reports, as the patch's behaviour, an error that exists only because G6's isolated interpreter "
        f"has no installed package (a missing import G6 recorded): {verdict}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_control_the_guard_that_raises_the_same_exception_again_is_unavailable(request, template):
    source = ("def run(value):\n    try:\n        import pydantic\n    except ImportError:\n        raise\n"
              "    return value\n")
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, template, source)
    assert on_patch["result"] == "error" and on_patch["detail"] == (
        UNAVAILABLE + ": import or lazy lookup did not complete: builtins.ModuleNotFoundError"), (found, ran.stderr)
    assert verdict["result"] == vs.CHECK_ERROR


def test_control_the_friendly_error_at_the_top_of_the_module_is_unavailable(request):
    source = "try:\n    import pydantic\nexcept ImportError:\n    raise ImportError('install pydantic')\n" + RUNS
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", source)
    assert on_patch["result"] == "error" and UNAVAILABLE in on_patch["detail"], (found, ran.stderr)


def test_control_the_guarded_held_name_imported_at_the_top_of_the_module_is_unavailable(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "example.v1", "from held import fast\n" + RUNS, {"held/__init__.py": GUARDED})
    assert on_patch["result"] == "error" and UNAVAILABLE in on_patch["detail"], (found, ran.stderr)


def test_control_an_import_error_the_code_raises_with_no_import_is_its_outcome(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "example.v1", "def run(value):\n    raise ImportError('no')\n")
    assert on_patch["result"] == "failed" and verdict["result"] == vs.COUNTEREXAMPLE, (found, ran.stderr)


def test_control_a_held_name_never_defined_and_imported_inside_the_call_is_the_codes_failure(request):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(
        request, "example.v1", LAZY_HELD, {"held/__init__.py": "kept = 1\n"})
    assert on_patch["result"] == "failed" and verdict["result"] == vs.COUNTEREXAMPLE, (found, ran.stderr)
