# SPDX-License-Identifier: Apache-2.0
"""detect_test_weakening (audit 007, SE-005): a skip marker, a conftest.py in any directory, a new file and a pytest or
CI configuration change weaken the tests, as do a deleted file, a removed check and fewer tests; a clean report names
what it compared and what it could not see, and nothing is ever ``verified``. Offline; nothing is run.

The null tests put the archive's heuristic back in memory (it read only files present before, wanted ``"/tests/"`` in
the path and counted ``assert`` lines) and show that each regression test then fails.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

import g6.safety_engine
from g6.invariants import check_envelope
from g6.safety_engine import weakening

T = "tests/test_a.py"
BASE = ("import pytest\n\nfrom calc import add\n\n\n"
        "def test_add():\n    assert add(2, 2) == 4\n    assert add(0, 0) == 0\n\n\n"
        "def test_zero():\n    assert add(0, 1) == 1\n")
UNIT = ("import unittest\n\nfrom calc import add\n\n\n"
        "class AddCase(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n"
        "        self.assertEqual(add(0, 0), 0)\n")


def _detect(before, after) -> weakening.TestWeakeningReport:
    return weakening.detect_test_weakening(before, after)  # through the module, so a null test can swap it


def _text(report: weakening.TestWeakeningReport) -> str:
    return "\n".join(report.reasons)


def _fails(test, *args) -> None:
    with pytest.raises(AssertionError):
        test(*args)


def _archive_detect(before, after) -> weakening.TestWeakeningReport:
    """The archive's heuristic (verification.py), restated for the null tests."""
    reasons = []
    count = lambda s, p: len(re.findall(p, s))  # noqa: E731
    for path, text in before.items():
        low = path.replace("\\", "/").lower()
        name = low.rsplit("/", 1)[-1]
        if not (name.startswith("test_") or name.endswith("_test.py") or "/tests/" in low):
            continue
        now = after.get(path)
        if now is None:
            reasons.append(f"test file deleted: {path}")
            continue
        asserts = r"(?m)^\s*assert\b|pytest\.raises|self\.assert|unittest"
        if count(now, asserts) < count(text, asserts):
            reasons.append("assertions removed")
        tests = r"(?m)^\s*(?:async\s+)?def\s+test\w*\s*\("
        if count(now, tests) < count(text, tests):
            reasons.append("test functions removed")
        if count(now, r"(?m)^\s*assert\s+(True|1)\s*(#.*)?$") > count(text, r"(?m)^\s*assert\s+(True|1)\s*(#.*)?$"):
            reasons.append("assertions neutered")
    return weakening.TestWeakeningReport(bool(reasons), tuple(reasons))


# --- the audit's case --------------------------------------------------------------------------------------------------

def test_se005_the_audit_case_is_weakened() -> None:
    """before = {tests/test_a.py}; after adds @pytest.mark.skip and a collection-clearing tests/conftest.py."""
    after = {T: BASE.replace("def test_add", "@pytest.mark.skip\ndef test_add"),
             "tests/conftest.py": "def pytest_collection_modifyitems(config, items):\n    items[:] = []\n"}
    report = _detect({T: BASE}, after)
    assert report.weakened is True and report.completion_state == "blocked-escalated"
    text = _text(report)
    assert "tests/conftest.py: conftest.py added: pytest runs it for every test under tests/" in text
    assert "adds or changes pytest_collection_modifyitems, which can deselect or drop collected tests" in text
    assert f"{T}: adds skip" in text
    assert report.checked == ("tests/conftest.py", T)


# --- skip markers ------------------------------------------------------------------------------------------------------

_SKIPS = [
    ("skip", BASE.replace("def test_add", "@pytest.mark.skip(reason='later')\ndef test_add"), "skip"),
    ("skipif", BASE.replace("def test_add", "@pytest.mark.skipif(True, reason='x')\ndef test_add"), "skipif"),
    ("xfail", BASE.replace("def test_add", "@pytest.mark.xfail\ndef test_add"), "xfail"),
    ("skip-call", BASE.replace("    assert add(2, 2) == 4\n", "    pytest.skip('flaky')\n    assert add(2, 2) == 4\n"),
     "skip"),
    ("importorskip", BASE.replace("from calc import add\n", "from calc import add\n\npytest.importorskip('nope')\n"),
     "importorskip"),
    ("pytestmark", BASE.replace("from calc import add\n", "from calc import add\n\npytestmark = pytest.mark.skip\n"),
     "pytestmark"),
    ("param-marks", BASE.replace("def test_zero():\n", "@pytest.mark.parametrize('x', [pytest.param(1, marks="
                                 "pytest.mark.skip)])\ndef test_zero(x):\n"), "skip"),
]


@pytest.mark.parametrize(("after", "name"), [(a, n) for _, a, n in _SKIPS], ids=[i for i, _, _ in _SKIPS])
def test_se005_an_added_skip_marker_is_weakening(after: str, name: str) -> None:
    report = _detect({T: BASE}, {T: after})
    assert report.weakened is True, report
    assert f"{T}: adds {name} (a skip or expected failure" in _text(report)


@pytest.mark.parametrize(("after", "name"), [
    (UNIT.replace("    def test_add", "    @unittest.skip('later')\n    def test_add"), "skip"),
    (UNIT.replace("        self.assertEqual(add(2, 2), 4)\n",
                  "        self.skipTest('later')\n        self.assertEqual(add(2, 2), 4)\n"), "skipTest"),
    (UNIT.replace("    def test_add", "    @unittest.expectedFailure\n    def test_add"), "expectedFailure"),
], ids=["unittest-skip", "skipTest", "expectedFailure"])
def test_se005_unittest_skips_count_too(after: str, name: str) -> None:
    report = _detect({"tests/test_u.py": UNIT}, {"tests/test_u.py": after})
    assert report.weakened is True and f"adds {name}" in _text(report)


# --- conftest.py, in any directory ----------------------------------------------------------------------------------

_CONFTESTS = [
    ("collect_ignore", "tests/conftest.py", "collect_ignore = ['test_a.py']\n", "collect_ignore, which drops paths"),
    ("collect_ignore_glob", "tests/conftest.py", "collect_ignore_glob = ['test_*.py']\n", "collect_ignore_glob"),
    ("deselect", "tests/conftest.py",
     "def pytest_collection_modifyitems(config, items):\n    config.hook.pytest_deselected(items=list(items))\n"
     "    items[:] = []\n", "pytest_collection_modifyitems"),
    ("ignore_collect", "tests/conftest.py", "def pytest_ignore_collect(collection_path, config):\n    return True\n",
     "pytest_ignore_collect, which can stop files from being collected"),
    ("root-makereport", "conftest.py",
     "import pytest\n\n\n@pytest.hookimpl(wrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
     "    report = yield\n    report.outcome = 'passed'\n    return report\n",
     "pytest_runtest_makereport, which can rewrite a test's outcome"),
    ("nested-autouse", "src/pkg/tests/unit/conftest.py",
     "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _fake(monkeypatch):\n"
     "    monkeypatch.setattr('calc.add', lambda a, b: 4)\n", "_fake, which is an autouse fixture"),
    ("non-test-dir", "pkg/conftest.py", "def pytest_ignore_collect(collection_path, config):\n    return True\n",
     "pytest_ignore_collect"),
]


@pytest.mark.parametrize(("path", "text", "fragment"), [c[1:] for c in _CONFTESTS], ids=[c[0] for c in _CONFTESTS])
def test_se005_a_new_conftest_anywhere_is_weakening(path: str, text: str, fragment: str) -> None:
    report = _detect({T: BASE}, {T: BASE, path: text})
    assert report.weakened is True
    assert f"{path}: conftest.py added" in _text(report) and fragment in _text(report), report.reasons


def test_se005_a_changed_or_removed_conftest_is_weakening() -> None:
    old = "import pytest\n\n\n@pytest.fixture\ndef data():\n    return [1, 2, 3]\n"
    changed = _detect({"tests/conftest.py": old}, {"tests/conftest.py": old.replace("[1, 2, 3]", "[]")})
    assert changed.weakened and "tests/conftest.py: conftest.py changed" in _text(changed)
    removed = _detect({"tests/conftest.py": old}, {})
    assert removed.weakened and "tests/conftest.py: conftest.py removed" in _text(removed)


def test_a_comment_only_conftest_change_is_not_weakening() -> None:
    old = "import pytest\n\n\n@pytest.fixture\ndef data():\n    return [1, 2, 3]\n"
    assert not _detect({"tests/conftest.py": old}, {"tests/conftest.py": "# shared fixtures\n" + old}).weakened


# --- pytest, runner and CI configuration ------------------------------------------------------------------------------

_INI_OPTS = '[tool.pytest.ini_options]\naddopts = "-q"\n'
_CONFIGS = [
    ("pytest.ini-added", "pytest.ini", None, "[pytest]\naddopts = -k 'not add'\n", "pytest configuration added"),
    ("pytest.ini-empty", "pytest.ini", None, "", "pytest configuration added"),
    ("pytest.ini-changed", "pytest.ini", "[pytest]\naddopts = -q\n",
     "[pytest]\naddopts = -q --deselect tests/test_a.py::test_add\n", "pytest configuration changed"),
    ("pytest.ini-removed", "pytest.ini", "[pytest]\nxfail_strict = true\n", None, "pytest configuration removed"),
    ("nested-pytest.ini", "pkg/pytest.ini", None, "[pytest]\n", "pytest configuration added"),
    ("pyproject-added", "pyproject.toml", '[project]\nname = "x"\n', '[project]\nname = "x"\n\n' + _INI_OPTS,
     "pytest configuration added"),
    ("pyproject-changed", "pyproject.toml", _INI_OPTS, _INI_OPTS.replace('"-q"', '"-q -p no:cacheprovider --co"'),
     "pytest configuration changed"),
    ("setup.cfg", "setup.cfg", "[metadata]\nname = x\n", "[metadata]\nname = x\n\n[tool:pytest]\nnorecursedirs = tests\n",
     "pytest configuration added"),
    ("tox.ini", "tox.ini", "[testenv]\ncommands = pytest\n", "[testenv]\ncommands = pytest -k 'not add'\n",
     "test-runner or CI configuration changed"),
    ("workflow", ".github/workflows/ci.yml", "run: pytest\n", "run: pytest || true\n",
     "test-runner or CI configuration changed"),
    ("noxfile", "noxfile.py", None, "import nox\n", "test-runner or CI configuration added"),
    ("sitecustomize", "sitecustomize.py", None, "import calc\ncalc.add = lambda a, b: 4\n", "sitecustomize.py added"),
]


@pytest.mark.parametrize(("path", "old", "new", "fragment"), [c[1:] for c in _CONFIGS], ids=[c[0] for c in _CONFIGS])
def test_se005_a_test_configuration_change_is_weakening(path: str, old: str | None, new: str | None,
                                                        fragment: str) -> None:
    before = {T: BASE, **({path: old} if old is not None else {})}
    after = {T: BASE, **({path: new} if new is not None else {})}
    report = _detect(before, after)
    assert report.weakened is True and f"{path}: {fragment}" in _text(report), report.reasons


def test_unreadable_configuration_after_the_change_is_weakening() -> None:
    report = _detect({"pyproject.toml": _INI_OPTS}, {"pyproject.toml": _INI_OPTS + "[broken\n"})
    assert report.weakened and "cannot be read after the change" in _text(report)


# --- deleted files, removed checks, fewer tests -----------------------------------------------------------------------

_WEAKER = [
    ("assert-removed", BASE.replace("    assert add(0, 0) == 0\n", ""), "test_add: a check was removed"),
    ("assert-true", BASE.replace("    assert add(2, 2) == 4\n    assert add(0, 0) == 0\n", "    assert True\n"),
     "test_add: a check was removed, changed or can no longer fail: assert add(2, 2) == 4 (and 1 more)"),
    ("assert-loosened", BASE.replace("add(0, 1) == 1", "add(0, 1) >= 0"), "test_zero: a check was removed"),
    ("test-removed", BASE.split("\n\n\ndef test_zero")[0] + "\n", "test removed or renamed: test_zero"),
    ("catch-all", BASE.replace("    assert add(0, 1) == 1\n",
                               "    try:\n        assert add(0, 1) == 1\n    except Exception:\n        pass\n"),
     "test_zero: a check was removed, changed or can no longer fail"),
    ("suppress", BASE.replace("import pytest\n", "import contextlib\n\nimport pytest\n").replace(
        "    assert add(0, 1) == 1\n", "    with contextlib.suppress(AssertionError):\n        assert add(0, 1) == 1\n"),
     "test_zero: a check was removed"),
    ("early-return", BASE.replace("    assert add(0, 1) == 1\n", "    return\n    assert add(0, 1) == 1\n"),
     "test_zero: early return added"),
    ("mock-in-old-test", BASE.replace("def test_zero():\n", "def test_zero(monkeypatch):\n    monkeypatch.setattr("
                                      "'calc.add', lambda a, b: 1)\n"), "adds monkeypatch (a mock or patch"),
    ("autouse-fixture", BASE + "\n\n@pytest.fixture(autouse=True)\ndef _quiet():\n    yield\n", "adds autouse"),
    ("module-patch", BASE.replace("from calc import add\n", "from calc import add\nimport calc\n\ncalc.add = max\n"),
     "module-level patch added (calc.add = max)"),
]


@pytest.mark.parametrize(("after", "fragment"), [c[1:] for c in _WEAKER], ids=[c[0] for c in _WEAKER])
def test_se005_a_weaker_test_file_is_weakening(after: str, fragment: str) -> None:
    report = _detect({T: BASE}, {T: after})
    assert report.weakened is True and fragment in _text(report), report.reasons


def test_se005_a_deleted_test_file_and_fewer_tests_count() -> None:
    deleted = _detect({T: BASE}, {})
    assert deleted.weakened and deleted.reasons == (f"{T}: test file deleted",)
    fewer = _detect({T: BASE}, {T: BASE.split("\n\n\ndef test_zero")[0] + "\n"})
    assert f"{T}: fewer tests: 2 -> 1" in fewer.reasons


@pytest.mark.parametrize(("before", "after", "fragment"), [
    (UNIT, UNIT.replace("        self.assertEqual(add(0, 0), 0)\n", ""), "self.assertEqual(add(0, 0), 0)"),
    (BASE.replace("    assert add(0, 1) == 1\n", "    with pytest.raises(TypeError):\n        add(None, 1)\n"
                  "    assert add(0, 1) == 1\n"), BASE, "pytest.raises(TypeError)"),
    (BASE.replace("def test_zero():\n    assert add(0, 1) == 1\n",
                  "@pytest.mark.parametrize('x', [1, 2, 3])\ndef test_zero(x):\n    assert add(0, x) == x\n"),
     BASE.replace("def test_zero():\n    assert add(0, 1) == 1\n",
                  "@pytest.mark.parametrize('x', [1])\ndef test_zero(x):\n    assert add(0, x) == x\n"),
     "parametrize 'x': 2 (and 1 more)"),
    (BASE + "\n\ndef check(v):\n    assert v == 4\n", BASE + "\n\ndef check(v):\n    assert v is not None\n",
     "check: a check was removed"),
], ids=["unittest-assert-method", "pytest-raises", "parametrize-case", "helper-assert"])
def test_se005_checks_other_than_assert_lines_count(before: str, after: str, fragment: str) -> None:
    report = _detect({T: before}, {T: after})
    assert report.weakened is True and fragment in _text(report), report.reasons


def test_se005_the_word_unittest_is_not_an_assertion() -> None:
    """The archive counted every "unittest" in the text as an assertion, so a removed assert hid behind a comment."""
    before = "import unittest\n\n\ndef test_a():\n    assert 1 + 1 == 2\n"
    after = "import unittest  # unittest kept for unittest-style helpers\n\n\ndef test_a():\n    pass\n"
    assert _detect({T: before}, {T: after}).weakened is True


# --- new files ------------------------------------------------------------------------------------------------------

def test_se005_a_new_file_is_read() -> None:
    patching = _detect({T: BASE}, {T: BASE, "tests/test_b.py": "import calc\n\ncalc.add = lambda a, b: 4\n\n\n"
                                                                "def test_b():\n    assert True\n"})
    assert patching.weakened and "tests/test_b.py: module-level patch added (calc.add = lambda a, b: 4)" in _text(
        patching)
    shadow = _detect({T: BASE}, {T: BASE, "tests/calc.py": "def add(a, b):\n    return 4\n"})
    assert shadow.weakened and "tests/calc.py: new module named like 'calc'" in _text(shadow)


# --- survival: an honest change is not weakening --------------------------------------------------------------------

HONEST = ("import pytest\n\nfrom calc import add\n\n\n"
          "def _pair():\n    return 2, 2\n\n\n"
          "def test_zero():\n    \"\"\"Adding zero changes nothing.\"\"\"\n    assert add(0, 1) == 1\n\n\n"
          "def test_add():\n    a, b = _pair()\n    assert add(2, 2) == 4, 'two and two'\n    assert add(0, 0) == 0\n"
          "    assert add(a, b) == 4\n\n\n"
          "@pytest.mark.parametrize('x', [1, 2, 3])\ndef test_more(x, monkeypatch):\n    monkeypatch.setenv('A', '1')\n"
          "    pytest.importorskip('calc')\n    assert add(x, 0) == x\n")
PYPROJECT = '[project]\nname = "x"\ndependencies = ["a>=1"]\n\n' + _INI_OPTS


def test_survival_an_honest_refactor_is_not_weakening() -> None:
    """Tests reordered, a docstring, an assert message, a new assert, a new test with its own mock and skip, a helper
    with no checks, a new test file, a comment in conftest.py, a dependency bump and a source change."""
    conftest = "import pytest\n\n\n@pytest.fixture\ndef data():\n    return [1]\n"
    before = {T: BASE, "tests/conftest.py": conftest, "pyproject.toml": PYPROJECT, "src/calc.py": "add = None\n"}
    after = {T: HONEST, "tests/conftest.py": "# fixtures\n" + conftest,
             "pyproject.toml": PYPROJECT.replace("a>=1", "a>=2"), "src/calc.py": "def add(a, b):\n    return a + b\n",
             "tests/test_c.py": "from unittest import mock\n\nimport pytest\n\npytestmark = pytest.mark.slow\n\n\n"
                                "@mock.patch('calc.add')\ndef test_c(fake):\n    assert fake is not None\n"}
    report = _detect(before, after)
    assert report.weakened is False, report.reasons
    assert report.completion_state == "qualified-draft"
    assert report.checked == ("pyproject.toml", "tests/conftest.py", T, "tests/test_c.py")
    assert "1 other file(s) were not examined: not tests, conftest.py or test configuration" in report.not_checked


def test_null_survival(monkeypatch) -> None:
    monkeypatch.setattr(weakening, "detect_test_weakening", lambda b, a: weakening.TestWeakeningReport(True, ("x",)))
    _fails(test_survival_an_honest_refactor_is_not_weakening)


# --- what the report says it did and did not check ------------------------------------------------------------------

def test_a_clean_report_lists_what_it_compared_and_what_it_could_not() -> None:
    report = _detect({T: BASE, "tests/data/expected.json": "[1]"}, {T: BASE, "tests/data/expected.json": "[]"})
    assert report.weakened is False and report.checked == (T,)
    assert any("tests/data/expected.json" in line and "not compared" in line for line in report.not_checked)
    assert set(weakening.LIMITS) <= set(report.not_checked)
    assert any("Nothing was run" in line for line in report.not_checked)


def test_a_file_that_did_not_parse_before_is_listed_as_not_compared() -> None:
    report = _detect({T: "def test_a(:\n"}, {T: BASE})
    assert report.weakened is False and any("did not parse before the change" in n for n in report.not_checked)
    broken = _detect({T: BASE}, {T: "def test_a(:\n"})
    assert broken.weakened and "does not parse after the change" in _text(broken)


@pytest.mark.parametrize(("before", "after", "state", "code"), [
    ({T: BASE}, {}, "blocked-escalated", weakening.DETECTED),
    ({T: BASE}, {T: HONEST}, "qualified-draft", weakening.STATIC_ONLY),
    ({"src/calc.py": "x = 1\n"}, {"src/calc.py": "x = 2\n"}, "qualified-draft", weakening.NOTHING_COMPARED),
    ({}, {}, "qualified-draft", weakening.NOTHING_COMPARED),
], ids=["detected", "clean", "nothing-compared", "empty"])
def test_the_envelope_is_never_verified_and_keeps_the_contract(before, after, state, code) -> None:
    report = _detect(before, after)
    envelope = report.to_envelope(request_id="req-1", run_id="run-1")
    assert check_envelope(envelope) == []
    assert envelope["completion_state"] == state == report.completion_state != "verified"
    assert envelope["warning_card"]["code"] == code and envelope["warning_card"]["verified"] is False
    assert envelope["request_id"] == "req-1" and envelope["run_id"] == "run-1"
    assert envelope["evidence"][0]["checked"] == list(report.checked)
    assert envelope["evidence"][0]["basis"].endswith("nothing was run")


# --- two directory snapshots ----------------------------------------------------------------------------------------

def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_se005_two_directory_snapshots_compare_like_mappings(tmp_path: Path) -> None:
    before = {T: BASE, "src/calc.py": "x = 1\n", ".venv/lib/tests/test_x.py": "def test_x():\n    assert 1\n"}
    after = {T: BASE.replace("def test_add", "@pytest.mark.skip\ndef test_add"), "src/calc.py": "x = 2\n",
             "tests/unit/conftest.py": "collect_ignore = ['test_a.py']\n"}
    old, new = _write(tmp_path / "old", before), _write(tmp_path / "new", after)
    from_dirs = _detect(old, str(new))
    assert from_dirs.weakened is True
    assert from_dirs.reasons == _detect({k: v for k, v in before.items() if not k.startswith(".venv")},
                                        {k: v for k, v in after.items()}).reasons
    assert weakening.read_snapshot(new).keys() == {T, "tests/unit/conftest.py"}  # .venv and src/ are not read
    assert from_dirs.checked == (T, "tests/unit/conftest.py")


def test_bad_input_is_refused_with_a_stable_code(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=weakening.INPUT_INVALID):
        _detect(42, {})
    with pytest.raises(TypeError, match=weakening.INPUT_INVALID):
        _detect({T: 1}, {})
    with pytest.raises(NotADirectoryError, match=weakening.INPUT_INVALID):
        _detect(tmp_path / "missing", {})
    assert _detect({T: BASE.encode()}, {T: BASE}).weakened is False  # bytes are read as UTF-8


# --- against the workforce detector's labelled corpus ----------------------------------------------------------------

def _corpus():
    path = Path(__file__).resolve().parents[1] / "workforce" / "weakened_tests_corpus.py"
    spec = importlib.util.spec_from_file_location("se005_weakened_tests_corpus", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass resolves its own module while it is built
    spec.loader.exec_module(module)  # data and a few helpers; it imports nothing of the product
    return module


def test_the_labelled_corpus_finds_every_weakening_the_workforce_check_finds_and_more() -> None:
    """``g6.workforce.verify.weakened_tests``'s corpus (Gate W D-W7): every case it catches is caught here, every
    benign case it stays quiet on stays quiet here, and four of its blind spots are closed. The fixture made trivial
    stays a blind spot (LIMITS)."""
    corpus = _corpus()
    for case in corpus.CASES:
        before, after = corpus.as_sources(case)
        alarmed = _detect(before, after).weakened
        if case.expect_alarm:
            assert alarmed, case.id
        elif case.label == "benign":
            assert not alarmed, case.id
    closed = {"b-unittest-assertEqual-removed", "b-pytest-raises-removed", "b-parametrize-cases-dropped",
              "b-assert-loosened-in-helper"}
    for case in corpus.CASES:
        if case.id in closed | {"b-fixture-made-trivial"}:
            assert _detect(*corpus.as_sources(case)).weakened is (case.id in closed), case.id


# --- null tests: the archive's heuristic fails every regression test above ------------------------------------------

def test_null_se005(monkeypatch) -> None:
    monkeypatch.setattr(weakening, "detect_test_weakening", _archive_detect)
    assert not _archive_detect({T: BASE}, {T: BASE.replace("def test_add", "@pytest.mark.skip\ndef test_add")}).weakened
    _fails(test_se005_the_audit_case_is_weakened)
    _fails(test_se005_an_added_skip_marker_is_weakening, _SKIPS[0][1], "skip")
    _fails(test_se005_a_new_conftest_anywhere_is_weakening, *_CONFTESTS[0][1:])
    _fails(test_se005_a_changed_or_removed_conftest_is_weakening)
    _fails(test_se005_a_test_configuration_change_is_weakening, *_CONFIGS[2][1:])
    _fails(test_se005_a_new_file_is_read)
    _fails(test_se005_the_word_unittest_is_not_an_assertion)
    shrunk = BASE.replace("def test_zero():\n    assert add(0, 1) == 1\n",
                          "@pytest.mark.parametrize('x', [1, 2])\ndef test_zero(x):\n    assert add(0, x) == x\n")
    _fails(test_se005_checks_other_than_assert_lines_count, shrunk, shrunk.replace("[1, 2]", "[1]"), "parametrize")
    helper = BASE + "\n\ndef check(v):\n    assert v == 4\n"
    _fails(test_se005_checks_other_than_assert_lines_count, helper, helper.replace("v == 4", "v is not None"), "check")


def test_null_se005_path_rule(monkeypatch) -> None:
    """Only the archive's path rule put back: a repository-relative tests/conftest.py is no longer a test path."""
    monkeypatch.setattr(weakening, "_kind", lambda p: "test" if "/tests/" in p.lower() or p.lower().rsplit(
        "/", 1)[-1].startswith("test_") else "other")
    _fails(test_se005_a_new_conftest_anywhere_is_weakening, *_CONFTESTS[0][1:])


# --- the package ----------------------------------------------------------------------------------------------------

def test_the_new_module_carries_the_library_header_and_is_exported() -> None:
    text = Path(weakening.__file__).read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:5])
    assert text.startswith("# SPDX-License-Identifier: Apache-2.0\n")
    assert not any(word in head for word in ("Copyright", "All rights reserved", "Proprietary"))
    assert g6.safety_engine.detect_test_weakening is weakening.detect_test_weakening
    assert "import g6.workforce" not in text and "from g6.workforce" not in text  # workforce depends on this package
    assert "verified" not in {weakening.TestWeakeningReport(w).completion_state for w in (True, False)}
