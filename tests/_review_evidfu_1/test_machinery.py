"""Real disk observations for follow-up review 1; execution transport only is replaced."""
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

from g6.harness.workflow import adapters, evidence, lifecycle, record_md
from g6.harness.workflow.record import ProjectRecord
from g6.workforce import coding_certificate
from tests._review_evid_3.test_review3_boundaries import (
    commit_fixture, finish, isolated, observe, public_review,
)
from tests.workflow.kit import make_repo


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "disk" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


CASES = {
    "dynamic_fixture": (
        "def test_case(request):\n"
        "    assert request.getfixturevalue('observed') == 2\n",
        "from oracle import observed\n",
        "import pytest\nfrom module import VALUE\n"
        "@pytest.fixture\ndef observed():\n    return VALUE\n",
        "import pytest\n@pytest.fixture\ndef observed():\n    return 2\n",
    ),
    "class_autouse": (
        "class TestCase:\n"
        "    from oracle import automatic\n"
        "    def test_case(self):\n        assert self.observed == 2\n",
        "",
        "import pytest\nfrom module import VALUE\n"
        "@pytest.fixture(autouse=True)\ndef automatic(self):\n    self.observed = VALUE\n",
        "import pytest\n@pytest.fixture(autouse=True)\n"
        "def automatic(self):\n    self.observed = 2\n",
    ),
    "class_usefixtures": (
        "import pytest\nclass TestCase:\n"
        "    from oracle import observed\n"
        "    pytestmark = pytest.mark.usefixtures('observed')\n"
        "    def test_case(self):\n        assert self.observed == 2\n",
        "",
        "import pytest\nfrom module import VALUE\n"
        "@pytest.fixture\ndef observed(self):\n    self.observed = VALUE\n",
        "import pytest\n@pytest.fixture\ndef observed(self):\n    self.observed = 2\n",
    ),
    "collector_fixture_override": (
        "from oracle import observed\nfrom checks_impl import test_case\n",
        "",
        "import pytest\nfrom module import VALUE\n"
        "@pytest.fixture\ndef observed():\n    return VALUE\n",
        "import pytest\n@pytest.fixture\ndef observed():\n    return 2\n",
    ),
    "parametrization_defaults": (
        "import pytest\nfrom factory import cases\nfrom module import VALUE\n"
        "@pytest.mark.parametrize('expected', cases(), ids=['case'])\n"
        "def test_case(expected):\n    assert VALUE == expected\n",
        "",
        "EXPECTED = [2]\n",
        "EXPECTED = [3]\n",
    ),
}


@pytest.mark.parametrize("kind,replace_machinery", [
    pytest.param(kind, replace, id=f"{kind}-{'oracle_replaced' if replace else 'product_control'}")
    for kind in CASES for replace in (False, True)
    if kind != "dynamic_fixture" or replace
])
def test_unbound_fixture_cannot_close_finding(tmp_path, monkeypatch, kind, replace_machinery):
    repo = make_repo(tmp_path / "repo")
    collector, conftest, original, replacement = CASES[kind]
    selection = "test_case.py"
    (repo / selection).write_text(collector, encoding="utf-8")
    (repo / "conftest.py").write_text(conftest, encoding="utf-8")
    if kind == "collector_fixture_override":
        (repo / "checks_impl.py").write_text(
            "import pytest\n@pytest.fixture\ndef observed():\n    return 99\n"
            "def test_case(observed):\n    assert observed == 2\n", encoding="utf-8")
    if kind == "parametrization_defaults":
        (repo / "factory.py").write_text(
            "from oracle import EXPECTED\ndef cases(values=EXPECTED):\n    return values\n", encoding="utf-8")
    oracle = repo / "oracle.py"
    oracle.write_text(original, encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    before = evidence.capture_sources(repo, [selection])
    failed = observe(repo, selection, tmp_path / "before")
    assert failed["failed"] == 1
    first = public_review(repo, selection, failed, monkeypatch)
    assert first["outcome"] == "defect_detected"
    finish(repo, first, "0", monkeypatch)
    if replace_machinery:
        oracle.write_text(replacement, encoding="utf-8")
    else:
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [selection])
    passed = observe(repo, selection, tmp_path / "after")
    assert passed["passed"] == 1
    second = public_review(repo, selection, passed, monkeypatch)
    assert second["outcome"] != "test_integrity_compromised", second["reasons"]
    reply = finish(repo, second, "1", monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    counts = record_md.counts(rows)
    readable = lifecycle.export(project).with_name("RECORD.md").read_text(encoding="utf-8")
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *args: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "task"})
    companion = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert companion["events"] == evidence.read(repo, "task")["justification"]
    # Runtime fixture lookup cannot establish static identity even for a product repair.
    expected = int(not replace_machinery and kind != "dynamic_fixture")
    assert reply["card"]["repaired"] == counts["repaired"] == expected, (
        f"{kind}: machinery replaced={replace_machinery}, identity unchanged={before == after}; "
        f"card repaired={reply['card']['repaired']}, open={reply['card']['open']}; "
        f"record repaired={counts['repaired']}; readable repaired={'repaired in a recorded review' in readable}; "
        f"certificate incomplete={companion.get('identities_incomplete', False)}; "
        f"product={(repo / 'module.py').read_text().strip()}"
    )


def test_explicit_fixture_control(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    (repo / "test_case.py").write_text(
        "from oracle import observed\ndef test_case(observed):\n    assert observed == 2\n", encoding="utf-8")
    oracle = repo / "oracle.py"
    oracle.write_text("import pytest\n@pytest.fixture\ndef observed():\n    return 3\n", encoding="utf-8")
    before = evidence.capture_sources(repo, ["test_case.py"])
    assert before["test_case.py"]
    oracle.write_text("import pytest\n@pytest.fixture\ndef observed():\n    return 2\n", encoding="utf-8")
    assert evidence.capture_sources(repo, ["test_case.py"]) != before
