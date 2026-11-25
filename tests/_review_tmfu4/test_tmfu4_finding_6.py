"""Review 4 of claude/template-methods-fu (1464c340), finding 6 (a limit).

Class: the host's ownership evidence is every top-level module name of the tree, written into each example.v1 and
differential.v1 instance's bindings, so it counts against two fixed sizes that had nothing to do with the
repository's size before.

``_own_modules`` lists every module and package directly under the import roots, the top folder and ``src``, on
the working tree, at the base commit and in the change. The list is part of the compiled check's one literal
(``compile_text``), whose text may hold 64 kilobytes (``compiled_check``: "its compiled check would hold more than
64 kilobytes"), and of the request on the runner's command line (``request``; ``run`` passes it as one argument,
and Windows allows 32,767 characters for the whole line). The fixed text of an example.v1 check is about 31
kilobytes, so about 1,500 top-level modules with names of 18 characters leave the instance inapplicable; on
Windows the command line is passed first, at about 1,400, where ``run`` reads the failed start as "no single
completed observation". b27712b1 compiled and ran these instances whatever the tree held.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu4_support as support  # noqa: E402
from tmfu4_support import runner, vt  # noqa: E402

WINDOWS_COMMAND_LINE = 32767


def instance(template):
    return vt.TemplateInstance(id="one", template=template, requirement_id="R1", intent="preserve",
                               bindings=support.default_bindings(template))


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_an_instance_compiles_in_a_tree_of_two_thousand_top_level_modules(request, template):
    disk = support.disk_for(request.node.nodeid)
    known = ["module_number_%04d.py" % index for index in range(2000)]
    data = support.applied(disk, template, known=known)
    try:
        vt.compiled_check(disk, instance(template), data, [])
    except vt.Inapplicable as exc:
        pytest.fail(f"the function and its one call are unchanged; the instance is refused: {exc} "
                    f"(the bindings hold {len(data['own_modules'])} module names)")


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_the_runners_request_fits_a_command_line_in_a_tree_of_fifteen_hundred_top_level_modules(request, template):
    disk = support.disk_for(request.node.nodeid)
    known = ["module_number_%04d.py" % index for index in range(1500)]
    data = runner.located(disk, support.applied(disk, template, known=known))
    payload = json.dumps(runner.request(data))
    assert len(payload) < WINDOWS_COMMAND_LINE, (
        f"the request G6 passes on the runner's command line holds {len(payload)} characters "
        f"({len(data['own_modules'])} module names); Windows starts no process with a longer line")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_control_two_thousand_modules_below_the_top_level_cost_nothing(request, template):
    disk = support.disk_for(request.node.nodeid)
    known = ["package/inner/module_number_%04d.py" % index for index in range(2000)]
    data = runner.located(disk, support.applied(disk, template, known=known))
    vt.compiled_check(disk, instance(template), data, [])
    assert len(json.dumps(runner.request(data))) < 1000
