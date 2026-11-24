"""Review 4 (repair 3, 794f766b..6d22aa4a): honest repositories that still get no definite answer.

Every repository here is honest: real pytest collects ``test_call``, runs it, and it calls ``api.neighbour``
("1 passed"). No file patches pytest, shadows a name, or rewrites a body. The collected cases go through the
shipped collection bootstrap, the reader and the map (review 3's ``helpers.scenario``); the controls run an earlier
commit's own modules on the same data (review 3's ``before.py``).
"""
import pytest

from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, before, scenario, show

BEFORE_REPAIR_2 = "6ef4fdc4"
REPAIR_2 = "794f766b"
PLAIN = {"files": {"test_api.py": HONEST_TEST}, "api": HONEST_API}
FIXTURE_TEST = "from api import neighbour\ndef test_call(two):\n    assert neighbour() == two\n"
# Finding 1: one ordinary line anywhere in the repository turns identity off for every collected check.
ORDINARY = {
    # the claim: "conftest and plugin hooks"; the code: any file named conftest.py
    "a_conftest_with_only_a_fixture": {"files": {
        "test_api.py": FIXTURE_TEST,
        "conftest.py": "import pytest\n@pytest.fixture\ndef two():\n    return 2\n"}, "api": HONEST_API},
    # a pytest section that changes nothing about collection
    "a_pytest_ini_with_only_a_minimum_version": {"files": {
        "test_api.py": HONEST_TEST, "pytest.ini": "[pytest]\nminversion = 6.0\n"}, "api": HONEST_API},
    # parametrize values built from a record type defined in the test file
    "parametrize_cases_built_from_a_local_record": {"files": {"test_api.py": (
        "from typing import NamedTuple\nimport pytest\nfrom api import neighbour\n"
        "class Case(NamedTuple):\n    want: int\n"
        "@pytest.mark.parametrize('case', [Case(2)])\n"
        "def test_call(case):\n    assert neighbour() == case.want\n")}, "api": HONEST_API},
    # a configuration object made at import
    "a_module_level_settings_object": {"files": {"test_api.py": HONEST_TEST}, "api": HONEST_API + (
        "class Settings:\n    debug = False\nSETTINGS = Settings()\n")},
    # a registering decorator that returns the same function, defined in another module
    "a_registering_decorator_from_another_module": {"files": {
        "test_api.py": HONEST_TEST,
        "registry.py": "HANDLERS = []\ndef register(function):\n    HANDLERS.append(function)\n    return function\n"},
        "api": HONEST_API + "from registry import register\n@register\ndef handler():\n    return 3\n"},
    # an environment variable set for the tests
    "an_environment_variable_set_in_the_test_file": {"files": {"test_api.py": (
        "import os\nos.environ['APP_ENV'] = 'test'\n" + HONEST_TEST)}, "api": HONEST_API},
    # one file that is not this interpreter's Python (old script, template, lint fixture), never imported
    "an_unparsable_script_nobody_imports": {"files": {
        "test_api.py": HONEST_TEST, "examples/legacy.py": 'print "hello"\n'}, "api": HONEST_API},
}
# Finding 2: pytest collects a file named on the command line whatever python_files says.
EXPLICIT_FILE = {"files": {"checks.py": HONEST_TEST}, "api": HONEST_API, "mode": "static",
                 "selection": "checks.py::test_call"}


def _state(found):
    sibling = found["sibling"]
    return {"authority": found.get("authority"), "real_run": found.get("tail"), "exercised": sibling["exercised"],
            "checks": sibling["checks"], "collection": sibling["collection"],
            "unresolved": (found.get("answer") or {}).get("unresolved"),
            "reason": (found.get("answer") or {}).get("reason")}


def _kept(found):
    assert found["sibling"]["exercised"] is True and found["sibling"]["checks"], show(_state(found))


def test_control_a_plain_repository_keeps_its_positive(tmp_path):
    found = scenario({**PLAIN, "root": str(tmp_path)})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    _kept(found)


@pytest.mark.parametrize("kind", sorted(ORDINARY))
def test_ordinary_repository_keeps_its_positive(tmp_path, kind):
    found = scenario({**ORDINARY[kind], "root": str(tmp_path)})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found   # honest: the test really calls neighbour
    _kept(found)


@pytest.mark.parametrize("kind", sorted(ORDINARY))
def test_control_the_same_repository_kept_it_before_repair_2(tmp_path, kind):
    _kept(before(BEFORE_REPAIR_2, ORDINARY[kind], tmp_path))


def test_explicit_selection_in_a_file_outside_python_files_keeps_its_positive(tmp_path):
    found = scenario({**EXPLICIT_FILE, "root": str(tmp_path)})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found   # pytest collected and ran checks.py::test_call
    _kept(found)


def test_control_repair_2_answered_that_selection(tmp_path):
    _kept(before(REPAIR_2, EXPLICIT_FILE, tmp_path))


def test_control_a_test_named_file_keeps_its_static_positive(tmp_path):
    found = scenario({"files": {"test_api.py": HONEST_TEST}, "api": HONEST_API, "mode": "static",
                      "selection": "test_api.py::test_call", "root": str(tmp_path)})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    _kept(found)
