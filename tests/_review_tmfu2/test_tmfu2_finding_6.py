"""Review 2 of claude/template-methods-fu (970da2d3), finding 6.

Class: a fixed wording used for a cause it does not describe (a state read as more specific than the evidence).

Repair 1's amendment keeps an ordinary setup error of a differential probe ``G6_E_OBSERVATION_UNAVAILABLE`` by
replacing whatever the setup refused with a bare ``ObservationUnavailable()`` (the bootstrap's ``setup_failed``),
whose fixed detail is "import or lazy lookup did not complete". So a probe whose constructor raised, whose bound
attribute is not callable, or whose method lookup raised now says an import or lazy lookup did not complete; no
import failed. The stable code is the ruled one and is unchanged; the words after it name the wrong cause. Before
the repair the same runs said "no single completed observation", and the confirmed example on the same tree says
``G6_E_CHECK_SETUP: constructor`` / ``method_lookup``.

No confined run is needed: G6's fixed bootstrap is started as ``behaviour_runner.run`` starts it (``-I -S -B``).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu2_support as support  # noqa: E402
from g6.harness import behaviour_runner as runner  # noqa: E402

STATED = '''
class Box:
    def __init__(self):
        self.n = 0
    def act(self, value):
        return value
'''
METHOD = "    def act(self, value):\n        return value\n"
CASES = {
    "constructor-raises": STATED.replace("self.n = 0", "raise ValueError('no')"),
    "attribute-is-not-callable": STATED.replace(METHOD, "    act = 7\n"),
    "method-lookup-raises": STATED.replace(METHOD, "    @property\n    def act(self):\n        raise ValueError('no')\n"),
}
IMPORT_FAILS = "import pydantic\n" + STATED


def _found(folder, template, source, module=runner):
    bindings = ({"function": "subject.Box.act", "constructor": {}, "cases": [{"args": [1], "returns": 1}]}
                if template == "example.v1" else
                {"function": "subject.Box.act", "constructor": {}, "mode": "outputs", "calls": [{"args": [1]}]})
    _, data = support.applied(folder, template, "subject", STATED, bindings)
    (folder / "subject.py").write_text(source, encoding="utf-8")
    found, ran = support.observed(folder, data, module)
    return found


@pytest.mark.parametrize("case", list(CASES))
def test_a_probes_setup_error_that_is_no_import_does_not_say_an_import_did_not_complete(request, case):
    found = _found(support.disk_for(request.node.nodeid) / "tree", "differential.v1", CASES[case])
    assert found["result"] == "error" and found["detail"].startswith("G6_E_OBSERVATION_UNAVAILABLE"), found
    assert "import" not in found["detail"], (
        f"the probe's setup failed with no import involved ({case}); G6's detail names an import or lazy lookup "
        f"as what did not complete: {found['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_a_probe_whose_import_fails_says_so(request):
    found = _found(support.disk_for(request.node.nodeid) / "tree", "differential.v1", IMPORT_FAILS)
    assert found["detail"] == ("G6_E_OBSERVATION_UNAVAILABLE: import or lazy lookup did not complete: "
                               "builtins.ModuleNotFoundError"), found


@pytest.mark.parametrize("case, detail", [("constructor-raises", "G6_E_CHECK_SETUP: constructor"),
                                          ("attribute-is-not-callable", "G6_E_CHECK_SETUP: method_lookup"),
                                          ("method-lookup-raises", "G6_E_CHECK_SETUP: method_lookup")])
def test_control_the_confirmed_example_names_the_phase(request, case, detail):
    found = _found(support.disk_for(request.node.nodeid) / "tree", "example.v1", CASES[case])
    assert found["result"] == "error" and found["detail"] == detail, found


@pytest.mark.parametrize("case", list(CASES))
def test_control_before_the_repair_the_probe_named_no_cause(request, case):
    before = support.parent_runner("9203d9c6")
    found = _found(support.disk_for(request.node.nodeid) / "tree", "differential.v1", CASES[case], before)
    assert found == {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: no single completed observation"}
