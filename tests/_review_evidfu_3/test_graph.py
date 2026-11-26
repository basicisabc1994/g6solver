"""Bounded graph termination and controls for the named review suspicions."""
import os
import subprocess
import sys

import pytest

from g6.harness.workflow import evidence
from tests._review_evidfu_3.test_boundaries import tmp_path, isolated
from tests._review_evid_3.test_review3_boundaries import observe
from tests._review_evid_3.test_review3_boundaries import (
    test_label_targets_and_certificate_custody_control as labels_and_custody,
)
from tests._review_evidfu_2.test_controls import (
    test_uncertain_machinery_withholds_control as uncertain,
    test_static_source_changes_identity_control as tracks,
)


@pytest.mark.parametrize("kind", [
    "cycle", "deep", "star", "hook", "plugin_hook", "above_root", "malformed", "encoding",
])
def test_unresolved_controls(tmp_path, kind):
    uncertain(tmp_path, kind)


@pytest.mark.parametrize("kind", ["relative_reexport", "namespace", "module_parameter", "indirect_fixture"])
def test_resolved_controls(tmp_path, kind):
    tracks(tmp_path, kind)


@pytest.mark.parametrize("depth", [2, 18], ids=["small_control", "repeated_diamond"])
def test_collection_graph_finishes(tmp_path, depth):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "class A0: pass\n"
    for i in range(1, depth + 1):
        source += f"class L{i}(A{i-1}): pass\nclass R{i}(A{i-1}): pass\nclass A{i}(L{i}, R{i}): pass\n"
    source += f"class TestCase(A{depth}):\n    def test_case(self, request): pass\n"
    (repo / "test_case.py").write_text(source, encoding="utf-8")
    assert observe(repo, "test_case.py", tmp_path / "execution")["passed"] == 1
    child = subprocess.run([
        sys.executable, "-c",
        "from g6.harness.workflow.evidence import capture_sources; import sys; "
        "print(capture_sources(sys.argv[1], ['test_case.py']))",
        str(repo),
    ], env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True, text=True, timeout=8)
    assert child.returncode == 0, child.stderr
    assert "test_case.py" in child.stdout


@pytest.mark.parametrize("kind", ["fixture_params", "class_decorator", "init_subclass", "property", "module_getattr"])
def test_additional_collection_controls(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    oracle = repo / "oracle.py"
    oracle.write_text("VALUE = 1\ndef touch(): pass\n", encoding="utf-8")
    if kind == "fixture_params":
        source = ("import pytest\nfrom oracle import VALUE\n"
                  "@pytest.fixture(params=[VALUE])\ndef item(request): return request.param\n"
                  "def test_case(item): pass\n")
    elif kind == "class_decorator":
        source = ("from helper import decorate\n@decorate\nclass TestCase:\n"
                  "    def test_case(self): pass\n")
        (repo / "helper.py").write_text(
            "from oracle import touch\ndef decorate(cls):\n    touch()\n    return cls\n", encoding="utf-8")
    elif kind == "init_subclass":
        source = "from helper import Base\nclass TestCase(Base):\n    def test_case(self): pass\n"
        (repo / "helper.py").write_text(
            "from oracle import touch\nclass Base:\n    def __init_subclass__(cls): touch()\n", encoding="utf-8")
    elif kind == "property":
        source = ("from helper import Meta\nclass TestCase(metaclass=Meta):\n"
                  "    def test_case(self): pass\n")
        (repo / "helper.py").write_text(
            "from oracle import touch\nclass Meta(type):\n"
            "    @property\n    def probe(cls):\n        touch()\n        return 1\n", encoding="utf-8")
    else:
        source = "from helper import test_case\n"
        (repo / "helper.py").write_text(
            "from oracle import touch\ndef __getattr__(name):\n    touch()\n    return lambda: None\n", encoding="utf-8")
    (repo / "test_case.py").write_text(source, encoding="utf-8")
    before = evidence.capture_sources(repo, ["test_case.py"])
    oracle.write_text("VALUE = 2\ndef touch(): return 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, ["test_case.py"])
    assert before != after or before == after == {"test_case.py": ""}


def test_authority_and_custody_control(tmp_path, monkeypatch):
    labels_and_custody(tmp_path, monkeypatch)
