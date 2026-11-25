"""Review 4 of claude/template-methods-fu (1464c340), finding 4.

Class: "requested by repository code" is decided by the file of the frame that reaches the import machinery, so an
import the repository asks a standard-library helper to make is nobody's: when it fails for want of an installed
package, the failure is the code's.

(a) Regression of repair 3. ``track_imports`` now records a missing import only when the importing frame's file is
inside the tree (frames of ``importlib`` itself are stepped over, no other). ``pkgutil.resolve_name``,
``importlib.resources.files``, ``unittest.mock.patch`` and an entry point's ``load`` import the module the
repository names from their own frames, so nothing is recorded; the ModuleNotFoundError that escapes has no import instruction in its traceback, so
``unavailable_import`` says no. The call's reading is the code's own raise: a failed example and a counterexample,
and for differential.v1 a changed behaviour. b27712b1 recorded every failed import and read all of these
G6_E_OBSERVATION_UNAVAILABLE.

(b) Limit, as b27712b1. The repair's claim that provenance is kept "through guarded calls [and] fallback values"
holds for an import statement, ``__import__`` and ``importlib.import_module``. A guard that asks without
importing (``importlib.util.find_spec(name) is None``, the usual modern form), through a helper, or from executed
text takes its fallback with no record, and the fallback is the code's. And where the guard is recorded but the
function under test is defined only when the package is there, the missing callable ends the run in a
``call_failure.v1`` frame, which carries no mark: the code's failure again.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu4_support as support  # noqa: E402
from tmfu4_support import vs  # noqa: E402

X = support.EXTERNAL
GOOD = "def run(value):\n    return value\n"
HELPERS = {
    "pkgutil-resolve-name": f"import pkgutil\ndef run(value):\n    pkgutil.resolve_name('{X}:thing')\n    return value\n",
    "importlib-resources-files": (f"import importlib.resources\ndef run(value):\n"
                                  f"    importlib.resources.files('{X}')\n    return value\n"),
    "unittest-mock-patch": (f"from unittest import mock\ndef run(value):\n    with mock.patch('{X}.get'):\n"
                            "        return value\n"),
    "entry-point-load": (f"from importlib.metadata import EntryPoint\ndef run(value):\n"
                         f"    EntryPoint(name='plugin', value='{X}:thing', group='plugins').load()\n"
                         "    return value\n"),
}
GUARDED = {
    "find-spec-flag": (f"import importlib.util\nHAS = importlib.util.find_spec('{X}') is not None\n"
                       "def run(value):\n    return value if HAS else 0\n"),
    "helper-in-a-guard": (f"import pkgutil\ndef run(value):\n    try:\n        pkgutil.resolve_name('{X}')\n"
                          "    except ImportError:\n        return 0\n    return value\n"),
    "executed-text-in-a-guard": (f"def run(value):\n    try:\n        exec('import {X}')\n"
                                 "    except ImportError:\n        return 0\n    return value\n"),
}


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("helper", list(HELPERS))
def test_a_missing_package_asked_for_through_a_library_helper_is_unavailable(request, template, helper):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, template, (GOOD, None),
                                                                    (HELPERS[helper], None))
    assert on_patch["result"] == "error" and support.UNAVAILABLE in on_patch["detail"], (
        "G6's runner has no installed package, so the import the repository asked the helper to make failed; the "
        f"reading on the patch is {on_patch['result']!r} ({on_patch['detail']!r}) and the finding is "
        f"{verdict['result']!r}: {verdict['detail']!r}")


@pytest.mark.parametrize("guard", list(GUARDED))
def test_limit_a_fallback_taken_with_no_import_statement_is_not_a_counterexample(request, guard):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", (GOOD, None),
                                                                    (GUARDED[guard], None))
    assert verdict["result"] != vs.COUNTEREXAMPLE, (
        f"the reading on the patch is {on_patch['result']!r} ({on_patch['detail']!r}) and the finding is "
        f"{verdict['result']!r}: {verdict['detail']!r}")


CONDITIONAL = (f"try:\n    import {X}\nexcept ImportError:\n    {X} = None\n"
               f"if {X} is not None:\n    def run(value):\n        return value\n")


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_limit_a_function_defined_only_with_the_package_is_not_a_counterexample(request, template):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, template, (GOOD, None),
                                                                    (CONDITIONAL, None))
    assert verdict["result"] != vs.COUNTEREXAMPLE, (
        f"the reading on the patch is {on_patch['result']!r} ({on_patch['detail']!r}) and the finding is "
        f"{verdict['result']!r}: {verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("line", [f"import {X}", f"__import__('{X}')", f"importlib.import_module('{X}')"],
                         ids=["statement", "dunder-import", "import-module"])
def test_control_the_same_package_imported_by_the_code_itself_is_unavailable(request, template, line):
    patch = f"import importlib\ndef run(value):\n    {line}\n    return value\n"
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, template, (GOOD, None), (patch, None))
    assert on_patch["result"] == "error" and support.UNAVAILABLE in on_patch["detail"], found
    assert verdict["result"] == vs.CHECK_ERROR


@pytest.mark.parametrize("helper", list(HELPERS))
def test_control_the_runner_before_repair_3_read_the_helper_as_unavailable(request, helper):
    old = support.runner_at("b27712b1")
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", (GOOD, None),
                                                                    (HELPERS[helper], None), module=old)
    assert on_patch["result"] == "error" and support.UNAVAILABLE in on_patch["detail"], found


def test_control_a_guarded_import_statement_keeps_its_cause(request):
    patch = f"def run(value):\n    try:\n        import {X}\n    except ImportError:\n        return 0\n    return value\n"
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", (GOOD, None),
                                                                    (patch, None))
    assert verdict["result"] == vs.CHECK_ERROR, (verdict, found)
