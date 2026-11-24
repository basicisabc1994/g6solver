"""Review 3 of claude/template-methods-fu (b27712b1), finding 5.

Class: a state read as more certain than its evidence (the other side of repair 2's rule for finding 1).

Repair 2 marks a call's outcome ``unavailable_import`` when the ImportError it raised is an import G6's isolated
interpreter could not satisfy, and ``finish`` reads that as G6_E_OBSERVATION_UNAVAILABLE "when the confirmed outcome
does not name it". When the confirmed outcome is ``raises ImportError`` (or ModuleNotFoundError), the marked outcome
is taken as the confirmed raise: ``passed``. A function that imports an installed package before it does its work
then passes its confirmed example in G6's runner whatever the patch made of the work, because the runner's own
missing package raises first. The reading says the confirmed behaviour was observed; what was observed is that G6's
interpreter has no ``pydantic``, which the same record names (``unavailable_import``).

Here the person confirmed "run(1) raises ImportError" (an unknown plug-in is refused); the patch returns a value
instead. In the person's interpreter that is a counterexample; G6 reads ``passed``.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tmfu3_support as support  # noqa: E402
from tmfu3_support import before, vs  # noqa: E402

BINDINGS = {"function": "subject.run", "cases": [{"args": [1], "raises": "ImportError"}]}
REFUSES = "def run(value):\n    raise ImportError('no plug-in named %r' % value)\n"
RETURNS = "def run(value):\n    return value\n"
LAZY = "    import pydantic\n"


def _reading(folder, tree_source):
    _, data = before.applied(folder, "example.v1", "subject", REFUSES, BINDINGS)
    (folder / "subject.py").write_text(tree_source, encoding="utf-8")
    found, ran = before.observed(folder, data)
    return before.read(found), found, ran


def _both(request, base_source, patch_source):
    disk = support.disk_for(request.node.nodeid)
    at_base, _, _ = _reading(disk / "base", base_source)
    on_patch, found, ran = _reading(disk / "patch", patch_source)
    return at_base, on_patch, vs.classify("preserve", on_patch, at_base), found, ran


def test_an_import_the_runner_cannot_satisfy_is_not_the_confirmed_raise(request):
    import pydantic  # noqa: F401  in the person's interpreter the patched function returns its value
    with_import = lambda source: source.replace(":\n", ":\n" + LAZY, 1)  # noqa: E731
    at_base, on_patch, verdict, found, ran = _both(request, with_import(REFUSES), with_import(RETURNS))
    assert on_patch["result"] != "passed", (
        "the patched function no longer raises the confirmed ImportError; it returns. G6's isolated interpreter "
        "could not import the installed package the function imports first, recorded that "
        f"({found.get('observed', {}).get('outcomes', [{}])[0].get('unavailable_import')!r}), and reads the "
        f"confirmed raise as observed: {on_patch['result']!r} ({on_patch['detail']!r}); the finding is "
        f"{verdict['result']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_the_confirmed_raise_with_no_other_import_passes(request):
    at_base, on_patch, verdict, found, ran = _both(request, REFUSES, REFUSES)
    assert on_patch["result"] == "passed" and verdict["result"] == vs.NOT_REFUTED, (found, ran.stderr)


def test_control_the_patch_that_returns_with_no_other_import_is_a_counterexample(request):
    at_base, on_patch, verdict, found, ran = _both(request, REFUSES, RETURNS)
    assert on_patch["result"] == "failed" and verdict["result"] == vs.COUNTEREXAMPLE, (found, ran.stderr)


def test_control_the_same_import_before_a_confirmed_return_is_unavailable(request):
    disk = support.disk_for(request.node.nodeid)
    on_patch, found, ran = before.runner_reading(disk / "patch", "example.v1", RETURNS,
                                                 "def run(value):\n" + LAZY + "    return value\n")
    assert on_patch["result"] == "error" and support.UNAVAILABLE in on_patch["detail"], (found, ran.stderr)
