"""Finding 4 (review 3, lead L2's class): the explicit-body fallback admits a body by a deny-list.

Collection is unavailable (this machine's own case: no AppContainer), so one stated ``file::name`` is
admitted statically and the map says ``collection: not_required``. Real pytest, through the shipped
bootstrap, either passes without calling ``api.neighbour`` (which raises when called) or does not find
the selection at all.
"""
import pytest

from tests._review_cmfu3.helpers import BODY, HONEST_API, HONEST_TEST, NODE, before, scenario, show

OTHER = "def other():\n    pass\n"
REPLACED = {
    # the module that defines the admitted body is never scanned (L2 is its __test__ form)
    "definition_module_rewrites_code": {
        "files": {"test_api.py": "from body import test_call\n",
                  "body.py": BODY + OTHER + "test_call.__code__ = other.__code__\n"}},
    # nor is the module of a body the map goes on to follow
    "callee_module_rewrites_code": {
        "files": {"test_api.py": "from helpers import helper\ndef test_call():\n    helper()\n",
                  "helpers.py": "from api import neighbour\ndef helper():\n    neighbour()\n" + OTHER +
                                "helper.__code__ = other.__code__\n"}},
    # nor a module the selected file imports, which rebinds the selected name from outside
    "imported_module_rebinds_the_name": {
        "files": {"test_api.py": BODY + "import late\n",
                  "late.py": "import test_api\ntest_api.test_call = lambda: None\n"}},
    # and inside the selected file the scan knows __code__, exec and compile, not the other ways to rebind
    "globals_subscript_in_the_selected_file": {
        "files": {"test_api.py": BODY + "globals()['test_call'] = lambda: None\n"}},
    "setattr_in_the_selected_file": {
        "files": {"test_api.py": "import sys\n" + BODY +
                                 "setattr(sys.modules[__name__], 'test_call', lambda: None)\n"}},
    # "a known pytest mark" is decided by the spelling pytest.mark.<name>; the attribute can be the repository's
    "rebound_pytest_mark": {
        "files": {"test_api.py": "import pytest\nfrom api import neighbour\n"
                                 "pytest.mark.harmless = lambda fn: (lambda: None)\n"
                                 "@pytest.mark.harmless\ndef test_call():\n    neighbour()\n"}},
}
NOT_A_TEST = {"files": {"test_api.py": "from api import neighbour\ndef helper():\n    neighbour()\n"},
              "selection": "test_api.py::helper"}


@pytest.mark.parametrize("kind", sorted(REPLACED))
def test_static_admission_follows_a_body_pytest_does_not_run(tmp_path, kind):
    found = scenario({**REPLACED[kind], "root": str(tmp_path), "selection": NODE, "mode": "static"})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found   # pytest ran it; neighbour was never called
    sibling = found["sibling"]
    assert sibling["exercised"] is not True and sibling["checks"] == [], show(
        {"real_run": found["tail"], "exercised": sibling["exercised"], "checks": sibling["checks"],
         "collection": sibling["collection"]})


def test_static_admission_of_a_name_pytest_never_collects(tmp_path):
    found = scenario({**NOT_A_TEST, "root": str(tmp_path), "mode": "static"})
    assert found["exit"] == 4, found   # pytest: not found
    sibling = found["sibling"]
    assert sibling["exercised"] is not True and sibling["checks"] == [], show(
        {"real_run": found["tail"], "exercised": sibling["exercised"], "checks": sibling["checks"],
         "collection": sibling["collection"]})


def test_control_the_branch_base_did_not_admit_that_name(tmp_path):
    found = before("12a7305a", {**NOT_A_TEST, "mode": "static", "collector": False}, tmp_path)
    assert found["exit"] == 4 and found["sibling"]["exercised"] is False and found["sibling"]["checks"] == [], found


CONTROLS = {
    "honest_explicit_body": ({"test_api.py": HONEST_TEST}, HONEST_API, True),
    "honest_imported_body": ({"test_api.py": "from body import test_call\n", "body.py": HONEST_TEST}, HONEST_API, True),
    # the same writes where the scan looks: refused, as repair 2 intends
    "code_write_in_the_selected_file": ({"test_api.py": BODY + OTHER + "test_call.__code__ = other.__code__\n"},
                                        None, "uncertain"),
    "replacing_decorator_in_the_definition_module": (
        {"test_api.py": "from body import test_call\n",
         "body.py": "from api import neighbour\ndef swap(fn):\n    return lambda: None\n"
                    "@swap\ndef test_call():\n    neighbour()\n"}, None, "uncertain"),
}


@pytest.mark.parametrize("kind", sorted(CONTROLS))
def test_control_static_answers(tmp_path, kind):
    files, api, expected = CONTROLS[kind]
    spec = {"root": str(tmp_path), "files": files, "selection": NODE, "mode": "static"}
    if api is not None:
        spec["api"] = api
    found = scenario(spec)
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    sibling = found["sibling"]
    assert sibling["exercised"] == expected and (sibling["exercised"] is True) == (expected is True), show(sibling)
    if expected is True:
        assert sibling["checks"] == [NODE] and sibling["collection"] == "not_required", show(sibling)
