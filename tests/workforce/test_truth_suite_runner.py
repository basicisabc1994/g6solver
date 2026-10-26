# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The isolated runner's two Stage A fixes (ECHA Stage A, 2026-10-09, founder approval of decision (a)).

Pinned: a ``src/`` layout project imports under the isolated runner (the repository root alone was on the path, so a
reproduction that imported the package never ran); and ``run_suite`` reports pytest's own outcome per test, class-based
and parametrized tests included, skips, expected failures, unexpected passes and set-up errors under their own names, a
fixture named like a test not at all, and says so when it could not run. Each test is a real pytest subprocess on a small
synthetic repository.
"""
from __future__ import annotations

import pytest
from g6.workforce import truth

SUITE = (
    "import pytest\n\n\n"
    "def test_plain():\n    assert True\n\n\n"
    "def test_broken():\n    assert False\n\n\n"
    "class TestGroup:\n    def test_method(self):\n        assert True\n\n"
    "    def test_method_broken(self):\n        assert False\n\n\n"
    "@pytest.mark.parametrize('n', [1, 2])\ndef test_param(n):\n    assert n\n\n\n"
    "@pytest.mark.skip(reason='later')\ndef test_skipped():\n    assert False\n\n\n"
    "@pytest.mark.xfail\ndef test_expected_to_fail():\n    assert False\n\n\n"
    "@pytest.mark.xfail\ndef test_unexpectedly_passes():\n    assert True\n\n\n"
    "@pytest.fixture\ndef missing_fixture_user(no_such_fixture):\n    return 1\n\n\n"
    "def test_setup_error(no_such_fixture):\n    assert True\n\n\n"
    "@pytest.fixture\ndef bad_teardown():\n    yield 1\n    raise RuntimeError('teardown')\n\n\n"
    "def test_teardown_error(bad_teardown):\n    assert True\n\n\n"
    "@pytest.fixture\ndef test_url():\n    return 'http://x'\n"
)


def _repo(tmp_path, files: dict[str, str]):
    repo = tmp_path / "repo"
    for relative, body in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return repo


def test_a_src_layout_project_imports_under_the_isolated_runner(tmp_path):
    repo = _repo(tmp_path, {"src/pkg/__init__.py": "def one():\n    return 1\n",
                            "tests/test_pkg.py": "from pkg import one\n\n\ndef test_one():\n    assert one() == 1\n"})
    assert truth.run_isolated("tests/test_pkg.py::test_one", repo) is True


def test_the_repository_root_still_comes_before_src_and_both_before_installed_packages(tmp_path):
    probe = ("import sys\n\n\ndef test_order():\n"
             "    paths = [p.replace('\\\\', '/') for p in sys.path]\n"
             "    root = paths.index(next(p for p in paths if p.endswith('/repo')))\n"
             "    src = paths.index(next(p for p in paths if p.endswith('/repo/src')))\n"
             "    installed = [i for i, p in enumerate(paths) if 'site-packages' in p or 'dist-packages' in p]\n"
             "    assert root < src and all(src < i for i in installed)\n")
    repo = _repo(tmp_path, {"src/pkg/__init__.py": "", "tests/test_order.py": probe})
    assert truth.run_isolated("tests/test_order.py::test_order", repo) is True


def test_a_flat_layout_project_is_unchanged_and_a_missing_src_adds_nothing(tmp_path):
    repo = _repo(tmp_path, {"mod.py": "def one():\n    return 1\n",
                            "tests/test_mod.py": ("import sys\nfrom mod import one\n\n\ndef test_one():\n"
                                                  "    assert one() == 1\n"
                                                  "    assert not any(p.replace('\\\\', '/').endswith('/repo/src') for p in sys.path)\n")})
    assert truth.run_isolated("tests/test_mod.py::test_one", repo) is True


def test_a_suite_run_names_every_outcome_pytest_reports(tmp_path):
    repo = _repo(tmp_path, {"tests/test_all.py": SUITE})
    found = truth.run_suite(repo)
    assert found["ran"] is True, found
    out = found["outcomes"]
    base = "tests/test_all.py::"
    assert out[base + "test_plain"] == "passed"
    assert out[base + "test_broken"] == "failed"
    assert out[base + "TestGroup::test_method"] == "passed"      # a class-based test is a test
    assert out[base + "TestGroup::test_method_broken"] == "failed"
    assert out[base + "test_param[1]"] == "passed" and out[base + "test_param[2]"] == "passed"
    assert out[base + "test_skipped"] == "skipped"
    assert out[base + "test_expected_to_fail"] == "xfailed"
    assert out[base + "test_unexpectedly_passes"] == "xpassed"
    assert out[base + "test_setup_error"] == "error"
    assert out[base + "test_teardown_error"] == "error"       # it passed, then its fixture failed to tear down
    assert "bad_teardown" not in "".join(out)
    assert base + "test_url" not in out and base + "missing_fixture_user" not in out   # a fixture is not a test


def test_a_collection_error_is_reported_and_the_other_files_still_run(tmp_path):
    repo = _repo(tmp_path, {"tests/test_good.py": "def test_ok():\n    assert True\n",
                            "tests/test_bad.py": "import module_that_does_not_exist\n\n\ndef test_never():\n    pass\n"})
    found = truth.run_suite(repo)
    assert found["ran"] is True
    assert found["outcomes"]["tests/test_good.py::test_ok"] == "passed"
    assert found["collect_errors"] == ["tests/test_bad.py"]


def test_a_suite_that_does_not_finish_says_it_did_not_run(tmp_path):
    repo = _repo(tmp_path, {"tests/test_slow.py": "import time\n\n\ndef test_slow():\n    time.sleep(30)\n"})
    found = truth.run_suite(repo, timeout=2)
    assert found["ran"] is False and "time" in found["why"] and found["outcomes"] == {}
    assert found["started"] is True          # it began and did not finish: an executed failure, not an unmeasured one


def test_a_repository_without_tests_has_nothing_to_compare(tmp_path):
    repo = _repo(tmp_path, {"mod.py": "x = 1\n"})
    found = truth.run_suite(repo)
    assert found["ran"] is False and found["outcomes"] == {}


def test_a_suite_run_leaves_the_repository_as_it_found_it(tmp_path):
    repo = _repo(tmp_path, {"tests/test_all.py": "def test_ok():\n    assert True\n"})
    before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
    truth.run_suite(repo)
    assert sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*")) == before


def test_src_comes_after_the_standard_library_so_it_cannot_shadow_it(tmp_path):
    """Review I-1: a src/ folder (a patch can create one) must not stand in for the standard library."""
    repo = _repo(tmp_path, {"src/fractions.py": "SHADOW = 1\n",
                            "tests/test_std.py": ("import fractions\n\n\ndef test_std():\n"
                                                  "    assert not hasattr(fractions, 'SHADOW')\n"
                                                  "    assert hasattr(fractions, 'Fraction')\n")})
    assert truth.run_isolated("tests/test_std.py::test_std", repo) is True
    assert truth.run_suite(repo)["outcomes"] == {"tests/test_std.py::test_std": "passed"}


def test_a_src_package_still_beats_an_installed_one(tmp_path):
    pytest.importorskip("fastmcp")
    repo = _repo(tmp_path, {"src/fastmcp/__init__.py": "MARK = 1\n",
                            "tests/test_mark.py": "import fastmcp\n\n\ndef test_mark():\n    assert fastmcp.MARK == 1\n"})
    assert truth.run_isolated("tests/test_mark.py::test_mark", repo) is True
    assert truth.run_suite(repo)["outcomes"] == {"tests/test_mark.py::test_mark": "passed"}


def test_node_ids_do_not_carry_the_repository_path(tmp_path):
    """Review I-3: a parametrize over files found beside the test puts the absolute path in the id; two copies of one
    repository must give the same ids, and the ids must not leak where the server keeps its copies."""
    files = {"tests/data/one.txt": "1",
             "tests/test_paths.py": ("import glob\nimport os\n\nimport pytest\n\n"
                                     "HERE = os.path.dirname(os.path.abspath(__file__))\n"
                                     "FILES = sorted(glob.glob(os.path.join(HERE, 'data', '*.txt')))\n\n\n"
                                     "@pytest.mark.parametrize('path', FILES)\ndef test_file(path):\n"
                                     "    assert os.path.exists(path)\n")}
    first = truth.run_suite(_repo(tmp_path / "a", files))
    second = truth.run_suite(_repo(tmp_path / "b", files))
    assert first["ran"] and list(first["outcomes"]) == list(second["outcomes"])
    assert not any(str(tmp_path) in node for node in first["outcomes"]), list(first["outcomes"])
    assert any("<repo>" in node for node in first["outcomes"])


def test_bytes_the_locale_cannot_decode_do_not_crash_the_suite_runner(tmp_path):
    """Review M-1: a process that writes to the runner's stderr after pytest let go of it, in bytes the locale codec
    cannot read, used to raise out of ``run_suite`` instead of being read."""
    repo = _repo(tmp_path, {"tests/test_b.py": ("import atexit\nimport os\n\natexit.register(lambda: os.write(2, bytes("
                                                "[0x81, 0x8d, 0x8f, 0x90, 0x9d, 0xff, 0xfe])))\n\n\n"
                                                "def test_ok():\n    assert True\n")})
    found = truth.run_suite(repo)
    assert found["ran"] is True and found["outcomes"] == {"tests/test_b.py::test_ok": "passed"}


def test_a_module_that_skips_itself_at_import_is_recorded_as_skipped(tmp_path):
    """Review M-3: ``pytest.importorskip`` skips a whole file at collection; that is a skip, not a missing test."""
    repo = _repo(tmp_path, {"tests/test_opt.py": ("import pytest\n\nlib = pytest.importorskip('no_such_module_for_g6')\n\n\n"
                                                  "def test_needs_lib():\n    assert lib\n"),
                            "tests/test_ok.py": "def test_ok():\n    assert True\n"})
    found = truth.run_suite(repo)
    assert found["ran"] is True and found["collect_skipped"] == ["tests/test_opt.py"]
    assert found["collect_errors"] == []
