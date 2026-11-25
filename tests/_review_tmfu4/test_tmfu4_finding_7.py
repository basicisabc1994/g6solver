"""Review 4 of claude/template-methods-fu (1464c340), finding 7.

Class: the host's file list reaches the runner only through a template instance's bindings; the program of a
person's confirmed expectation holds no such list, so there a deleted top-level module of the tree is still an
import G6 "could not satisfy".

Repair 3 answers review 3's finding 3(b) with ``own_modules``: ``_example`` and ``_differential`` write the tree's
top-level module names into the instance's data, ``request`` passes them to the bootstrap, and ``own_import_absent``
reads ``data['own_modules']``. A confirmed expectation does not run that way. Its check is the canonical scenario's
program (``executable_scenario._program_parts``), which builds ``data`` inside the child from the scenario's four
fields; ``Scenario.resolve`` reads the whole tree (``Tree.read``) and keeps the entry point only. So for the one
example.v1 check that is ``person_confirmed``, a change that deletes ``helpers.py`` and still imports it reads
G6_E_OBSERVATION_UNAVAILABLE, where the same function through a template instance reads failed (and 970da2d3 read
failed on both).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tmfu4_support as support  # noqa: E402
from g6.core.ledger import canonical  # noqa: E402
from g6.harness.executable_scenario import SETUP, Scenario  # noqa: E402
from tmfu4_support import runner  # noqa: E402

X = support.EXTERNAL
DELETED = "def run(value):\n    import helpers\n    return value\n"
LISTS = {"base": ["helpers.py"], "changed": ["helpers.py"]}   # the base commit held helpers.py; the change deletes it


def scenario_reading(folder, source, **lists):
    """A confirmed expectation's own program on the tree (``Scenario.program``, started as ``behaviour_runner.run``
    starts it, with an empty request), and the host's comparison of its frame for the chosen outcome."""
    data = support.applied(folder, "example.v1", **lists)
    support.write_tree(folder, source)
    scenario = Scenario(canonical({
        "version": "example-scenario.v2", "setup": SETUP,
        "entrypoint": [*data["function"], {"import_roots": [".", "src"]}],
        "input": {"args": [1], "kwargs": {}},
        "outcomes": {"A": {"expect": "returns", "value": 1}, "B": {"expect": "returns", "value": 2}},
    }).encode("ascii"))
    own = folder.parent / (folder.name + "-g6")
    own.mkdir(parents=True, exist_ok=True)
    boot, channel = own / "bootstrap.py", own / "result.bin"
    boot.write_text(scenario.program, encoding="utf-8")
    channel.write_bytes(b"")
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(folder), str(channel), "{}"],
                         capture_output=True, timeout=60)
    return runner.finish(ran.returncode, channel.read_bytes(), scenario.comparison("A")), ran


def test_a_confirmed_expectation_reads_a_deleted_top_level_module_as_the_codes_failure(request):
    disk = support.disk_for(request.node.nodeid)
    found, ran = scenario_reading(disk / "tree", DELETED, **LISTS)
    assert found["result"] == "failed", (
        "the change deleted the tree's own helpers.py and the function still imports it; the host's file lists "
        f"name it, and the confirmed expectation's program reads {found['result']!r}: {found['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_a_template_instance_reads_the_same_tree_as_failed(request):
    disk = support.disk_for(request.node.nodeid)
    reading, found, ran = support.one_reading(disk / "tree", "example.v1", DELETED, **LISTS)
    assert found["result"] == "failed", (found, ran.stderr)


def test_control_a_confirmed_expectation_runs_and_compares(request):
    disk = support.disk_for(request.node.nodeid)
    found, ran = scenario_reading(disk / "tree", support.RUNS, **LISTS)
    assert found["result"] == "passed", (found, ran.stderr)
    found, ran = scenario_reading(disk / "other", "def run(value):\n    return value + 1\n", **LISTS)
    assert found["result"] == "failed", (found, ran.stderr)


def test_control_a_confirmed_expectation_reads_an_external_package_as_unavailable(request):
    disk = support.disk_for(request.node.nodeid)
    found, ran = scenario_reading(disk / "tree", f"def run(value):\n    import {X}\n    return value\n", **LISTS)
    assert found["result"] == "error" and support.UNAVAILABLE in found["detail"], (found, ran.stderr)
