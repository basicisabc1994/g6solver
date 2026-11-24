"""Review 3 of claude/template-methods-fu (b27712b1), finding 2.

Class: a missing import decides the reading only when an ImportError arrives; the same cause that arrives as a
value, a flag or a missing attribute is the code's own behaviour (review 2's finding 1's root, "one rule for an
import the runner cannot satisfy, wherever it surfaces", for a guard that does not raise an ImportError).

The optional-dependency guard (``try: import pydantic / except ImportError: pydantic = None``) is the ordinary way a
repository relies on an installed package. In G6's isolated interpreter (``-I -S``) the guard always takes its
fallback, so the observed call returns the fallback's value or raises the fallback's error. ``track_imports`` holds
the missing import (``_missing_imports``, and ``_missing_preparation_imports`` when the guard is at the top of the
module), and the reading is still the code's outcome: a failed confirmed example and a counterexample, or a changed
behaviour for a probe, where the person's interpreter returns the confirmed value.

A further shape needs no guard: a folder of the tree with no ``__init__.py`` whose name is an installed package's
(``docker/`` holding a Dockerfile, beside ``import docker``). G6's interpreter imports the folder as an empty
namespace package (the person's interpreter prefers the installed regular package), no import fails, and the first
attribute read is the code's AttributeError.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu3_support as support  # noqa: E402
from tmfu3_support import vs  # noqa: E402

RUNS, UNAVAILABLE = support.RUNS, support.UNAVAILABLE
SHAPES = {
    "fallback-value-inside-the-call": (
        "def run(value):\n    try:\n        import pydantic\n    except ImportError:\n        return 0\n"
        "    return value\n", None),
    "fallback-flag-at-the-top": (
        "try:\n    import pydantic\nexcept ImportError:\n    pydantic = None\n"
        "def run(value):\n    if pydantic is None:\n        raise RuntimeError('pydantic is required')\n"
        "    return value\n", None),
    "guarded-attribute-of-a-held-package": (
        "import held\ndef run(value):\n    held.fast\n    return value\n",
        {"held/__init__.py": "try:\n    import pydantic\n    fast = 1\nexcept ImportError:\n    pass\n"}),
    "a-tree-folder-named-as-an-installed-package": (
        "import docker\ndef run(value):\n    docker.from_env\n    return value\n",
        {"docker/Dockerfile": "FROM scratch\n"}),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_fallback_taken_because_the_runner_lacks_a_package_is_not_a_counterexample(request, shape):
    source, extra = SHAPES[shape]
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", source, extra)
    assert at_base["result"] == "passed", at_base
    assert verdict["result"] != vs.COUNTEREXAMPLE, (
        "G6's isolated interpreter has no installed package, so the code took the path it takes without one (G6 "
        f"recorded the missing import, or imported a folder of the tree in the package's place); the reading is "
        f"{on_patch['result']!r} ({on_patch['detail']!r}) and the finding is {verdict['result']!r}: "
        f"{verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("source,extra", [
    ("import pydantic\n" + RUNS, None),
    ("from docker import from_env\n" + RUNS, {"docker/Dockerfile": "FROM scratch\n"}),
], ids=["unguarded-import", "a-name-from-the-tree-folder"])
def test_control_the_same_cause_that_arrives_as_an_import_error_is_unavailable(request, source, extra):
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", source, extra)
    assert on_patch["result"] == "error" and UNAVAILABLE in on_patch["detail"], (found, ran.stderr)
    assert verdict["result"] == vs.CHECK_ERROR


def test_control_a_guard_whose_fallback_returns_the_confirmed_value_passes(request):
    source = ("try:\n    import pydantic\nexcept ImportError:\n    pydantic = None\n" + RUNS)
    at_base, on_patch, verdict, found, ran = support.base_and_patch(request, "example.v1", source)
    assert on_patch["result"] == "passed" and verdict["result"] == vs.NOT_REFUTED, (found, ran.stderr)
