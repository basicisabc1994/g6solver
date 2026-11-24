"""Review 3 of claude/template-methods-fu (b27712b1), finding 3.

Class: "the tree's own" decided from evidence that is not about the tree (repair 2's two new rules).

(a) ``track_imports`` records every ModuleNotFoundError of a module that is not the tree's own, and the standard
library's own optional imports are among them: ``import subprocess`` ends ``import fcntl`` in ModuleNotFoundError on
Windows (``msvcrt`` elsewhere), ``import pathlib`` ends ``import posix`` so on Windows. One such import while a
repository module loads fills ``_missing_preparation_imports``, and from then on a held module's missing name is
"guarded" whenever the held file binds that name ANYWHERE (``guarded_export`` walks every node: a local variable of
a function, a class attribute) or calls ``setattr``/``globals``/``locals``/``exec`` anywhere. Repair 2's claim "a
name the package never defines stays failed" then depends on whether the module under test imports ``subprocess``:
the same broken ``from held import fast`` reads as the code's failed call without it and as
G6_E_OBSERVATION_UNAVAILABLE with it, so a patch that removes the name is not refuted.

(b) A missing module with a one-part name is never the tree's own (``own_import_absent`` returns True for a missing
module only when the name is dotted, or is the module G6 itself imports). A patch that deletes or renames a
top-level module of the tree and leaves one import of it inside a function was a counterexample at 970da2d3
(control) and is G6_E_OBSERVATION_UNAVAILABLE now: repair 2's "the tree's own missing module ... stay outcomes"
holds for ``held.missing`` and not for ``helpers``.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu3_support as support  # noqa: E402

RUNS, UNAVAILABLE = support.RUNS, support.UNAVAILABLE
#: ``fast`` is a local variable of a function; the package never defines the name.
HELD = {"held/__init__.py": "def configure():\n    fast = 1\n    return fast\n"}
USES = "from held import fast\n" + RUNS
LAZY_OWN = "def run(value):\n    import helpers\n    return helpers.keep(value)\n"


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_a_name_the_package_never_defines_stays_failed_beside_a_standard_library_import(request, template):
    on_patch, found, ran = support.patch_reading(request, template, "import subprocess\n" + USES, HELD)
    assert on_patch["result"] == "failed" and on_patch["detail"] == "call failed: builtins.ImportError", (
        "held/__init__.py never defines `fast` (it is a local variable of one function), so `from held import "
        "fast` is the code's failed call, and reads so without `import subprocess`. With it, the standard "
        "library's own optional import (fcntl or msvcrt) is recorded as a missing preparation import and the "
        f"reading is {on_patch['result']!r} ({on_patch['detail']!r})")


def test_a_deleted_top_level_module_of_the_tree_imported_inside_the_call_is_the_codes_failure(request):
    # the base tree holds helpers.py; the patch deleted it and kept the function's import
    on_patch, found, ran = support.patch_reading(request, "example.v1", LAZY_OWN)
    assert on_patch["result"] == "failed", (
        "the patch removed the tree's own top-level module `helpers` and the function still imports it: the call "
        f"raises ModuleNotFoundError in every interpreter. G6 reads {on_patch['result']!r} "
        f"({on_patch['detail']!r}), as it reads an installed package it cannot import")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_control_the_same_missing_name_without_the_standard_library_import_is_failed(request, template):
    on_patch, found, ran = support.patch_reading(request, template, USES, HELD)
    assert on_patch["result"] == "failed" and on_patch["detail"] == "call failed: builtins.ImportError", (
        found, ran.stderr)


def test_control_a_package_that_binds_the_name_nowhere_is_failed_beside_the_standard_library_import(request):
    on_patch, found, ran = support.patch_reading(request, "example.v1", "import subprocess\n" + USES,
                                                 {"held/__init__.py": "kept = 1\n"})
    assert on_patch["result"] == "failed" and on_patch["detail"] == "call failed: builtins.ImportError", (
        found, ran.stderr)


def test_control_the_runner_before_the_repair_read_the_missing_name_as_failed(request):
    earlier = support.runner_at("970da2d3")
    on_patch, found, ran = support.patch_reading(request, "example.v1", "import subprocess\n" + USES, HELD,
                                                 module=earlier)
    assert on_patch["result"] == "failed" and on_patch["detail"] == "call failed: builtins.ImportError", (
        found, ran.stderr)


def test_control_the_runner_before_the_repair_read_the_deleted_module_as_the_codes_failure(request):
    earlier = support.runner_at("970da2d3")
    on_patch, found, ran = support.patch_reading(request, "example.v1", LAZY_OWN, module=earlier)
    assert on_patch["result"] == "failed", (found, ran.stderr)


def test_control_a_missing_submodule_of_a_tree_package_inside_the_call_is_the_codes_failure(request):
    on_patch, found, ran = support.patch_reading(
        request, "example.v1", "def run(value):\n    import held.missing\n    return value\n",
        {"held/__init__.py": ""})
    assert on_patch["result"] == "failed", (found, ran.stderr)
