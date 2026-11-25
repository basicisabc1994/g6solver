"""Findings 5 and 6 (review 3): definite answers the map kept before repair 2 and no longer gives.

Every repository here is honest: real pytest collects ``test_call``, runs it, and it calls ``api.neighbour``
("1 passed"). Before repair 2 (6ef4fdc4) the same data gave ``exercised: true``; the controls run that
commit's own modules on the same fixtures (``before.py``).
"""
import os
import sys
import sysconfig
from pathlib import Path

import pytest

from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, before, scenario, show

BEFORE_REPAIR_2 = "6ef4fdc4"
PLAIN = {"files": {"test_api.py": HONEST_TEST}, "api": HONEST_API}
# Finding 5: what the pre-launch read takes for "the repository can run code in the child".
ORDINARY = {
    # a package file with no pytest table: repo_tests._configures_pytest already tells the two apart
    "pyproject_without_a_pytest_table": {"files": {"test_api.py": HONEST_TEST,
                                                   "pyproject.toml": "[project]\nname = 'demo'\nversion = '0'\n"},
                                         "api": HONEST_API},
    "setup_cfg_without_a_pytest_section": {"files": {"test_api.py": HONEST_TEST, "setup.cfg": "[metadata]\nname = demo\n"},
                                           "api": HONEST_API},
    "a_class_with_a_constructor": {"files": {"test_api.py": HONEST_TEST},
                                   "api": HONEST_API + "class Thing:\n    def __init__(self):\n        self.value = 1\n"},
    "a_module_logger": {"files": {"test_api.py": HONEST_TEST},
                        "api": "import logging\nlogger = logging.getLogger(__name__)\n" + HONEST_API},
    "a_main_guard": {"files": {"test_api.py": HONEST_TEST},
                     "api": HONEST_API + "if __name__ == '__main__':\n    print(neighbour())\n"},
    "a_dataclass": {"files": {"test_api.py": HONEST_TEST},
                    "api": "from dataclasses import dataclass\n@dataclass\nclass Thing:\n    value: int = 1\n" + HONEST_API},
    "a_property": {"files": {"test_api.py": HONEST_TEST},
                   "api": HONEST_API + "class Thing:\n    @property\n    def value(self):\n        return 1\n"},
    "a_fixture_in_the_test_file": {"files": {"test_api.py": (
        "import pytest\nfrom api import neighbour\n@pytest.fixture\ndef two():\n    return 2\n"
        "def test_call(two):\n    assert neighbour() == two\n")}, "api": HONEST_API},
    "an_installed_dependency": {"files": {"test_api.py": HONEST_TEST}, "api": "import pydantic\n" + HONEST_API},
}
PROPOSED = {"files": {"placeholder.py": "VALUE = 1\n"}, "api": HONEST_API, "mode": "proposed",
            "proposed": [{"id": "p1", "requirement_id": "r1", "intent": "preserve", "file": "test_new.py",
                          "source": HONEST_TEST, "test": "test_call"}]}


def _kept(found):
    sibling = found["sibling"]
    assert sibling["exercised"] is True and sibling["checks"], show(
        {"authority": found.get("authority"), "real_run": found.get("tail"), "exercised": sibling["exercised"],
         "checks": sibling["checks"], "collection": sibling["collection"],
         "unresolved": (found.get("answer") or {}).get("unresolved")})


@pytest.mark.parametrize("kind", sorted(ORDINARY))
def test_ordinary_repository_keeps_its_positive(tmp_path, kind):
    found = scenario({**ORDINARY[kind], "root": str(tmp_path)})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found   # honest: the test really calls neighbour
    _kept(found)


@pytest.mark.parametrize("kind", sorted(ORDINARY))
def test_control_ordinary_repository_before_repair_2(tmp_path, kind):
    found = before(BEFORE_REPAIR_2, ORDINARY[kind], tmp_path)
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    _kept(found)


def test_proposed_check_keeps_its_positive(tmp_path):
    # repo_tests.collect runs a proposed check in repository_copy, which holds no git metadata; the
    # pre-launch read asks git for the copy's inventory, so its authority is never available.
    found = scenario({**PROPOSED, "root": str(tmp_path)})
    assert found["exit"] == 0, found
    _kept(found)


def test_control_proposed_check_before_repair_2(tmp_path):
    found = before(BEFORE_REPAIR_2, PROPOSED, tmp_path)
    assert found["exit"] == 0, found
    _kept(found)


# Finding 6: the comparison is against G6's own interpreter and G6's own pytest.
def test_g6_environment_without_pytest_keeps_its_positive(tmp_path):
    # g6 depends on pydantic and returns only; README: the tests run with the repository's own .venv, and
    # only "without one, with the Python running G6, which then needs pytest".
    found = scenario({**PLAIN, "root": str(tmp_path), "g6_without_pytest": True})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    _kept(found)


def _another_python():
    """A CPython of another minor version on this machine (uv's own installs), or None."""
    home = Path(os.environ.get("APPDATA", "")) / "uv" / "python"
    if not home.is_dir():
        return None
    mine = f"cpython-{sys.version_info.major}.{sys.version_info.minor}."
    for folder in sorted(home.glob("cpython-3.*")):
        exe = folder / "python.exe"
        if not folder.name.startswith(mine) and exe.is_file():
            return str(exe)
    return None


def test_repository_interpreter_of_another_version_keeps_its_positive(tmp_path):
    other = _another_python()
    if other is None:
        pytest.skip("no CPython of another minor version on this machine")
    # The repository's interpreter (G6_REPO_PYTHON's place). It has no pytest of its own here, so one line in
    # front of the shipped bootstrap lends it this environment's pure-Python pytest; nothing else differs.
    prefix = "import sys; sys.path.append(%r)\n" % sysconfig.get_paths()["purelib"]
    found = scenario({**PLAIN, "root": str(tmp_path), "python": other, "prefix": prefix})
    assert found["collect_exit"] == 0 and found["exit"] == 0 and "1 passed" in found["tail"][0], found
    _kept(found)


def test_control_same_interpreter_keeps_its_positive(tmp_path):
    found = scenario({**PLAIN, "root": str(tmp_path)})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    _kept(found)
