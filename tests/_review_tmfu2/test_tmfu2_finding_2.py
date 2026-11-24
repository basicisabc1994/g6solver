"""Review 2 of claude/template-methods-fu (970da2d3), finding 2.

Class: a decision made from the last symptom, not from its cause (review 1's findings 2 and 4 against each other).

Repair 1 reads ``from held import name`` that fails as the tree's own failed call whenever ``held`` is a module or
regular package of the tree (the ImportError's ``name`` and ``path``). A name a held package defines only where an
installed package is present (``try: import yaml ... except ImportError: pass``, the ordinary optional-dependency
guard) is missing in G6's isolated interpreter for the reason finding 2 of review 1 named, an import the runner
cannot satisfy; the guard swallowed that ModuleNotFoundError one statement earlier, and only the second symptom
reaches ``own_import_absent``. G6 reads ``failed`` ("call failed: builtins.ImportError"), a counterexample for a
patch that adds the guarded feature, where the person's interpreter imports the name and the call returns.

No confined run is needed: G6's fixed bootstrap is started as ``behaviour_runner.run`` starts it (``-I -S -B``).
``pydantic`` stands for an installed package: this interpreter has it, G6's isolated child does not.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu2_support as support  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

RUNS = support.RUNS
USES = "from held import fast\n" + RUNS
GUARDED = "try:\n    import pydantic\n    fast = 1\nexcept ImportError:\n    pass\n"
GUARDED_PART = "try:\n    from ._speed import fast\nexcept ImportError:\n    pass\n"
SPEED = "import pydantic\nfast = 1\n"
UNAVAILABLE = "G6_E_OBSERVATION_UNAVAILABLE"


def _base_and_patch(request, template, patch_source, extra=None):
    disk = support.disk_for(request.node.nodeid)
    at_base, _, _ = support.runner_reading(disk / "base", template, RUNS, RUNS)
    on_patch, found, ran = support.runner_reading(disk / "patch", template, RUNS, patch_source, extra)
    return at_base, on_patch, found, ran


@pytest.mark.parametrize("extra", [{"held/__init__.py": GUARDED},
                                   {"held/__init__.py": GUARDED_PART, "held/_speed.py": SPEED}],
                         ids=["guarded-in-the-package", "guarded-submodule"])
def test_a_name_missing_because_the_runner_lacks_an_installed_package_is_not_the_codes_failed_call(request, extra):
    # in this interpreter (the person's), which has the installed package, the held package defines the name
    probe = support.disk_for(request.node.nodeid + "-person")
    for rel, text in extra.items():
        path = probe.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    import subprocess
    person = subprocess.run([sys.executable, "-B", "-c", "from held import fast; print(fast)"], cwd=probe,
                            capture_output=True, text=True, timeout=60)
    assert person.stdout.strip() == "1", person.stderr
    at_base, on_patch, found, ran = _base_and_patch(request, "example.v1", USES, extra)
    verdict = vs.classify("preserve", on_patch, at_base)
    assert on_patch["result"] == "error" and UNAVAILABLE in on_patch["detail"], (
        "the held package defines the name only where an installed package is present; G6's isolated interpreter "
        "has none, the package's own guard swallowed that, and G6 reads the missing name as the code's failed "
        f"call: {on_patch['result']!r} ({on_patch['detail']!r}); the finding is {verdict['result']!r}: "
        f"{verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_a_name_the_held_package_never_defines_is_the_codes_failed_call(request):
    at_base, on_patch, found, ran = _base_and_patch(request, "example.v1", USES, {"held/__init__.py": "kept = 1\n"})
    assert on_patch["result"] == "failed" and on_patch["detail"] == "call failed: builtins.ImportError", (
        found, ran.stderr)
    assert vs.classify("preserve", on_patch, at_base)["result"] == vs.COUNTEREXAMPLE


def test_control_the_unguarded_installed_import_is_unavailable(request):
    at_base, on_patch, found, ran = _base_and_patch(request, "example.v1", USES,
                                                    {"held/__init__.py": "import pydantic\nfast = 1\n"})
    assert on_patch["result"] == "error" and UNAVAILABLE in on_patch["detail"], (found, ran.stderr)


def test_control_the_runner_before_the_repair_read_the_guarded_name_as_unavailable(request):
    disk = support.disk_for(request.node.nodeid)
    before = support.parent_runner("9203d9c6")
    on_patch, found, ran = support.runner_reading(disk / "patch", "example.v1", RUNS, USES,
                                                  {"held/__init__.py": GUARDED}, module=before)
    assert on_patch["result"] == "error" and on_patch["detail"] == (
        UNAVAILABLE + ": import or lazy lookup did not complete: builtins.ImportError"), (found, ran.stderr)


def test_control_a_guard_that_defines_the_name_either_way_passes(request):
    guard = "try:\n    import pydantic\n    fast = 1\nexcept ImportError:\n    fast = 0\n"
    at_base, on_patch, found, ran = _base_and_patch(request, "example.v1", USES, {"held/__init__.py": guard})
    assert on_patch["result"] == "passed", (found, ran.stderr)
