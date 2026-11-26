"""Real-disk pytest observations; only G6's execution transport is substituted."""
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
    "class_metaclass": (
        "from oracle import Meta\nclass TestCase(metaclass=Meta):\n"
        "    def test_case(self):\n        assert self.expected == 2\n",
        {
            "oracle.py": "import module\nclass Meta(type):\n"
                         "    def __new__(mcls, name, bases, namespace):\n"
                         "        cls = super().__new__(mcls, name, bases, namespace)\n"
                         "        cls.expected = module.VALUE\n        return cls\n",
        },
        "oracle.py", "class Meta(type):\n"
                     "    def __new__(mcls, name, bases, namespace):\n"
                     "        cls = super().__new__(mcls, name, bases, namespace)\n"
                     "        cls.expected = 2\n        return cls\n",
    ),
    "factory_return": (
        "import pytest\nfrom factory import cases\nfrom module import VALUE\n"
        "@pytest.mark.parametrize('expected', cases(), ids=['case'])\n"
        "def test_case(expected):\n    assert VALUE == expected\n",
        {
            "factory.py": "from oracle import EXPECTED\ndef cases():\n    return EXPECTED\n",
            "oracle.py": "EXPECTED = [2]\n",
        },
        "oracle.py", "EXPECTED = [3]\n",
    ),
    "inherited_usefixtures": (
        "import module\nfrom base import Base\nfrom oracle import observed\n"
        "class TestCase(Base):\n"
        "    def test_case(self):\n        assert module.VALUE == 2\n",
        {
            "base.py": "import pytest\nclass Base:\n"
                       "    pytestmark = pytest.mark.usefixtures('observed')\n",
            "oracle.py": "import pytest\nimport module\n@pytest.fixture\n"
                         "def observed():\n    pass\n",
        },
        "oracle.py", "import pytest\nimport module\n@pytest.fixture\n"
                     "def observed():\n    module.VALUE = 2\n",
    ),
    "reexported_autouse": (
        "import module\nfrom oracle import automatic\n"
        "def test_case():\n    assert module.VALUE == 2\n",
        {
            "fixture_api.py": "from pytest import fixture as register\n",
            "oracle.py": "from fixture_api import register\nimport module\n"
                         "@register(autouse=True)\ndef automatic():\n    pass\n",
        },
        "oracle.py", "from fixture_api import register\nimport module\n"
                     "@register(autouse=True)\ndef automatic():\n    module.VALUE = 2\n",
    ),
}


@pytest.mark.parametrize("kind", CASES)
@pytest.mark.parametrize("replace_machinery", [False, True], ids=["product_control", "oracle_replaced"])
def test_changed_machinery_cannot_receive_repair_credit(tmp_path, monkeypatch, kind, replace_machinery):
    repo = make_repo(tmp_path / "repo")
    source, support, changed, replacement = CASES[kind]
    selection = "test_case.py"
    (repo / selection).write_text(source, encoding="utf-8")
    for name, text in support.items():
        (repo / name).write_text(text, encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    before = evidence.capture_sources(repo, [selection])
    failed = observe(repo, selection, tmp_path / "before")
    assert failed["failed"] == 1
    first = public_review(repo, selection, failed, monkeypatch)
    assert first["outcome"] == "defect_detected"
    finish(repo, first, "0", monkeypatch)
    if replace_machinery:
        (repo / changed).write_text(replacement, encoding="utf-8")
    else:
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [selection])
    assert (repo / "module.py").read_text(encoding="utf-8") == (
        "VALUE = 3\n" if replace_machinery else "VALUE = 2\n")
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
    expected = int(not replace_machinery and bool(before[selection]) and bool(after[selection]))
    assert reply["card"]["repaired"] == counts["repaired"] == expected, (
        f"{kind}: machinery changed={replace_machinery}, nonempty identity={bool(before[selection])}, "
        f"identity unchanged={before == after}; card repaired={reply['card']['repaired']}, "
        f"open={reply['card']['open']}; RECORD repaired={counts['repaired']}, "
        f"claims repair={'1 repaired' in readable}; companion incomplete={companion.get('identities_incomplete', False)}"
    )


@pytest.mark.parametrize("remap", [False, True], ids=["ordinary_control", "remapped_identity"])
def test_unresolved_collector_cannot_fall_back_to_legacy_identity(tmp_path, monkeypatch, remap):
    repo = make_repo(tmp_path / "repo")
    selection = "test_case.py"
    (repo / selection).write_text(
        ("pytest_plugins = ['plugin']\n" if remap else "") +
        "from module import VALUE\ndef test_case():\n    assert VALUE == 2\n", encoding="utf-8")
    if remap:
        (repo / "plugin.py").write_text(
            "from _pytest.nodeid import NodeId\n"
            "def pytest_collection_modifyitems(items):\n"
            "    for item in items:\n"
            "        item._id = NodeId.parse('virtual.py::test_case')\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    sources = evidence.capture_sources(repo, [selection])
    assert bool(sources[selection]) == (not remap)
    failed = observe(repo, selection, tmp_path / "before")
    assert failed["failed"] == 1
    if remap:
        assert set(failed["test_outcomes"]) == {"virtual.py::test_case"}
    first = public_review(repo, selection, failed, monkeypatch)
    finish(repo, first, "0", monkeypatch)
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    passed = observe(repo, selection, tmp_path / "after")
    assert passed["passed"] == 1
    second = public_review(repo, selection, passed, monkeypatch)
    reply = finish(repo, second, "1", monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    readable = lifecycle.export(project).with_name("RECORD.md").read_text(encoding="utf-8")
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *args: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "task"})
    companion = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert companion["events"] == evidence.read(repo, "task")["justification"]
    assert reply["card"]["repaired"] == record_md.counts(rows)["repaired"] == int(not remap), (
        f"capture={sources}; outcomes={first['scope']['checks_run'][0].get('executed_outcomes')}; "
        f"card repaired={reply['card']['repaired']}, open={reply['card']['open']}; "
        f"incomplete sources={[e.data.get('source_incomplete_sha256') for e in rows if e.kind == 'review_finished']}; "
        f"RECORD claims repair={'1 repaired' in readable}; companion incomplete={companion.get('identities_incomplete', False)}"
    )
