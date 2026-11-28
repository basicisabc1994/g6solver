"""Findings 1 to 3 (review 3): G6's pre-launch read grants identity while repository code runs in the child.

Each case runs real pytest twice through the shipped bootstrap (collection, then the test itself). The test
passes although ``api.neighbour`` raises when called, so the selected check never called it; the collection
report is the unmodified producer's, with the content identity G6 expects; and the map still names the
check as exercising ``neighbour``. The controls beside them keep their definite answers.
"""
import pytest

from tests._review_cmfu3.helpers import BODY, HONEST_API, HONEST_TEST, TWIN, cache_name, scenario, show

RUN_TWIN = TWIN + "_twin()\n"
# Root A (lead L1's class): the authority inventory is git's listing; Python and pytest load what the disk holds.
INVENTORY = {
    # a generated module git ignores (setuptools-scm's _version.py is the everyday shape), no shadowing needed
    "ignored_package_submodule": {
        "files": {"test_api.py": "from pkg import _version\n" + BODY, "pkg/__init__.py": "",
                  ".gitignore": "_version.py\n"},
        "ignored": {"pkg/_version.py": RUN_TWIN}},
    # a listed, passive module, loaded from its bytecode cache: the cache's header matches the file, its code does not
    "stale_bytecode_cache": {
        "files": {"test_api.py": "import helper\n" + BODY, "helper.py": "VALUE = 1\n",
                  ".gitignore": "__pycache__/\n"},
        "ignored": {cache_name("helper.py"): {"stale_cache_of": "helper.py", "source": "VALUE = 1\n" + RUN_TWIN}}},
    # an ignored conftest.py in a folder that holds no listed Python file, reached by a folder selection
    "ignored_conftest_in_unlisted_folder": {
        "files": {"tests/test_api.py": BODY, ".gitignore": "a_data/\n"},
        "ignored": {"tests/a_data/conftest.py": RUN_TWIN}, "selection": "tests"},
}
# Root B (lead L3's class): the scan names a decorator by its text, not by what the name is bound to.
NAMES = {
    "class_named_pytest": {"files": {"test_api.py": (
        "from api import neighbour\n"
        "class pytest:\n    class mark:\n        def harmless(fn):\n            import types\n"
        "            return types.FunctionType(fn.__code__, {'neighbour': lambda: None}, fn.__name__)\n"
        "@pytest.mark.harmless\ndef test_call():\n    neighbour()\n")}},
    "own_staticmethod": {"files": {"test_api.py": (
        "from api import neighbour\n"
        "def staticmethod(fn):\n    import types\n"
        "    return types.FunctionType(fn.__code__, {**fn.__globals__, 'neighbour': lambda: None}, fn.__name__)\n"
        "class TestAPI:\n    @staticmethod\n    def test_call(self):\n        neighbour()\n")}},
}
# Root C (new): collection itself calls repository callables the scan never looks inside.
CALLBACKS = {
    "parametrize_ids_callable": {"files": {"test_api.py": (
        "import pytest\nfrom api import neighbour\n" + TWIN +
        "def label(value):\n    _twin()\n    return 'x'\n"
        "@pytest.mark.parametrize('value', [1], ids=label)\ndef test_call(value):\n    neighbour()\n")}},
    # pytest builds one TestCase at collection (UnitTestCase.newinstance); TestCase.__init__ calls this method
    "unittest_constructor_callback": {"files": {"test_api.py": (
        "import unittest\nfrom api import neighbour\n"
        "class TestAPI(unittest.TestCase):\n"
        "    def addTypeEqualityFunc(self, typeobj, function):\n"
        "        import types\n"
        "        cls = type(self)\n"
        "        real = vars(cls)['test_call']\n"
        "        if real.__globals__ is globals():\n"
        "            cls.test_call = types.FunctionType(\n"
        "                real.__code__, {**real.__globals__, 'neighbour': lambda: None}, real.__name__)\n"
        "    def test_call(self):\n"
        "        neighbour()\n")}},
}


def _never_called_yet_exercised(tmp_path, spec):
    found = scenario({**spec, "root": str(tmp_path)})
    # Preconditions, all pytest's own: collection succeeded and the test passed without calling neighbour.
    assert found["collect_exit"] == 0, found
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    sibling = found["sibling"]
    assert sibling["exercised"] is not True and sibling["checks"] == [], show(
        {"authority": found["authority"], "real_run": found["tail"], "answer": found["answer"],
         "exercised": sibling["exercised"], "checks": sibling["checks"], "collection": sibling["collection"]})


@pytest.mark.parametrize("kind", sorted(INVENTORY))
def test_unlisted_code_python_loads_keeps_no_identity(tmp_path, kind):
    _never_called_yet_exercised(tmp_path, INVENTORY[kind])


@pytest.mark.parametrize("kind", sorted(NAMES))
def test_decorator_named_by_text_keeps_no_identity(tmp_path, kind):
    _never_called_yet_exercised(tmp_path, NAMES[kind])


@pytest.mark.parametrize("kind", sorted(CALLBACKS))
def test_collection_callback_keeps_no_identity(tmp_path, kind):
    _never_called_yet_exercised(tmp_path, CALLBACKS[kind])


CONTROLS = {
    # the same code where the pre-launch read can see it: refused, as repair 2 intends
    "listed_submodule_with_the_same_code": ({"test_api.py": "from pkg import _version\n" + BODY, "pkg/__init__.py": "",
                                             "pkg/_version.py": RUN_TWIN}, "test_api.py", None, "uncertain"),
    "listed_conftest": ({"tests/test_api.py": BODY, "tests/a_data/conftest.py": RUN_TWIN}, "tests", None, "uncertain"),
    # honest counterparts: the positive is kept
    "honest_package_submodule": ({"test_api.py": "from pkg import _version\n" + HONEST_TEST, "pkg/__init__.py": "",
                                  "pkg/_version.py": "VERSION = '1'\n"}, "test_api.py", HONEST_API, True),
    "honest_helper_module": ({"test_api.py": "import helper\n" + HONEST_TEST, "helper.py": "VALUE = 1\n"},
                             "test_api.py", HONEST_API, True),
    "real_pytest_mark": ({"test_api.py": "import pytest\nfrom api import neighbour\n@pytest.mark.slow\n"
                                         "def test_call():\n    assert neighbour() == 2\n"},
                         "test_api.py", HONEST_API, True),
    "builtin_staticmethod": ({"test_api.py": "from api import neighbour\nclass TestAPI:\n    @staticmethod\n"
                                             "    def test_call():\n        assert neighbour() == 2\n"},
                             "test_api.py", HONEST_API, True),
    "literal_ids": ({"test_api.py": "import pytest\nfrom api import neighbour\n"
                                    "@pytest.mark.parametrize('value', [2], ids=['two'])\n"
                                    "def test_call(value):\n    assert neighbour() == value\n"},
                    "test_api.py", HONEST_API, True),
    "honest_unittest": ({"test_api.py": "import unittest\nfrom api import neighbour\n"
                                        "class TestAPI(unittest.TestCase):\n    def test_call(self):\n"
                                        "        assert neighbour() == 2\n"}, "test_api.py", HONEST_API, True),
}


@pytest.mark.parametrize("kind", sorted(CONTROLS))
def test_control_gate_answers(tmp_path, kind):
    files, selection, api, expected = CONTROLS[kind]
    spec = {"root": str(tmp_path), "files": files, "selection": selection}
    if api is not None:
        spec["api"] = api
    found = scenario(spec)
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    assert found["sibling"]["exercised"] == expected and (found["sibling"]["exercised"] is True) == (expected is True), \
        show({"authority": found["authority"], "sibling": found["sibling"]})
